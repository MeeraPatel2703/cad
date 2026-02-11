"""LangGraph StateGraph wiring – full audit pipeline."""
from __future__ import annotations

import logging
import uuid

from langgraph.graph import StateGraph, END

logger = logging.getLogger(__name__)

from app.agents.state import AuditState
from app.agents.ingestor import run_ingestor
from app.config import settings
from app.agents.sherlock import run_sherlock
from app.agents.physicist import run_physicist
from app.agents.reporter import run_reporter
from app.agents.reflexion import check_reflexion, prepare_rescan
from app.services.ws_manager import manager


async def ingestor_node(state: AuditState) -> AuditState:
    drawing_id = state.get("drawing_id", "")
    logger.info("=== PIPELINE: Starting INGESTOR for %s ===", drawing_id)
    await manager.send_event(
        uuid.UUID(drawing_id), "ingestor", "thought",
        {"message": "Analyzing drawing with Gemini Vision..."},
    )
    if settings.USE_PADDLE_OCR:
        from app.agents.ingestor_paddle import run_ingestor_paddle
        result = await run_ingestor_paddle(state)
    else:
        result = await run_ingestor(state)
    logger.info("=== PIPELINE: INGESTOR complete for %s ===", drawing_id)
    ms = result.get("machine_state", {})
    await manager.send_event(
        uuid.UUID(drawing_id), "ingestor", "thought",
        {
            "message": f"Extracted {len(ms.get('dimensions', []))} dimensions, "
                       f"{len(ms.get('part_list', []))} parts, "
                       f"{len(ms.get('zones', []))} zones",
        },
    )
    return result


async def sherlock_node(state: AuditState) -> AuditState:
    drawing_id = state.get("drawing_id", "")
    logger.info("=== PIPELINE: Starting SHERLOCK for %s ===", drawing_id)
    await manager.send_event(
        uuid.UUID(drawing_id), "sherlock", "thought",
        {"message": "Running cross-verification checks..."},
    )
    result = await run_sherlock(state)
    logger.info("=== PIPELINE: SHERLOCK complete for %s ===", drawing_id)
    new_findings = len(result.get("findings", [])) - len(state.get("findings", []))
    await manager.send_event(
        uuid.UUID(drawing_id), "sherlock", "thought",
        {"message": f"Found {new_findings} issues during verification"},
    )
    # Emit individual findings
    for f in result.get("findings", [])[-new_findings:] if new_findings > 0 else []:
        await manager.send_event(
            uuid.UUID(drawing_id), "sherlock", "finding", f,
        )
    return result


async def physicist_node(state: AuditState) -> AuditState:
    drawing_id = state.get("drawing_id", "")
    logger.info("=== PIPELINE: Starting PHYSICIST for %s ===", drawing_id)
    await manager.send_event(
        uuid.UUID(drawing_id), "physicist", "thought",
        {"message": "Running physics and tolerance analysis..."},
    )
    result = await run_physicist(state)
    logger.info("=== PIPELINE: PHYSICIST complete for %s ===", drawing_id)
    new_findings = len(result.get("findings", [])) - len(state.get("findings", []))
    await manager.send_event(
        uuid.UUID(drawing_id), "physicist", "thought",
        {"message": f"Physics check complete. {new_findings} new findings."},
    )
    for f in result.get("findings", [])[-new_findings:] if new_findings > 0 else []:
        await manager.send_event(
            uuid.UUID(drawing_id), "physicist", "finding", f,
        )
    return result


def reflexion_router(state: AuditState) -> str:
    return check_reflexion(state)


async def reflexion_node(state: AuditState) -> AuditState:
    drawing_id = state.get("drawing_id", "")
    await manager.send_event(
        uuid.UUID(drawing_id), "reflexion", "thought",
        {"message": f"Re-scanning suspect region (attempt {state.get('reflexion_count', 0) + 1})..."},
    )
    return await prepare_rescan(state)


async def reporter_node(state: AuditState) -> AuditState:
    drawing_id = state.get("drawing_id", "")
    logger.info("=== PIPELINE: Starting REPORTER for %s ===", drawing_id)
    await manager.send_event(
        uuid.UUID(drawing_id), "reporter", "thought",
        {"message": "Generating RFI and inspection reports..."},
    )
    result = await run_reporter(state)
    logger.info("=== PIPELINE: REPORTER complete for %s ===", drawing_id)
    await manager.send_event(
        uuid.UUID(drawing_id), "reporter", "complete",
        {
            "message": "Audit complete",
            "integrity_score": result.get("integrity_score"),
            "total_findings": len(result.get("findings", [])),
        },
    )
    return result


def build_graph() -> StateGraph:
    graph = StateGraph(AuditState)

    graph.add_node("ingestor", ingestor_node)
    graph.add_node("sherlock", sherlock_node)
    graph.add_node("physicist", physicist_node)
    graph.add_node("reflexion", reflexion_node)
    graph.add_node("reporter", reporter_node)

    graph.set_entry_point("ingestor")
    graph.add_edge("ingestor", "sherlock")
    graph.add_edge("sherlock", "physicist")
    graph.add_conditional_edges(
        "physicist",
        reflexion_router,
        {"rescan": "reflexion", "report": "reporter"},
    )
    graph.add_edge("reflexion", "ingestor")
    graph.add_edge("reporter", END)

    return graph.compile()


audit_graph = build_graph()


async def run_audit(drawing_id: str, file_path: str) -> AuditState:
    """Run the full audit pipeline."""
    initial_state: AuditState = {
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

    result = await audit_graph.ainvoke(initial_state)
    return result
