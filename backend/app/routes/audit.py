import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.models import Drawing, AuditResult
from app.schemas import DrawingOut, DrawingDetail, AuditResultOut, AuditStatusOut

router = APIRouter()


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
