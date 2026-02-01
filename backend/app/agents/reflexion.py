"""Reflexion Agent – Self-correction re-scan logic."""
from __future__ import annotations

from app.agents.state import AuditState

MAX_REFLEXION = 3
WEIGHT_DISCREPANCY_THRESHOLD = 5.0  # 500%


def check_reflexion(state: AuditState) -> str:
    """Conditional edge: decide if re-scan is needed.

    Returns:
        "rescan" if ingestor should re-extract a region
        "report" if we should proceed to reporter
    """
    reflexion_count = state.get("reflexion_count", 0)
    if reflexion_count >= MAX_REFLEXION:
        return "report"

    findings = state.get("findings", [])
    machine_state = state.get("machine_state", {})

    # Check for extreme weight discrepancies (>500%)
    for finding in findings:
        if finding.get("source_agent") == "physicist" and finding.get("finding_type") == "PHYSICS_FAIL":
            evidence = finding.get("evidence", {})
            calculated = evidence.get("calculated")
            specified = evidence.get("specified")
            if calculated and specified:
                try:
                    calc_val = float(str(calculated).replace("kg", "").strip())
                    spec_val = float(str(specified).replace("kg", "").strip())
                    if spec_val > 0:
                        ratio = abs(calc_val - spec_val) / spec_val
                        if ratio > WEIGHT_DISCREPANCY_THRESHOLD:
                            return "rescan"
                except (ValueError, TypeError):
                    continue

    return "report"


async def prepare_rescan(state: AuditState) -> AuditState:
    """Prepare state for targeted re-scan of suspect region."""
    findings = state.get("findings", [])
    reflexion_count = state.get("reflexion_count", 0)

    # Find the suspect region from the most critical physics finding
    suspect_coords = None
    for finding in findings:
        if finding.get("source_agent") == "physicist" and finding.get("finding_type") == "PHYSICS_FAIL":
            coords = finding.get("coordinates", {})
            if coords.get("x") is not None and coords.get("y") is not None:
                suspect_coords = coords
                break

    # Create a crop region around the suspect area (200px padding)
    crop_region = None
    if suspect_coords:
        x, y = suspect_coords.get("x", 0), suspect_coords.get("y", 0)
        crop_region = {
            "x1": max(0, x - 200),
            "y1": max(0, y - 200),
            "x2": x + 200,
            "y2": y + 200,
        }

    log_entry = {
        "agent": "reflexion",
        "action": "prepare_rescan",
        "reflexion_count": reflexion_count + 1,
        "crop_region": crop_region,
    }

    agent_log = state.get("agent_log", [])
    agent_log.append(log_entry)

    return {
        **state,
        "reflexion_count": reflexion_count + 1,
        "crop_region": crop_region,
        "agent_log": agent_log,
        "status": "rescanning",
    }
