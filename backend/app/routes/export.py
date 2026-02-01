import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import Drawing

router = APIRouter()


@router.get("/export/rfi/{drawing_id}")
async def export_rfi(drawing_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Drawing).where(Drawing.id == drawing_id))
    drawing = result.scalar_one_or_none()
    if not drawing:
        raise HTTPException(status_code=404, detail="Drawing not found")
    if not drawing.rfi_json:
        raise HTTPException(status_code=404, detail="RFI not yet generated")
    return drawing.rfi_json


@router.get("/export/inspection/{drawing_id}")
async def export_inspection(drawing_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Drawing).where(Drawing.id == drawing_id))
    drawing = result.scalar_one_or_none()
    if not drawing:
        raise HTTPException(status_code=404, detail="Drawing not found")
    if not drawing.inspection_sheet:
        raise HTTPException(status_code=404, detail="Inspection sheet not yet generated")
    return drawing.inspection_sheet
