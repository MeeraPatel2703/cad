"""Sherlock Agent – Logical cross-verification of extracted drawing data."""
from __future__ import annotations

import json

import google.generativeai as genai

from app.config import settings
from app.agents.state import AuditState, AuditFinding, FindingType, Severity

SHERLOCK_PROMPT = """You are Sherlock, an expert mechanical engineering auditor.
You have been given structured data extracted from a mechanical drawing.

Perform these verification checks:

1. **Consensus Audit**: Find any dimension that appears in multiple views.
   Verify they match. Flag any mismatches with coordinates of both occurrences.

2. **Envelope Verification**: Check that child/detail dimensions sum correctly
   within parent/assembly dimensions. A shaft cannot be larger than its housing bore.

3. **Omission Detection**: Identify any features or parts that are missing:
   - Dimensions without tolerances on critical fits
   - Parts without material specification
   - Weld symbols without size
   - Missing surface finish callouts on mating surfaces
   - Items in part list not referenced in drawing views

4. **Decimal/Unit Consistency**: Check all dimensions use consistent decimal places
   and units. Flag mixed metric/imperial without conversion notes.

Return your findings as a JSON array of objects:
[{{
  "finding_type": "MISMATCH"|"OMISSION"|"DECIMAL_ERROR",
  "severity": "critical"|"warning"|"info",
  "description": "Clear engineering description of the issue",
  "coordinates": {{"x": 0, "y": 0}},
  "item_number": "1"|null,
  "evidence": {{"expected": "...", "found": "...", "views": ["Top View", "Section A-A"]}}
}}]

Be specific. Reference item numbers and zone names. Every finding must have evidence.
If no issues found for a check category, that's fine – don't fabricate findings.

DRAWING DATA:
"""


async def run_sherlock(state: AuditState) -> AuditState:
    """Cross-verify MachineState data for logical consistency."""
    genai.configure(api_key=settings.GOOGLE_API_KEY)

    machine_state = state.get("machine_state", {})
    if not machine_state:
        return {
            **state,
            "status": "error",
            "agent_log": state.get("agent_log", []) + [{"agent": "sherlock", "error": "No machine state"}],
        }

    model = genai.GenerativeModel(settings.REASONING_MODEL)
    prompt = SHERLOCK_PROMPT + json.dumps(machine_state, indent=2)

    response = await model.generate_content_async(
        prompt,
        generation_config=genai.GenerationConfig(
            response_mime_type="application/json",
            temperature=0.2,
        ),
    )

    try:
        raw_findings = json.loads(response.text)
    except json.JSONDecodeError:
        text = response.text
        start = text.find("[")
        end = text.rfind("]") + 1
        if start >= 0 and end > start:
            raw_findings = json.loads(text[start:end])
        else:
            raw_findings = []

    if isinstance(raw_findings, dict):
        raw_findings = raw_findings.get("findings", [raw_findings])

    findings = state.get("findings", [])
    for f in raw_findings:
        finding = AuditFinding(
            finding_type=FindingType(f.get("finding_type", "OMISSION")),
            severity=Severity(f.get("severity", "warning")),
            description=f.get("description", ""),
            coordinates=f.get("coordinates") or {},
            source_agent="sherlock",
            evidence=f.get("evidence") or {},
            item_number=f.get("item_number"),
        )
        findings.append(finding.model_dump())

    log_entry = {
        "agent": "sherlock",
        "action": "cross_verification",
        "findings_count": len(raw_findings),
        "checks": ["consensus", "envelope", "omission", "decimal_consistency"],
    }

    agent_log = state.get("agent_log", [])
    agent_log.append(log_entry)

    return {
        **state,
        "findings": findings,
        "agent_log": agent_log,
        "status": "verified",
    }
