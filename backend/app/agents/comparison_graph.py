"""LangGraph StateGraph wiring – master/check comparison pipeline."""
from __future__ import annotations

import uuid

from langgraph.graph import StateGraph, END

from app.agents.state import ComparisonState, AuditState
from app.agents.ingestor import run_ingestor
from app.agents.comparator import run_comparator
from app.agents.comparison_reporter import run_comparison_reporter
from app.services.ws_manager import manager


async def master_ingestor_node(state: ComparisonState) -> ComparisonState:
    session_id = state.get("session_id", "")

    # Skip if master already ingested
    if state.get("master_machine_state"):
        await manager.send_session_event(
            uuid.UUID(session_id), "ingestor", "thought",
            {"message": "Master drawing already ingested, reusing cached data."},
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
    session_id = state.get("session_id", "")

    await manager.send_session_event(
        uuid.UUID(session_id), "ingestor", "thought",
        {"message": "Analyzing check drawing with Gemini Vision..."},
    )

    audit_state: AuditState = {
        "drawing_id": state.get("check_drawing_id", ""),
        "file_path": state["check_file_path"],
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
    graph.add_node("reporter", reporter_node)

    graph.set_entry_point("master_ingestor")
    graph.add_edge("master_ingestor", "check_ingestor")
    graph.add_edge("check_ingestor", "comparator")
    graph.add_edge("comparator", "reporter")
    graph.add_edge("reporter", END)

    return graph.compile()


comparison_graph = build_comparison_graph()


async def run_comparison(
    session_id: str,
    master_file: str,
    check_file: str,
    master_drawing_id: str,
    check_drawing_id: str,
    master_machine_state: dict | None = None,
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
