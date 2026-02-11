"""Inspection session routes – master/check drawing comparison workflow."""
from __future__ import annotations

import asyncio
import logging
import traceback
import uuid
from pathlib import Path
from typing import Tuple

from fastapi import APIRouter, UploadFile, File, BackgroundTasks, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings
from app.database import get_db, async_session
from app.models import Drawing, InspectionSession, ComparisonItem
from app.schemas import (
    InspectionSessionOut,
    InspectionSessionDetail,
    ComparisonItemOut,
    DrawingBalloons,
    BalloonData,
    DrawingOut,
    UploadResponse,
)
from app.agents.comparison_graph import run_comparison
from app.agents.ingestor import run_ingestor
from app.agents.state import AuditState
from app.services.ws_manager import manager
from app.services.vector_store import store_machine_state

router = APIRouter()


logger = logging.getLogger(__name__)


def _save_file(file_content: bytes, filename: str) -> Tuple[uuid.UUID, Path]:
    """Save uploaded file to disk. Returns (file_id, path)."""
    file_id = uuid.uuid4()
    ext = Path(filename).suffix
    save_name = f"{file_id}{ext}"
    save_path = settings.upload_path / save_name
    save_path.write_bytes(file_content)
    return file_id, save_path


async def _ingest_master(drawing_id: str, file_path: str, session_id: str):
    """Background task: ingest master drawing and store machine_state."""
    uid = uuid.UUID(drawing_id)
    sid = uuid.UUID(session_id)

    try:
        await manager.send_session_event(sid, "system", "thought", {"message": "Master ingestion starting..."})

        audit_state: AuditState = {
            "drawing_id": drawing_id,
            "file_path": file_path,
            "machine_state": None,
            "findings": [],
            "agent_log": [],
            "reflexion_count": 0,
            "status": "started",
            "crop_region": None,
            "rfi": None,
            "inspection_sheet": None,
            "integrity_score": None,
        }

        result = await run_ingestor(audit_state)
        ms = result.get("machine_state", {})

        # Persist machine_state and balloon_data on the drawing
        async with async_session() as db:
            row = await db.execute(select(Drawing).where(Drawing.id == uid))
            drawing = row.scalar_one()
            drawing.machine_state = ms
            drawing.status = "ingested"

            # Generate balloon data for all dimensions
            balloons = []
            for i, dim in enumerate(ms.get("dimensions", [])):
                coords = dim.get("coordinates", {})
                if coords:
                    balloons.append({
                        "balloon_number": i + 1,
                        "value": dim.get("value", 0),
                        "unit": dim.get("unit", "mm"),
                        "coordinates": coords,
                        "tolerance_class": dim.get("tolerance_class"),
                        "nominal": dim.get("nominal") or dim.get("value"),
                        "upper_tol": dim.get("upper_tol"),
                        "lower_tol": dim.get("lower_tol"),
                        "status": "pending",
                    })
            drawing.balloon_data = balloons
            await db.commit()

        # Store in vector DB
        if ms:
            store_machine_state(uid, ms)

        dims = len(ms.get("dimensions", []))
        await manager.send_session_event(
            sid, "ingestor", "thought",
            {"message": f"Master ingested: {dims} dimensions extracted. Ready for check drawing."},
        )

    except Exception as e:
        logger.error(f"Master ingestion failed: {e}")
        logger.error(traceback.format_exc())
        await manager.send_session_event(sid, "system", "error", {"message": str(e)})


