import logging
import traceback
import uuid
from pathlib import Path

from fastapi import APIRouter, UploadFile, File, BackgroundTasks, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models import Drawing
from app.schemas import UploadResponse
from app.agents.graph import run_audit
from app.services.ws_manager import manager
from app.services.vector_store import store_machine_state

logger = logging.getLogger(__name__)

router = APIRouter()


async def _run_audit_pipeline(drawing_id: str, file_path: str, db_url: str):
    """Background task to run the full audit pipeline."""
    from app.database import async_session
    from app.models import Drawing, AuditResult

    try:
        # Notify start
        uid = uuid.UUID(drawing_id)
        await manager.send_event(uid, "system", "thought", {"message": "Audit pipeline starting..."})

        # Update status
        async with async_session() as session:
            result = await session.execute(select(Drawing).where(Drawing.id == uid))
            drawing = result.scalar_one()
            drawing.status = "auditing"
            await session.commit()

        # Run the graph
        final_state = await run_audit(drawing_id, file_path)

        # Persist results
        async with async_session() as session:
            result = await session.execute(select(Drawing).where(Drawing.id == uid))
            drawing = result.scalar_one()
            drawing.status = final_state.get("status", "complete")
            drawing.integrity_score = final_state.get("integrity_score")
            drawing.machine_state = final_state.get("machine_state")
            drawing.rfi_json = final_state.get("rfi")
            drawing.inspection_sheet = final_state.get("inspection_sheet")

            # Store individual findings
            for finding in final_state.get("findings", []):
                audit_result = AuditResult(
                    drawing_id=uid,
                    agent_name=finding.get("source_agent", "unknown"),
                    result_type=finding.get("finding_type", "UNKNOWN"),
                    severity=finding.get("severity", "info"),
                    details={"description": finding.get("description", ""), "evidence": finding.get("evidence", {})},
                    coordinates=finding.get("coordinates"),
                )
                session.add(audit_result)

            await session.commit()

        # Store in vector DB
        if final_state.get("machine_state"):
            store_machine_state(uid, final_state["machine_state"])

    except Exception as e:
        logger.error(f"Audit pipeline failed for {drawing_id}: {e}")
        logger.error(traceback.format_exc())
        uid = uuid.UUID(drawing_id)
        await manager.send_event(uid, "system", "error", {"message": str(e)})
        async with async_session() as session:
            result = await session.execute(select(Drawing).where(Drawing.id == uid))
            drawing = result.scalar_one_or_none()
            if drawing:
                drawing.status = "error"
                await session.commit()


@router.post("/upload", response_model=UploadResponse)
async def upload_drawing(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
):
    # Save file
    file_id = uuid.uuid4()
    ext = Path(file.filename).suffix
    save_name = f"{file_id}{ext}"
    save_path = settings.upload_path / save_name

    content = await file.read()
    save_path.write_bytes(content)

    # Create DB record
    drawing = Drawing(
        id=file_id,
        filename=file.filename,
        file_path=str(save_path),
        status="uploaded",
    )
    db.add(drawing)
    await db.flush()

    # Launch audit
    background_tasks.add_task(
        _run_audit_pipeline,
        str(file_id),
        str(save_path),
        settings.DATABASE_URL,
    )

    return UploadResponse(drawing_id=file_id, status="uploaded", filename=file.filename)
