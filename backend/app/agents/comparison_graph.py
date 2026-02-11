"""LangGraph StateGraph wiring – master/check comparison pipeline."""
from __future__ import annotations

import asyncio
import uuid
from typing import Optional, Dict

from langgraph.graph import StateGraph, END

from app.agents.state import ComparisonState, AuditState
from app.agents.ingestor import run_ingestor
from app.config import settings
from app.agents.comparator import run_comparator
from app.agents.comparison_reporter import run_comparison_reporter
from app.agents.sherlock import run_sherlock
from app.services.ws_manager import manager


def _has_valid_extraction(machine_state: dict) -> bool:
    """Check if machine_state has actual extracted data (not just empty structure)."""
    if not machine_state:
        return False
    dims = machine_state.get("dimensions", [])
    parts = machine_state.get("part_list", [])
    return len(dims) > 0 or len(parts) > 0


async def master_ingestor_node(state: ComparisonState) -> ComparisonState:
    session_id = state.get("session_id", "")

    # Skip if master already has valid extracted data
    master_ms = state.get("master_machine_state")
    if _has_valid_extraction(master_ms):
        await manager.send_session_event(
            uuid.UUID(session_id), "ingestor", "thought",
            {"message": f"Master drawing already ingested ({len(master_ms.get('dimensions', []))} dims), reusing cached data."},
        )
        return state

    await manager.send_session_event(
        uuid.UUID(session_id), "ingestor", "thought",
        {"message": "Analyzing master drawing with Gemini Vision..."},
    )

    # Build a temporary AuditState for the ingestor
    audit_state: AuditState = {
        "drawing_id": state.get("master_drawing_id", ""),
        "file_path": state["master_file_path"],
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

    if settings.USE_PADDLE_OCR:
        from app.agents.ingestor_paddle import run_ingestor_paddle
        result = await run_ingestor_paddle(audit_state)
    else:
        result = await run_ingestor(audit_state)
    ms = result.get("machine_state", {})

    await manager.send_session_event(
        uuid.UUID(session_id), "ingestor", "thought",
        {
            "message": f"Master: extracted {len(ms.get('dimensions', []))} dimensions, "
                       f"{len(ms.get('part_list', []))} parts, "
                       f"{len(ms.get('zones', []))} zones",
        },
    )

    agent_log = list(state.get("agent_log", []))
    agent_log.append({
        "agent": "ingestor",
        "role": "master",
        "action": "extraction",
        "dimensions": len(ms.get("dimensions", [])),
    })

    return {
        **state,
        "master_machine_state": ms,
        "agent_log": agent_log,
    }


async def check_ingestor_node(state: ComparisonState) -> ComparisonState:
    import logging
    import os
    logger = logging.getLogger(__name__)

    from sqlalchemy import select
    from app.database import async_session
    from app.models import Drawing

    session_id = state.get("session_id", "")
    check_file_path = state.get("check_file_path", "")

    # Validate file path
    if not check_file_path:
        logger.error("Check ingestor: check_file_path is empty!")
        await manager.send_session_event(
            uuid.UUID(session_id), "ingestor", "error",
            {"message": "Check file path is missing"},
        )
        return {**state, "check_machine_state": {}, "agent_log": state.get("agent_log", [])}

    if not os.path.exists(check_file_path):
        logger.error(f"Check ingestor: file does not exist: {check_file_path}")
        await manager.send_session_event(
            uuid.UUID(session_id), "ingestor", "error",
            {"message": f"Check file not found: {check_file_path}"},
        )
        return {**state, "check_machine_state": {}, "agent_log": state.get("agent_log", [])}

    logger.info(f"Check ingestor: file validated, size={os.path.getsize(check_file_path)} bytes")

    await manager.send_session_event(
        uuid.UUID(session_id), "ingestor", "thought",
        {"message": "Analyzing check drawing with Gemini Vision..."},
    )

    audit_state: AuditState = {
        "drawing_id": state.get("check_drawing_id", ""),
        "file_path": check_file_path,
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

    logger.info(f"Check ingestor: starting extraction for {check_file_path}")
    ms = {}
    try:
        if settings.USE_PADDLE_OCR:
            from app.agents.ingestor_paddle import run_ingestor_paddle
            result = await run_ingestor_paddle(audit_state)
        else:
            result = await run_ingestor(audit_state)
        ms = result.get("machine_state", {})
        dims_count = len(ms.get("dimensions", [])) if ms else 0
        logger.info(f"Check ingestor: extraction complete, {dims_count} dimensions")

        if dims_count == 0:
            logger.warning("Check ingestor: 0 dimensions extracted - may indicate extraction issue")
            # Log what we got back
            logger.info(f"Check ingestor: machine_state keys = {list(ms.keys()) if ms else 'None'}")

    except Exception as e:
        import traceback
        error_msg = str(e)
        error_type = type(e).__name__
        logger.error(f"Check ingestor failed with {error_type}: {error_msg}")
        logger.error(traceback.format_exc())
        await manager.send_session_event(
            uuid.UUID(session_id), "ingestor", "error",
            {"message": f"Check extraction failed: {error_type}: {error_msg[:100]}"},
        )
        ms = {}

    # Persist initial "pending" balloons for check drawing so they show during comparison
    check_drawing_id = state.get("check_drawing_id", "")
    if ms and ms.get("dimensions") and check_drawing_id:
        try:
            async with async_session() as db:
                row = await db.execute(
                    select(Drawing).where(Drawing.id == uuid.UUID(check_drawing_id))
                )
                drawing = row.scalar_one_or_none()
                if drawing:
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
                    drawing.machine_state = ms
                    await db.commit()
                    logger.info(f"Check ingestor: saved {len(balloons)} initial balloons")
        except Exception as e:
            logger.warning(f"Check ingestor: failed to save initial balloons: {e}")

    await manager.send_session_event(
        uuid.UUID(session_id), "ingestor", "thought",
        {
            "message": f"Check: extracted {len(ms.get('dimensions', []))} dimensions, "
                       f"{len(ms.get('part_list', []))} parts, "
                       f"{len(ms.get('zones', []))} zones",
        },
    )

    agent_log = list(state.get("agent_log", []))
    agent_log.append({
        "agent": "ingestor",
        "role": "check",
        "action": "extraction",
        "dimensions": len(ms.get("dimensions", [])),
    })

    return {
        **state,
        "check_machine_state": ms,
        "agent_log": agent_log,
    }


async def comparator_node(state: ComparisonState) -> ComparisonState:
    session_id = state.get("session_id", "")

    await manager.send_session_event(
        uuid.UUID(session_id), "comparator", "thought",
        {"message": "Matching and comparing dimensions between master and check..."},
    )

    result = await run_comparator(state)
    summary = result.get("summary", {})

    await manager.send_session_event(
        uuid.UUID(session_id), "comparator", "thought",
        {
            "message": f"Compared {summary.get('total_dimensions', 0)} dimensions: "
                       f"{summary.get('pass', 0)} pass, "
                       f"{summary.get('fail', 0)} fail, "
                       f"{summary.get('warning', 0)} warning, "
                       f"{summary.get('not_found', 0)} not found",
        },
    )

    return result


async def sherlock_node(state: ComparisonState) -> ComparisonState:
    """Run Sherlock cross-verification on both master and check drawings."""
    session_id = state.get("session_id", "")

    await manager.send_session_event(
        uuid.UUID(session_id), "sherlock", "thought",
        {"message": "Running Sherlock cross-verification on both drawings..."},
    )

    master_ms = state.get("master_machine_state") or {}
    check_ms = state.get("check_machine_state") or {}

    # Enrich machine states with balloon data so Sherlock can reference balloon numbers
    master_balloon_data = state.get("master_balloon_data") or []
    check_balloon_data = state.get("check_balloon_data") or []
    if master_balloon_data:
        master_ms = {**master_ms, "balloon_data": master_balloon_data}
    if check_balloon_data:
        check_ms = {**check_ms, "balloon_data": check_balloon_data}

    # Build temporary AuditStates for Sherlock
    master_audit: AuditState = {
        "drawing_id": state.get("master_drawing_id", ""),
        "file_path": state.get("master_file_path", ""),
        "machine_state": master_ms,
        "findings": [],
        "agent_log": [],
        "reflexion_count": 0,
        "status": "started",
        "crop_region": None,
        "rfi": None,
        "inspection_sheet": None,
        "integrity_score": None,
    }
    check_audit: AuditState = {
        "drawing_id": state.get("check_drawing_id", ""),
        "file_path": state.get("check_file_path", ""),
        "machine_state": check_ms,
        "findings": [],
        "agent_log": [],
        "reflexion_count": 0,
        "status": "started",
        "crop_region": None,
        "rfi": None,
        "inspection_sheet": None,
        "integrity_score": None,
    }

    # Run Sherlock on both drawings in parallel
    master_result, check_result = await asyncio.gather(
        run_sherlock(master_audit),
        run_sherlock(check_audit),
    )

    # Tag findings with their drawing role for display
    master_findings = master_result.get("findings", [])
    for f in master_findings:
        f["drawing_role"] = "master"

    check_findings = check_result.get("findings", [])
    for f in check_findings:
        f["drawing_role"] = "check"

    all_findings = list(state.get("findings", [])) + master_findings + check_findings

    master_count = len(master_findings)
    check_count = len(check_findings)

    await manager.send_session_event(
        uuid.UUID(session_id), "sherlock", "thought",
        {
            "message": f"Sherlock complete: {master_count} findings on master, "
                       f"{check_count} findings on check drawing",
        },
    )

    agent_log = list(state.get("agent_log", []))
    agent_log.append({
        "agent": "sherlock",
        "action": "cross_verification",
        "master_findings": master_count,
        "check_findings": check_count,
    })

    return {
        **state,
        "findings": all_findings,
        "agent_log": agent_log,
    }


async def reporter_node(state: ComparisonState) -> ComparisonState:
    session_id = state.get("session_id", "")

    await manager.send_session_event(
        uuid.UUID(session_id), "reporter", "thought",
        {"message": "Generating inspection RFI report..."},
    )

    result = await run_comparison_reporter(state)
    summary = result.get("summary", {})

    await manager.send_session_event(
        uuid.UUID(session_id), "reporter", "complete",
        {
            "message": "Inspection complete",
            "score": summary.get("score"),
            "total_dimensions": summary.get("total_dimensions"),
            "pass": summary.get("pass"),
            "fail": summary.get("fail"),
        },
    )

    return result


def build_comparison_graph() -> StateGraph:
    graph = StateGraph(ComparisonState)

    graph.add_node("master_ingestor", master_ingestor_node)
    graph.add_node("check_ingestor", check_ingestor_node)
    graph.add_node("comparator", comparator_node)
    graph.add_node("sherlock", sherlock_node)
    graph.add_node("reporter", reporter_node)

    graph.set_entry_point("master_ingestor")
    graph.add_edge("master_ingestor", "check_ingestor")
    graph.add_edge("check_ingestor", "comparator")
    graph.add_edge("comparator", "sherlock")
    graph.add_edge("sherlock", "reporter")
    graph.add_edge("reporter", END)

    return graph.compile()


comparison_graph = build_comparison_graph()


async def run_comparison(
    session_id: str,
    master_file: str,
    check_file: str,
    master_drawing_id: str,
    check_drawing_id: str,
    master_machine_state: Optional[Dict] = None,
) -> ComparisonState:
    """Run the full comparison pipeline."""
    initial_state: ComparisonState = {
        "session_id": session_id,
        "master_drawing_id": master_drawing_id,
        "master_file_path": master_file,
        "check_drawing_id": check_drawing_id,
        "check_file_path": check_file,
        "master_machine_state": master_machine_state,
        "check_machine_state": None,
        "comparison_items": [],
        "findings": [],
        "agent_log": [],
        "status": "started",
        "master_balloon_data": [],
        "check_balloon_data": [],
        "summary": None,
        "rfi": None,
    }

    result = await comparison_graph.ainvoke(initial_state)
    return result