async def _run_comparison_pipeline(session_id: str, master_drawing_id: str, check_drawing_id: str):
    """Background task: run full comparison pipeline."""
    sid = uuid.UUID(session_id)

    try:
        await manager.send_session_event(sid, "system", "thought", {"message": "Comparison pipeline starting..."})

        # Update session status
        async with async_session() as db:
            row = await db.execute(select(InspectionSession).where(InspectionSession.id == sid))
            session = row.scalar_one()
            session.status = "comparing"
            await db.commit()

        # Load master drawing data
        async with async_session() as db:
            row = await db.execute(select(Drawing).where(Drawing.id == uuid.UUID(master_drawing_id)))
            master = row.scalar_one()
            master_file = master.file_path
            master_ms = master.machine_state

            row = await db.execute(select(Drawing).where(Drawing.id == uuid.UUID(check_drawing_id)))
            check = row.scalar_one()
            check_file = check.file_path

        # Run the comparison graph
        final_state = await run_comparison(
            session_id=session_id,
            master_file=master_file,
            check_file=check_file,
            master_drawing_id=master_drawing_id,
            check_drawing_id=check_drawing_id,
            master_machine_state=master_ms,
        )

        # Persist results
        async with async_session() as db:
            # Update session
            row = await db.execute(select(InspectionSession).where(InspectionSession.id == sid))
            session = row.scalar_one()
            session.status = final_state.get("status", "complete")
            session.summary = final_state.get("summary")
            session.comparison_results = {
                "rfi": final_state.get("rfi"),
                "agent_log": final_state.get("agent_log"),
                "findings": final_state.get("findings"),
            }

            # Clear any existing comparison items first to prevent duplicates
            await db.execute(
                ComparisonItem.__table__.delete().where(ComparisonItem.session_id == sid)
            )

            # Store comparison items
            for item in final_state.get("comparison_items", []):
                ci = ComparisonItem(
                    session_id=sid,
                    balloon_number=item["balloon_number"],
                    feature_description=item.get("feature_description", ""),
                    zone=item.get("zone"),
                    master_nominal=item.get("master_nominal"),
                    master_upper_tol=item.get("master_upper_tol"),
                    master_lower_tol=item.get("master_lower_tol"),
                    master_unit=item.get("master_unit", "mm"),
                    master_tolerance_class=item.get("master_tolerance_class"),
                    check_actual=item.get("check_actual"),
                    deviation=item.get("deviation"),
                    status=item.get("status", "pending"),
                    master_coordinates=item.get("master_coordinates"),
                    check_coordinates=item.get("check_coordinates"),
                    notes=item.get("notes"),
                )
                db.add(ci)

            # Update balloon data on both drawings
            row = await db.execute(select(Drawing).where(Drawing.id == uuid.UUID(master_drawing_id)))
            master_drawing = row.scalar_one()
            master_drawing.balloon_data = final_state.get("master_balloon_data")
            master_drawing.machine_state = final_state.get("master_machine_state") or master_drawing.machine_state

            row = await db.execute(select(Drawing).where(Drawing.id == uuid.UUID(check_drawing_id)))
            check_drawing = row.scalar_one()
            check_drawing.balloon_data = final_state.get("check_balloon_data")
            check_drawing.machine_state = final_state.get("check_machine_state")

            await db.commit()

    except Exception as e:
        logger.error(f"Comparison pipeline failed for session {session_id}: {e}")
        logger.error(traceback.format_exc())
        await manager.send_session_event(sid, "system", "error", {"message": str(e)})
        async with async_session() as db:
            row = await db.execute(select(InspectionSession).where(InspectionSession.id == sid))
            session = row.scalar_one_or_none()
            if session:
                session.status = "error"
                await db.commit()


# ── Endpoints ──


@router.post("/inspection/session", response_model=InspectionSessionOut)
async def create_inspection_session(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
):
    """Upload master drawing and create an inspection session."""
    content = await file.read()
    file_id, save_path = _save_file(content, file.filename)

    # Create drawing record
    drawing = Drawing(
        id=file_id,
        filename=file.filename,
        file_path=str(save_path),
        status="uploaded",
    )
    db.add(drawing)
    await db.flush()

    # Create inspection session
    session = InspectionSession(
        master_drawing_id=file_id,
        status="awaiting_check",
    )
    db.add(session)
    await db.flush()

    # Start background master ingestion
    background_tasks.add_task(
        _ingest_master,
        str(file_id),
        str(save_path),
        str(session.id),
    )

    return session


@router.post("/inspection/session/{session_id}/check", response_model=InspectionSessionOut)
async def upload_check_drawing(
    session_id: uuid.UUID,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
):
    """Upload check drawing and trigger comparison pipeline."""
    result = await db.execute(select(InspectionSession).where(InspectionSession.id == session_id))
    session = result.scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    content = await file.read()
    file_id, save_path = _save_file(content, file.filename)

    # Create drawing record for check
    drawing = Drawing(
        id=file_id,
        filename=file.filename,
        file_path=str(save_path),
        status="uploaded",
    )
    db.add(drawing)
    await db.flush()

    # Update session
    session.check_drawing_id = file_id
    session.status = "ingesting"
    await db.flush()

    # Launch comparison pipeline
    background_tasks.add_task(
        _run_comparison_pipeline,
        str(session_id),
        str(session.master_drawing_id),
        str(file_id),
    )

    return session


@router.get("/inspection/sessions", response_model=list[InspectionSessionOut])
async def list_sessions(db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(InspectionSession).order_by(InspectionSession.created_at.desc())
    )
    return result.scalars().all()


@router.get("/inspection/session/{session_id}", response_model=InspectionSessionDetail)
async def get_session(session_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(InspectionSession)
        .options(
            selectinload(InspectionSession.master_drawing),
            selectinload(InspectionSession.check_drawing),
        )
        .where(InspectionSession.id == session_id)
    )
    session = result.scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    return session


@router.get("/inspection/session/{session_id}/comparison", response_model=list[ComparisonItemOut])
async def get_comparison(session_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(ComparisonItem)
        .where(ComparisonItem.session_id == session_id)
        .order_by(ComparisonItem.balloon_number)
    )
    return result.scalars().all()


