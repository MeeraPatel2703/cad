"""Sherlock Agent – Logical cross-verification of extracted drawing data."""
from __future__ import annotations

import json
import logging

import google.generativeai as genai

from app.config import settings
from app.agents.state import AuditState, AuditFinding, FindingType, Severity

logger = logging.getLogger(__name__)

SHERLOCK_PROMPT = """You are Sherlock, an expert mechanical engineering auditor specializing in GD&T,
ASME Y14.5, and ISO drawing standards. You have been given structured data extracted from a mechanical drawing.

Perform these verification checks IN ORDER:

## 1. CONSENSUS AUDIT (Cross-View Verification)
Find dimensions that appear in multiple views and verify consistency:
- Same feature dimensioned in different views must match exactly
- Section views must agree with parent views
- Detail views must match their source views
- Flag ANY numeric discrepancy, even 0.001mm differences

## 2. ENVELOPE VERIFICATION (Dimensional Stack-Up)
Check geometric containment and stack-up logic:
- Child dimensions must sum to parent dimension (within tolerance)
- Shaft OD must be less than bore ID for clearance fits
- Interference fits: shaft must be larger than bore by specified amount
- Hole patterns: verify bolt circle diameter vs hole positions
- Assembly dimensions: components must fit within overall envelope

## 3. OMISSION DETECTION (Missing Critical Information)
Identify missing information per ASME Y14.5 / ISO 1101:
- **Critical fits without tolerance**: H7/g6, press fits, sliding fits MUST have tolerance class
- **Threaded holes without depth or thread spec**: Must specify thread size, pitch, depth
- **Bores/shafts without surface finish**: Mating surfaces need Ra callouts
- **Welds without size**: Fillet welds need leg size, groove welds need depth
- **Parts without material**: Every part needs material specification
- **Missing datums**: GD&T callouts need datum references
- **Incomplete hole callouts**: Need diameter, depth (THRU or blind depth), quantity

## 4. DECIMAL/UNIT CONSISTENCY
Check dimensional consistency:
- Mixed metric/imperial without conversion notes is ERROR
- Inconsistent decimal places (some 0.1, some 0.001) needs explanation
- Angular dimensions should be consistent (all decimal degrees OR all DMS)

## FINDING TYPES (use exactly these):
- **MISMATCH**: Same feature has different values in different locations
- **OMISSION**: Required information is missing from the drawing
- **DECIMAL_ERROR**: Unit or decimal place inconsistency
- **STACK_UP_ERROR**: Dimensions don't add up correctly
- **TOLERANCE_MISSING**: Critical feature lacks tolerance specification

## SEVERITY LEVELS:
- **critical**: Will cause part rejection or assembly failure (wrong dimension, impossible fit)
- **warning**: May cause manufacturing issues or ambiguity (missing tolerance on non-critical feature)
- **info**: Best practice violation, documentation improvement needed

## SPATIAL REFERENCING (CRITICAL)
When the drawing data includes balloon_data, you MUST reference balloon numbers in your findings:
- Use "at balloon #N" or "near balloon #N" to locate issues
- Include grid references like "(grid C4)" when zone/grid_ref data is available
- Use spatial language: "upper-left quadrant", "adjacent to feature Y", "between balloons #2 and #5"
- Every finding description must be locatable on the drawing by a human reader
- Example: "Bore diameter at balloon #3 (grid C4, Top View) missing H7 tolerance"

Return findings as JSON array:
[{{
  "finding_type": "MISMATCH|OMISSION|DECIMAL_ERROR|STACK_UP_ERROR|TOLERANCE_MISSING",
  "severity": "critical|warning|info",
  "category": "consensus|envelope|omission|decimal",
  "description": "Detailed engineering description with specific values AND balloon/location references",
  "affected_features": ["feature names or dimension values involved"],
  "coordinates": {{"x": 0, "y": 0}},
  "item_number": "1"|null,
  "zone": "zone name if applicable",
  "nearest_balloon": 3,
  "grid_ref": "C4",
  "evidence": {{
    "expected": "what should be there",
    "found": "what was actually found (or 'missing')",
    "views": ["view names where issue appears"],
    "standard_reference": "ASME Y14.5 section or ISO standard if applicable"
  }},
  "recommendation": "specific action to fix this issue"
}}]

Be thorough but precise. Every finding MUST have concrete evidence with actual values.
Do NOT fabricate issues - only report genuine problems found in the data.
Every description MUST reference the nearest balloon number and spatial location when balloon data is available.

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

    logger.info("Sherlock: sending prompt to Gemini (%d chars)", len(prompt))
    response = await model.generate_content_async(
        prompt,
        generation_config=genai.GenerationConfig(
            response_mime_type="application/json",
            temperature=0.2,
        ),
        request_options={"timeout": 600},
    )

    resp_text = response.text or ""
    logger.info("Sherlock: Gemini response length: %d chars", len(resp_text))
    logger.info("Sherlock: response preview: %.500s", resp_text[:500])

    try:
        raw_findings = json.loads(resp_text)
        logger.info("Sherlock: parsed JSON type=%s", type(raw_findings).__name__)
    except json.JSONDecodeError as e:
        logger.warning("Sherlock: JSON parse failed: %s", e)
        text = resp_text
        start = text.find("[")
        end = text.rfind("]") + 1
        if start >= 0 and end > start:
            raw_findings = json.loads(text[start:end])
        else:
            raw_findings = []

    if isinstance(raw_findings, dict):
        raw_findings = raw_findings.get("findings", [raw_findings])

    # Flatten nested lists and filter out non-dict items
    if raw_findings and isinstance(raw_findings, list) and isinstance(raw_findings[0], list):
        raw_findings = [item for sublist in raw_findings for item in sublist]
    raw_findings = [f for f in raw_findings if isinstance(f, dict)]

    findings = state.get("findings", [])
    for f in raw_findings:
        # Handle finding_type gracefully - default to OMISSION if unknown
        try:
            ftype = FindingType(f.get("finding_type", "OMISSION"))
        except ValueError:
            ftype = FindingType.OMISSION

        finding = AuditFinding(
            finding_type=ftype,
            severity=Severity(f.get("severity", "warning")),
            description=f.get("description", ""),
            coordinates=f.get("coordinates") or {},
            source_agent="sherlock",
            evidence=f.get("evidence") or {},
            item_number=f.get("item_number"),
            category=f.get("category"),
            zone=f.get("zone"),
            affected_features=f.get("affected_features") or [],
            recommendation=f.get("recommendation"),
            nearest_balloon=f.get("nearest_balloon"),
            grid_ref=f.get("grid_ref"),
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
