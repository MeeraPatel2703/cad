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
from app.agents.review_agent import run_review
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


@router.post("/review")
async def review_drawings(
    master: UploadFile = File(...),
    check: UploadFile = File(...),
):
    """Upload master + check drawings and get a Claude-powered review report.

    Stateless — no session or DB storage needed.
    """
    master_content = await master.read()
    check_content = await check.read()

    master_id, master_path = _save_file(master_content, master.filename)
    check_id, check_path = _save_file(check_content, check.filename)

    try:
        result = await run_review(str(master_path), str(check_path))
    except Exception as e:
        logger.error(f"Review failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))

    result["master_id"] = str(master_id)
    result["check_id"] = str(check_id)
    return result


@router.get("/review/image/{file_id}")
async def get_review_image(file_id: uuid.UUID):
    """Serve an uploaded file by UUID, converting PDFs to PNG."""
    # Find the file on disk by UUID prefix
    upload_dir = settings.upload_path
    matches = list(upload_dir.glob(f"{file_id}.*"))
    if not matches:
        raise HTTPException(status_code=404, detail="File not found")

    file_path = matches[0]
    suffix = file_path.suffix.lower()

    if suffix == ".pdf":
        import fitz

        png_path = file_path.with_suffix(".png")
        if not png_path.exists():
            doc = fitz.open(str(file_path))
            page = doc[0]
            mat = fitz.Matrix(2, 2)
            pix = page.get_pixmap(matrix=mat)
            pix.save(str(png_path))
            doc.close()

        return FileResponse(str(png_path), media_type="image/png")

    media_types = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".tiff": "image/tiff",
        ".tif": "image/tiff",
        ".bmp": "image/bmp",
    }
    media_type = media_types.get(suffix, "application/octet-stream")
    return FileResponse(str(file_path), media_type=media_type)