@router.get("/inspection/session/{session_id}/balloons/{role}", response_model=DrawingBalloons)
async def get_balloons(
    session_id: uuid.UUID,
    role: str,
    db: AsyncSession = Depends(get_db),
):
    """Get balloon overlay data for master or check drawing."""
    if role not in ("master", "check"):
        raise HTTPException(status_code=400, detail="Role must be 'master' or 'check'")

    result = await db.execute(select(InspectionSession).where(InspectionSession.id == session_id))
    session = result.scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    drawing_id = session.master_drawing_id if role == "master" else session.check_drawing_id
    if not drawing_id:
        return DrawingBalloons(drawing_id=session.master_drawing_id, balloons=[])

    result = await db.execute(select(Drawing).where(Drawing.id == drawing_id))
    drawing = result.scalar_one_or_none()
    if not drawing or not drawing.balloon_data:
        return DrawingBalloons(drawing_id=drawing_id, balloons=[])

    balloons = [BalloonData(**b) for b in drawing.balloon_data]
    return DrawingBalloons(drawing_id=drawing_id, balloons=balloons)


@router.post("/inspection/session/{session_id}/rerun")
async def rerun_comparison(
    session_id: uuid.UUID,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    """Re-run the comparison pipeline for a session."""
    result = await db.execute(select(InspectionSession).where(InspectionSession.id == session_id))
    session = result.scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    if not session.check_drawing_id:
        raise HTTPException(status_code=400, detail="No check drawing uploaded yet")

    # Clear previous comparison items
    await db.execute(
        ComparisonItem.__table__.delete().where(ComparisonItem.session_id == session_id)
    )

    # Reset session status
    session.status = "comparing"
    session.summary = None
    session.comparison_results = None
    await db.commit()

    # Re-run comparison pipeline
    background_tasks.add_task(
        _run_comparison_pipeline,
        str(session_id),
        str(session.master_drawing_id),
        str(session.check_drawing_id),
    )

    return {"status": "started", "session_id": str(session_id)}


@router.delete("/inspection/session/{session_id}")
async def delete_inspection_session(
    session_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """Delete an inspection session and its associated data."""
    result = await db.execute(select(InspectionSession).where(InspectionSession.id == session_id))
    session = result.scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    # Delete comparison items first (foreign key constraint)
    await db.execute(
        ComparisonItem.__table__.delete().where(ComparisonItem.session_id == session_id)
    )

    # Optionally delete associated drawings and files
    drawing_ids = []
    if session.master_drawing_id:
        drawing_ids.append(session.master_drawing_id)
    if session.check_drawing_id:
        drawing_ids.append(session.check_drawing_id)

    for drawing_id in drawing_ids:
        result = await db.execute(select(Drawing).where(Drawing.id == drawing_id))
        drawing = result.scalar_one_or_none()
        if drawing:
            # Delete files from disk
            file_path = Path(drawing.file_path)
            if file_path.exists():
                file_path.unlink()
            # Also delete cached PNG if exists
            png_path = file_path.with_suffix(".png")
            if png_path.exists():
                png_path.unlink()
            # Delete drawing record
            await db.delete(drawing)

    # Delete the session
    await db.delete(session)
    await db.commit()

    logger.info(f"Deleted inspection session {session_id}")
    return {"status": "deleted", "session_id": str(session_id)}


@router.get("/inspection/session/{session_id}/image/{role}")
async def get_session_image(
    session_id: uuid.UUID,
    role: str,
    db: AsyncSession = Depends(get_db),
):
    """Serve the image file for master or check drawing. Converts PDFs to PNG."""
    if role not in ("master", "check"):
        raise HTTPException(status_code=400, detail="Role must be 'master' or 'check'")

    result = await db.execute(select(InspectionSession).where(InspectionSession.id == session_id))
    session = result.scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    drawing_id = session.master_drawing_id if role == "master" else session.check_drawing_id
    if not drawing_id:
        raise HTTPException(status_code=404, detail="No check drawing uploaded yet")

    result = await db.execute(select(Drawing).where(Drawing.id == drawing_id))
    drawing = result.scalar_one_or_none()
    if not drawing:
        raise HTTPException(status_code=404, detail="Drawing not found")

    file_path = Path(drawing.file_path)
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="File not found on disk")

    suffix = file_path.suffix.lower()

    # Convert PDF to PNG for browser display
    if suffix == ".pdf":
        import fitz  # PyMuPDF
        from fastapi.responses import Response

        # Check for cached PNG
        png_path = file_path.with_suffix(".png")
        if not png_path.exists():
            doc = fitz.open(str(file_path))
            page = doc[0]  # First page
            # Render at 2x resolution for clarity
            mat = fitz.Matrix(2, 2)
            pix = page.get_pixmap(matrix=mat)
            pix.save(str(png_path))
            doc.close()

        return FileResponse(str(png_path), media_type="image/png", filename=f"{drawing.filename}.png")

    # Determine media type for other formats
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
