"""Comparison Reporter Agent – Generates RFI from comparison results."""
from __future__ import annotations

import json
from datetime import datetime, timezone

import google.generativeai as genai

from app.config import settings
from app.agents.state import ComparisonState

REPORTER_PROMPT = """You are a mechanical engineering inspection report writer.
Given the dimension-by-dimension comparison results between a master (reference) drawing
and a check (inspected) drawing, generate a formal Request for Information (RFI).

Focus on FAILING and WARNING dimensions only. For each issue:
- Reference the balloon number and feature
- Describe the discrepancy in professional engineering terminology
- Classify priority (Critical for fail, Major for warning)
- Suggest a resolution

Return as JSON:
{{
  "rfi": {{
    "reference": "Inspection comparison report",
    "date": "{date}",
    "items": [
      {{
        "number": 1,
        "balloon_ref": 3,
        "priority": "Critical",
        "description": "Dimension at balloon #3 (Shaft OD) measures 15.030mm, exceeding upper tolerance of +0.011mm. Deviation: +0.030mm.",
        "zone": "Top View",
        "resolution": "Re-machine to nominal 15.000mm within H7 tolerance band"
      }}
    ]
  }}
}}

COMPARISON RESULTS:
{comparison_items}

SUMMARY:
{summary}
"""


async def run_comparison_reporter(state: ComparisonState) -> ComparisonState:
    """Generate RFI from comparison results."""
    genai.configure(api_key=settings.GOOGLE_API_KEY)

    comparison_items = state.get("comparison_items", [])
    summary = state.get("summary", {})

    # Filter to only failing/warning items for the prompt
    issues = [c for c in comparison_items if c.get("status") in ("fail", "warning")]

    if not issues:
        rfi = {
            "reference": "Inspection comparison report",
            "date": datetime.now(timezone.utc).isoformat(),
            "items": [],
            "note": "All dimensions within tolerance. No RFI items generated.",
        }
    else:
        model = genai.GenerativeModel(settings.REASONING_MODEL)
        prompt = REPORTER_PROMPT.format(
            date=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            comparison_items=json.dumps(issues, indent=2),
            summary=json.dumps(summary, indent=2),
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
                report_data = {"rfi": {"items": []}}

        rfi = report_data.get("rfi", report_data)

    log_entry = {
        "agent": "comparison_reporter",
        "action": "rfi_generation",
        "rfi_items": len(rfi.get("items", [])),
        "score": summary.get("score", 0),
    }

    agent_log = list(state.get("agent_log", []))
    agent_log.append(log_entry)

    return {
        **state,
        "rfi": rfi,
        "agent_log": agent_log,
        "status": "complete",
    }