@router.post("/inspection/session/{session_id}/review")
async def review_session(
    session_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """Run Claude review on an existing session's master + check drawings."""
    result = await db.execute(select(InspectionSession).where(InspectionSession.id == session_id))
    session = result.scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    if not session.check_drawing_id:
        raise HTTPException(status_code=400, detail="No check drawing uploaded yet")

    # Load drawing file paths
    master_row = await db.execute(select(Drawing).where(Drawing.id == session.master_drawing_id))
    master_drawing = master_row.scalar_one()

    check_row = await db.execute(select(Drawing).where(Drawing.id == session.check_drawing_id))
    check_drawing = check_row.scalar_one()

    try:
        review_result = await run_review(master_drawing.file_path, check_drawing.file_path)
    except Exception as e:
        logger.error(f"Session review failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))

    # Store result on session
    session.review_results = review_result
    await db.commit()

    # Notify via WebSocket
    await manager.send_session_event(
        session_id, "review", "complete",
        {"message": review_result.get("summary", "Review complete")},
    )

    return review_result


# ── Demo seed ──


@router.post("/demo/seed", response_model=InspectionSessionOut)
async def seed_demo_session(db: AsyncSession = Depends(get_db)):
    """Create a fully populated demo session with fake data."""

    # Fake drawing records (no real files)
    master_id = uuid.uuid4()
    check_id = uuid.uuid4()
    fake_path = str(settings.upload_path / "demo_placeholder.pdf")

    master_balloons = [
        {"balloon_number": 1, "value": 120.0, "unit": "mm", "coordinates": {"x": 25, "y": 40}, "tolerance_class": None, "nominal": 120.0, "upper_tol": 0.05, "lower_tol": -0.05, "status": "pass"},
        {"balloon_number": 2, "value": 45.0, "unit": "mm", "coordinates": {"x": 42, "y": 28}, "tolerance_class": "H7", "nominal": 45.0, "upper_tol": 0.025, "lower_tol": 0.0, "status": "pass"},
        {"balloon_number": 3, "value": 25.0, "unit": "mm", "coordinates": {"x": 60, "y": 55}, "tolerance_class": "g6", "nominal": 25.0, "upper_tol": -0.007, "lower_tol": -0.020, "status": "fail"},
        {"balloon_number": 4, "value": 80.0, "unit": "mm", "coordinates": {"x": 15, "y": 70}, "tolerance_class": None, "nominal": 80.0, "upper_tol": 0.1, "lower_tol": -0.1, "status": "pass"},
        {"balloon_number": 5, "value": 10.0, "unit": "mm", "coordinates": {"x": 75, "y": 35}, "tolerance_class": None, "nominal": 10.0, "upper_tol": 0.05, "lower_tol": -0.05, "status": "warning"},
        {"balloon_number": 6, "value": 62.5, "unit": "mm", "coordinates": {"x": 35, "y": 60}, "tolerance_class": None, "nominal": 62.5, "upper_tol": 0.02, "lower_tol": -0.02, "status": "pass"},
        {"balloon_number": 7, "value": 8.0, "unit": "mm", "coordinates": {"x": 50, "y": 20}, "tolerance_class": None, "nominal": 8.0, "upper_tol": 0.1, "lower_tol": -0.1, "status": "deviation"},
        {"balloon_number": 8, "value": 30.0, "unit": "mm", "coordinates": {"x": 85, "y": 50}, "tolerance_class": None, "nominal": 30.0, "upper_tol": None, "lower_tol": None, "status": "not_found"},
        {"balloon_number": 9, "value": 150.0, "unit": "mm", "coordinates": {"x": 20, "y": 15}, "tolerance_class": None, "nominal": 150.0, "upper_tol": 0.2, "lower_tol": -0.2, "status": "pass"},
        {"balloon_number": 10, "value": 5.5, "unit": "mm", "coordinates": {"x": 65, "y": 80}, "tolerance_class": None, "nominal": 5.5, "upper_tol": 0.05, "lower_tol": -0.05, "status": "pass"},
        {"balloon_number": 11, "value": 12.0, "unit": "mm", "coordinates": {"x": 45, "y": 75}, "tolerance_class": "H8", "nominal": 12.0, "upper_tol": 0.027, "lower_tol": 0.0, "status": "fail"},
        {"balloon_number": 12, "value": 90.0, "unit": "mm", "coordinates": {"x": 30, "y": 45}, "tolerance_class": None, "nominal": 90.0, "upper_tol": 0.05, "lower_tol": -0.05, "status": "pass"},
    ]

    check_balloons = [
        {**b, "status": b["status"]} for b in master_balloons
    ]

    master_drawing = Drawing(
        id=master_id, filename="GearboxHousing_Rev3_Master.pdf",
        file_path=fake_path, status="ingested",
        balloon_data=master_balloons,
        machine_state={
            "dimensions": [{"value": b["value"], "unit": "mm", "coordinates": b["coordinates"], "tolerance_class": b.get("tolerance_class")} for b in master_balloons],
            "title_block": {"part_name": "Gearbox Housing", "part_number": "GH-2024-003", "revision": "C", "material": "AL 6061-T6", "drawn_by": "R. Mehta", "date": "2024-11-08"},
            "part_list": [
                {"item_number": "1", "description": "Gearbox Housing", "material": "AL 6061-T6", "quantity": 1},
                {"item_number": "2", "description": "Bearing Cap", "material": "Steel 4140", "quantity": 2},
                {"item_number": "3", "description": "Dowel Pin", "material": "Steel 1045", "quantity": 4},
            ],
        },
    )
    check_drawing = Drawing(
        id=check_id, filename="GearboxHousing_SupplierA_Check.pdf",
        file_path=fake_path, status="ingested",
        balloon_data=check_balloons,
    )
    db.add(master_drawing)
    db.add(check_drawing)
    await db.flush()

    # Session
    session = InspectionSession(
        master_drawing_id=master_id,
        check_drawing_id=check_id,
        status="complete",
        summary={"score": 72, "pass": 7, "fail": 2, "warning": 1, "deviation": 1, "not_found": 1},
        comparison_results={
            "findings": [
                {
                    "finding_type": "MISMATCH", "severity": "critical", "category": "consensus",
                    "description": "Bore diameter at balloon #3 (grid C4) reads 24.985mm on check vs 25.000mm nominal on master. Outside g6 tolerance band (-0.007/-0.020). Shaft will not achieve required interference fit.",
                    "nearest_balloon": 3, "grid_ref": "C4", "drawing_role": "check",
                    "recommendation": "Verify bore diameter measurement. If confirmed, reject part — interference fit requires 25.000 g6.",
                    "affected_features": ["25.0 g6 bore"],
                    "evidence": {"expected": "25.000 g6 (-0.007/-0.020)", "found": "24.985 (deviation: -0.015 beyond lower limit)", "standard_reference": "ISO 286-1"},
                },
                {
                    "finding_type": "TOLERANCE_MISSING", "severity": "critical", "category": "omission",
                    "description": "Bearing bore at balloon #11 (grid B6) shows 12.0mm but check drawing is missing H8 tolerance class. Bearing seat requires controlled fit.",
                    "nearest_balloon": 11, "grid_ref": "B6", "drawing_role": "check",
                    "recommendation": "Add H8 tolerance callout (12.000 +0.027/+0.000) to check drawing.",
                    "affected_features": ["12.0 H8 bearing bore"],
                    "evidence": {"expected": "12.000 H8 (+0.027/+0.000)", "found": "12.0 (no tolerance)", "standard_reference": "ASME Y14.5-2018 §5.4"},
                },
                {
                    "finding_type": "OMISSION", "severity": "warning", "category": "omission",
                    "description": "Chamfer dimension at balloon #5 (grid D2) reads 10.04mm on check vs 10.00mm nominal. Within tolerance but near upper limit (+0.04 of +0.05 allowed).",
                    "nearest_balloon": 5, "grid_ref": "D2", "drawing_role": "check",
                    "recommendation": "Accept with note — monitor this dimension in subsequent parts for upward drift.",
                    "affected_features": ["10.0 chamfer"],
                    "evidence": {"expected": "10.000 +0.05/-0.05", "found": "10.040 (80% of tolerance consumed)"},
                },
                {
                    "finding_type": "OMISSION", "severity": "warning", "category": "omission",
                    "description": "Slot width at balloon #8 (grid E3) not found on check drawing. 30.0mm dimension present on master with no tolerance specified.",
                    "nearest_balloon": 8, "grid_ref": "E3", "drawing_role": "check",
                    "recommendation": "Measure slot width on physical part and add to check drawing.",
                    "affected_features": ["30.0 slot width"],
                    "evidence": {"expected": "30.0mm", "found": "missing"},
                },
                {
                    "finding_type": "STACK_UP_ERROR", "severity": "info", "category": "envelope",
                    "description": "Step height at balloon #7 (grid C2) measures 8.08mm vs 8.00mm nominal. Deviation of +0.08mm within +/-0.1 tolerance but check drawing shows different view angle than master.",
                    "nearest_balloon": 7, "grid_ref": "C2", "drawing_role": "check",
                    "recommendation": "Confirm measurement method matches master drawing datum scheme.",
                    "affected_features": ["8.0 step height"],
                    "evidence": {"expected": "8.000 +0.1/-0.1", "found": "8.080 (within tolerance)"},
                },
            ],
            "agent_log": [
                {"agent": "ingestor", "action": "extract", "dims": 12},
                {"agent": "comparator", "action": "match", "matched": 11, "unmatched": 1},
                {"agent": "sherlock", "action": "cross_verification", "findings_count": 5, "checks": ["consensus", "envelope", "omission", "decimal_consistency"]},
            ],
        },
        review_results={
            "missing_dimensions": [
                {"value": "30.0", "type": "linear", "location": "Slot feature, right side of housing", "description": "30.0mm slot width is fully dimensioned on master but entirely absent from check drawing"},
                {"value": "R3", "type": "radius", "location": "Fillet at bearing shoulder", "description": "R3 fillet radius callout on master not present on check"},
            ],
            "missing_tolerances": [
                {"value": "H8", "type": "tolerance", "location": "12.0mm bearing bore", "description": "H8 tolerance class on 12.0mm bearing bore is on master but missing from check"},
                {"value": "+/-0.02", "type": "tolerance", "location": "62.5mm center distance", "description": "Bilateral tolerance on 62.5mm hole center distance missing from check"},
            ],
            "modified_values": [
                {"master_value": "25.0 g6", "check_value": "25.0", "location": "Main shaft bore", "description": "g6 tolerance class dropped from check — interference fit no longer specified"},
            ],
            "summary": "2 dimensions missing, 2 tolerances missing, 1 value modified",
        },
    )
    db.add(session)
    await db.flush()

    # Comparison items (tolerance table)
    items = [
        (1, "Overall length", None, 120.0, 0.05, -0.05, 120.02, 0.02, "pass"),
        (2, "Main bore dia", "H7", 45.0, 0.025, 0.0, 45.012, 0.012, "pass"),
        (3, "Shaft bore dia", "g6", 25.0, -0.007, -0.020, 24.985, -0.015, "fail"),
        (4, "Housing width", None, 80.0, 0.1, -0.1, 80.05, 0.05, "pass"),
        (5, "Chamfer depth", None, 10.0, 0.05, -0.05, 10.04, 0.04, "warning"),
        (6, "Hole center dist", None, 62.5, 0.02, -0.02, 62.51, 0.01, "pass"),
        (7, "Step height", None, 8.0, 0.1, -0.1, 8.08, 0.08, "deviation"),
        (8, "Slot width", None, 30.0, None, None, None, None, "not_found"),
        (9, "Flange length", None, 150.0, 0.2, -0.2, 150.1, 0.1, "pass"),
        (10, "Fillet radius", None, 5.5, 0.05, -0.05, 5.48, -0.02, "pass"),
        (11, "Bearing bore", "H8", 12.0, 0.027, 0.0, 12.032, 0.032, "fail"),
        (12, "Mounting face", None, 90.0, 0.05, -0.05, 89.98, -0.02, "pass"),
    ]
    for bn, desc, tol_cls, nom, ut, lt, actual, dev, st in items:
        db.add(ComparisonItem(
            session_id=session.id, balloon_number=bn,
            feature_description=desc, master_nominal=nom,
            master_upper_tol=ut, master_lower_tol=lt,
            master_tolerance_class=tol_cls,
            check_actual=actual, deviation=dev, status=st,
        ))

    await db.commit()
    await db.refresh(session)
    return session
