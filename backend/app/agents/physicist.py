"""Physicist Agent – Physics and tolerance calculations."""
from __future__ import annotations

import json
import math
from pathlib import Path

import google.generativeai as genai

from app.config import settings
from app.agents.state import AuditState, AuditFinding, FindingType, Severity

# Material densities in kg/m³
MATERIAL_DENSITIES = {
    "steel": 7850,
    "stainless steel": 8000,
    "aisi 1045": 7850,
    "aisi 304": 8000,
    "aisi 316": 8000,
    "aluminum": 2700,
    "al 6061": 2700,
    "al 7075": 2810,
    "brass": 8500,
    "bronze": 8800,
    "cast iron": 7200,
    "copper": 8960,
    "titanium": 4510,
    "nylon": 1150,
    "pom": 1410,
    "ptfe": 2200,
}


def _load_iso_tables() -> dict:
    iso_path = Path(__file__).parent.parent / "data" / "iso_tables.json"
    if iso_path.exists():
        with open(iso_path) as f:
            return json.load(f)
    return {}


def _get_material_density(material_str: str) -> float | None:
    mat_lower = material_str.lower().strip()
    for key, density in MATERIAL_DENSITIES.items():
        if key in mat_lower:
            return density
    return None


def _check_tolerance_fit(bore_dim: dict, shaft_dim: dict, iso_tables: dict) -> dict | None:
    """Check if bore/shaft tolerance classes form a valid fit."""
    bore_tol = bore_dim.get("tolerance_class", "")
    shaft_tol = shaft_dim.get("tolerance_class", "")

    if not bore_tol or not shaft_tol:
        return None

    fit_key = f"{bore_tol}/{shaft_tol}"
    fit_data = iso_tables.get("fits", {}).get(fit_key)

    if fit_data:
        return {
            "fit_type": fit_data.get("type", "unknown"),
            "clearance_min": fit_data.get("clearance_min"),
            "clearance_max": fit_data.get("clearance_max"),
            "valid": True,
        }

    return {"fit_type": "unknown", "valid": False, "note": f"Fit {fit_key} not in ISO tables"}


PHYSICIST_PROMPT = """You are a physics-focused engineering auditor.
Given the machine state and existing findings, perform:

1. **Mass-Property Validation**: For each item in the part list that has a weight,
   estimate theoretical weight from the visible geometry and material density.
   Flag if measured vs theoretical weight differs by more than 20%.

2. **Mating Integrity**: For any bore/shaft pairs, verify tolerance classes are
   compatible (e.g., H7/g6 is a clearance fit). Flag incompatible fits.

3. **Pressure Safety**: If any cylindrical vessels or pipes are visible, check
   wall thickness adequacy using basic thin-wall pressure vessel formula:
   t_min = (P * D) / (2 * S * E) where P=pressure, D=diameter, S=allowable stress, E=efficiency.
   Flag if wall appears undersized.

Return findings as JSON array:
[{{
  "finding_type": "PHYSICS_FAIL",
  "severity": "critical"|"warning",
  "description": "...",
  "coordinates": {{"x": 0, "y": 0}},
  "item_number": "1"|null,
  "evidence": {{"calculated": "...", "specified": "...", "formula": "..."}}
}}]

MACHINE STATE:
{machine_state}

EXISTING FINDINGS:
{findings}
"""


async def run_physicist(state: AuditState) -> AuditState:
    """Run physics and tolerance calculations."""
    genai.configure(api_key=settings.GOOGLE_API_KEY)

    machine_state = state.get("machine_state", {})
    existing_findings = state.get("findings", [])
    iso_tables = _load_iso_tables()

    # Local tolerance checks
    local_findings = []
    dimensions = machine_state.get("dimensions", [])

    # Find bore/shaft pairs by looking for matching zones
    bore_dims = [d for d in dimensions if (d.get("tolerance_class") or "").startswith(("H", "J", "K"))]
    shaft_dims = [d for d in dimensions if (d.get("tolerance_class") or "").startswith(("g", "f", "h", "k", "n", "p"))]

    for bore in bore_dims:
        for shaft in shaft_dims:
            if bore.get("zone") == shaft.get("zone") or bore.get("item_number") == shaft.get("item_number"):
                result = _check_tolerance_fit(bore, shaft, iso_tables)
                if result and not result.get("valid"):
                    local_findings.append(
                        AuditFinding(
                            finding_type=FindingType.PHYSICS_FAIL,
                            severity=Severity.WARNING,
                            description=f"Tolerance fit {bore.get('tolerance_class')}/{shaft.get('tolerance_class')} not verified in ISO tables",
                            coordinates=bore.get("coordinates", {}),
                            source_agent="physicist",
                            evidence=result,
                            item_number=bore.get("item_number"),
                        ).model_dump()
                    )

    # Weight validation for part list items
    part_list = machine_state.get("part_list", [])
    for part in part_list:
        weight = part.get("weight")
        material = part.get("material", "")
        if weight and material:
            density = _get_material_density(material)
            if density and weight > 0:
                # We can't calculate volume from 2D drawing alone,
                # but we can flag extreme outliers via Gemini
                pass

    # Use Gemini for deeper physics reasoning
    model = genai.GenerativeModel(settings.REASONING_MODEL)
    prompt = PHYSICIST_PROMPT.format(
        machine_state=json.dumps(machine_state, indent=2),
        findings=json.dumps(existing_findings, indent=2),
    )

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

    for f in raw_findings:
        local_findings.append(
            AuditFinding(
                finding_type=FindingType(f.get("finding_type", "PHYSICS_FAIL")),
                severity=Severity(f.get("severity", "warning")),
                description=f.get("description", ""),
                coordinates=f.get("coordinates") or {},
                source_agent="physicist",
                evidence=f.get("evidence") or {},
                item_number=f.get("item_number"),
            ).model_dump()
        )

    findings = existing_findings + local_findings

    log_entry = {
        "agent": "physicist",
        "action": "physics_validation",
        "findings_count": len(local_findings),
        "checks": ["tolerance_fits", "mass_properties", "pressure_safety"],
    }

    agent_log = state.get("agent_log", [])
    agent_log.append(log_entry)

    return {
        **state,
        "findings": findings,
        "agent_log": agent_log,
        "status": "physics_checked",
    }
