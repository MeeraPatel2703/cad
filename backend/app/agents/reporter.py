"""Reporter Agent – RFI, inspection sheet, and integrity score generation."""
from __future__ import annotations

import json
from datetime import datetime, timezone

import google.generativeai as genai

from app.config import settings
from app.agents.state import AuditState

REPORTER_PROMPT = """You are a mechanical engineering report writer.
Given the machine state and all audit findings, generate:

1. **RFI Document**: A formal Request for Information with:
   - Drawing reference (from title block)
   - Each finding as a numbered RFI item with:
     - Item number
     - Description in professional engineering terminology
     - Reference to specific drawing zone/view
     - Suggested resolution
   - Priority classification (Critical/Major/Minor)

2. **Inspection Sheet**: A quality inspection checklist with:
   - Each critical dimension with expected value and tolerance range
   - Required measuring instrument (caliper, CMM, micrometer, etc.)
   - Accept/Reject criteria
   - Special inspection notes

Return as JSON:
{{
  "rfi": {{
    "reference": "drawing number and revision",
    "date": "ISO date",
    "items": [{{"number": 1, "priority": "Critical", "description": "...", "zone": "...", "resolution": "..."}}]
  }},
  "inspection_sheet": {{
    "drawing_ref": "...",
    "items": [{{"dim_id": 1, "feature": "...", "nominal": 25.0, "tolerance": "+0.021/-0.000", "instrument": "Micrometer", "criteria": "..."}}]
  }}
}}

MACHINE STATE:
{machine_state}

FINDINGS:
{findings}
"""


def _calculate_integrity_score(machine_state: dict, findings: list[dict]) -> float:
    """Calculate integrity score: (verified_dims / total_dims) * 100, penalized by criticals."""
    total_dims = len(machine_state.get("dimensions", []))
    if total_dims == 0:
        return 0.0

    critical_count = sum(1 for f in findings if f.get("severity") == "critical")
    warning_count = sum(1 for f in findings if f.get("severity") == "warning")

    # Base score from dimension coverage
    finding_dims = set()
    for f in findings:
        item = f.get("item_number")
        if item:
            finding_dims.add(item)

    # Dimensions with issues
    problematic = len(finding_dims)
    verified = max(0, total_dims - problematic)

    base_score = (verified / total_dims) * 100

    # Penalties
    base_score -= critical_count * 10
    base_score -= warning_count * 3

    return max(0.0, min(100.0, round(base_score, 1)))


async def run_reporter(state: AuditState) -> AuditState:
    """Generate RFI, inspection sheet, and integrity score."""
    genai.configure(api_key=settings.GOOGLE_API_KEY)

    machine_state = state.get("machine_state", {})
    findings = state.get("findings", [])

    model = genai.GenerativeModel(settings.REASONING_MODEL)
    prompt = REPORTER_PROMPT.format(
        machine_state=json.dumps(machine_state, indent=2),
        findings=json.dumps(findings, indent=2),
    )

    response = await model.generate_content_async(
        prompt,
        generation_config=genai.GenerationConfig(
            response_mime_type="application/json",
            temperature=0.3,
        ),
    )

    try:
        report_data = json.loads(response.text)
    except json.JSONDecodeError:
        text = response.text
        start = text.find("{")
        end = text.rfind("}") + 1
        if start >= 0 and end > start:
            report_data = json.loads(text[start:end])
        else:
            report_data = {"rfi": {"items": []}, "inspection_sheet": {"items": []}}

    rfi = report_data.get("rfi", {})
    inspection_sheet = report_data.get("inspection_sheet", {})
    integrity_score = _calculate_integrity_score(machine_state, findings)

    log_entry = {
        "agent": "reporter",
        "action": "report_generation",
        "rfi_items": len(rfi.get("items", [])),
        "inspection_items": len(inspection_sheet.get("items", [])),
        "integrity_score": integrity_score,
    }

    agent_log = state.get("agent_log", [])
    agent_log.append(log_entry)

    return {
        **state,
        "rfi": rfi,
        "inspection_sheet": inspection_sheet,
        "integrity_score": integrity_score,
        "agent_log": agent_log,
        "status": "complete",
    }
