import logging
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.models import Drawing, AuditResult
from app.schemas import DrawingOut, DrawingDetail, AuditResultOut, AuditStatusOut

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("/drawings", response_model=list[DrawingOut])
async def list_drawings(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Drawing).order_by(Drawing.upload_date.desc()))
    drawings = result.scalars().all()
    return drawings


@router.get("/drawings/{drawing_id}", response_model=DrawingDetail)
async def get_drawing(drawing_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Drawing).where(Drawing.id == drawing_id))
    drawing = result.scalar_one_or_none()
    if not drawing:
        raise HTTPException(status_code=404, detail="Drawing not found")
    return drawing


@router.get("/drawings/{drawing_id}/image")
async def get_drawing_image(drawing_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    """Serve the drawing image file. Converts PDFs to PNG for browser display."""
    result = await db.execute(select(Drawing).where(Drawing.id == drawing_id))
    drawing = result.scalar_one_or_none()
    if not drawing:
        raise HTTPException(status_code=404, detail="Drawing not found")

    file_path = Path(drawing.file_path)
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="File not found on disk")

    suffix = file_path.suffix.lower()

    if suffix == ".pdf":
        import fitz  # PyMuPDF

        png_path = file_path.with_suffix(".png")
        if not png_path.exists():
            doc = fitz.open(str(file_path))
            page = doc[0]
            mat = fitz.Matrix(2, 2)
            pix = page.get_pixmap(matrix=mat)
            pix.save(str(png_path))
            doc.close()

        return FileResponse(str(png_path), media_type="image/png", filename=f"{drawing.filename}.png")

    media_types = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".tiff": "image/tiff",
        ".tif": "image/tiff",
        ".bmp": "image/bmp",
    }
    media_type = media_types.get(suffix, "application/octet-stream")

    return FileResponse(str(file_path), media_type=media_type, filename=drawing.filename)


@router.get("/audit/{drawing_id}/status", response_model=AuditStatusOut)
async def audit_status(drawing_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Drawing).options(selectinload(Drawing.audit_results)).where(Drawing.id == drawing_id)
    )
    drawing = result.scalar_one_or_none()
    if not drawing:
        raise HTTPException(status_code=404, detail="Drawing not found")

    return AuditStatusOut(
        drawing_id=drawing.id,
        status=drawing.status,
        integrity_score=drawing.integrity_score,
        findings=[AuditResultOut.model_validate(r) for r in drawing.audit_results],
    )


@router.get("/audit/{drawing_id}/findings", response_model=list[AuditResultOut])
async def audit_findings(drawing_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(AuditResult)
        .where(AuditResult.drawing_id == drawing_id)
        .order_by(AuditResult.created_at)
    )
    findings = result.scalars().all()
    return findings


@router.delete("/drawings/{drawing_id}")
async def delete_drawing(drawing_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    """Delete a drawing and its associated data."""
    result = await db.execute(select(Drawing).where(Drawing.id == drawing_id))
    drawing = result.scalar_one_or_none()
    if not drawing:
        raise HTTPException(status_code=404, detail="Drawing not found")

    # Delete audit results first (foreign key constraint)
    await db.execute(
        AuditResult.__table__.delete().where(AuditResult.drawing_id == drawing_id)
    )

    # Delete files from disk
    if drawing.file_path:
        file_path = Path(drawing.file_path)
        if file_path.exists():
            file_path.unlink()
        # Also delete cached PNG if exists
        png_path = file_path.with_suffix(".png")
        if png_path.exists():
            png_path.unlink()

    # Delete the drawing record
    await db.delete(drawing)
    await db.commit()

    logger.info(f"Deleted drawing {drawing_id}")
    return {"status": "deleted", "drawing_id": str(drawing_id)}
