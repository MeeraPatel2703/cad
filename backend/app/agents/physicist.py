"""Physicist Agent – Physics and tolerance calculations using Machinery Handbook data."""
from __future__ import annotations

import json
import math
import re
from pathlib import Path

import google.generativeai as genai

from app.config import settings
from app.agents.state import AuditState, AuditFinding, FindingType, Severity

# Material densities in kg/m³ - expanded from Machinery Handbook
MATERIAL_DENSITIES = {
    "steel": 7850,
    "carbon steel": 7850,
    "mild steel": 7870,
    "stainless steel": 8000,
    "stainless": 8000,
    "aisi 1018": 7870,
    "aisi 1045": 7850,
    "1045": 7850,
    "aisi 4140": 7850,
    "4140": 7850,
    "aisi 4340": 7850,
    "4340": 7850,
    "aisi 304": 8000,
    "304": 8000,
    "304 ss": 8000,
    "aisi 316": 8000,
    "316": 8000,
    "316 ss": 8000,
    "aluminum": 2700,
    "aluminium": 2700,
    "al 6061": 2700,
    "6061": 2700,
    "6061-t6": 2700,
    "al 7075": 2810,
    "7075": 2810,
    "7075-t6": 2810,
    "brass": 8500,
    "c360": 8500,
    "bronze": 8800,
    "bearing bronze": 8800,
    "c932": 8800,
    "cast iron": 7200,
    "gray iron": 7200,
    "ductile iron": 7100,
    "copper": 8960,
    "titanium": 4430,
    "ti-6al-4v": 4430,
    "ti 6al-4v": 4430,
    "nylon": 1140,
    "nylon 6": 1140,
    "polyamide": 1140,
    "pom": 1410,
    "delrin": 1410,
    "acetal": 1410,
    "ptfe": 2200,
    "teflon": 2200,
    "hdpe": 960,
    "abs": 1050,
    "polycarbonate": 1200,
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


PHYSICIST_PROMPT = """You are a physics-focused engineering auditor with expertise from the Machinery Handbook.
Given the machine state and existing findings, perform COMPREHENSIVE physics validation:

## 1. TOLERANCE FIT VALIDATION (ISO 286 / ANSI B4.1)
For any bore/shaft pairs, verify tolerance classes form valid fits:
- **Clearance fits** (H7/g6, H8/f7): shaft smaller than bore, allows rotation/sliding
- **Transition fits** (H7/k6, H7/js6): may have slight clearance or interference
- **Interference fits** (H7/p6, H7/s6): shaft larger than bore, requires press/shrink fit
- Flag INCOMPATIBLE combinations (e.g., interference fit on rotating shaft)
- Flag MISSING tolerance classes on critical mating features

## 2. BEARING FIT REQUIREMENTS
Check bearing mounting per ISO standards:
- Rotating inner ring with normal load: shaft should be k5-m5, housing H7
- Stationary inner ring: shaft should be g6-h6, housing J7-K7
- Flag oversized/undersized fits that will cause bearing damage

## 3. THREAD VALIDATION
For threaded features:
- Verify tap drill diameter is correct for thread size (per Machinery Handbook)
- Check thread depth is adequate (min 1.5x diameter for steel, 2x for aluminum)
- Flag missing thread specifications (size, pitch, class)

## 4. MASS-PROPERTY VALIDATION
For parts with weight specifications:
- Estimate theoretical weight: Volume × Density
- Use material densities: Steel=7850, Al=2700, SS=8000, Brass=8500, Bronze=8800 kg/m³
- Flag if specified vs calculated weight differs by >20%

## 5. STRUCTURAL INTEGRITY
Check for obvious structural issues:
- Wall thickness vs pressure (t_min = PD/2S for thin-wall vessels)
- Shaft diameter vs torque capacity
- Keyway depth vs shaft strength (max 25% of diameter)
- Fillet radius at stress concentrations

## 6. MATERIAL COMPATIBILITY
Flag incompatible material combinations:
- Galvanic corrosion risks (e.g., aluminum + copper without isolation)
- Dissimilar thermal expansion in precision assemblies
- Hardness mismatch in wear pairs (bearing should be softer than shaft)

## FINDING TYPES:
- **PHYSICS_FAIL**: Calculation shows physical impossibility or failure risk
- **FIT_ERROR**: Tolerance fit is incorrect for application
- **MATERIAL_ERROR**: Material specification issue

## SEVERITY:
- **critical**: Will cause part failure, safety risk, or assembly impossible
- **warning**: Suboptimal but may function, quality concern

Return findings as JSON array:
[{{
  "finding_type": "PHYSICS_FAIL|FIT_ERROR|MATERIAL_ERROR",
  "severity": "critical|warning",
  "category": "fit|bearing|thread|mass|structure|material",
  "description": "Detailed engineering description with values",
  "affected_features": ["dimension/part names involved"],
  "coordinates": {{"x": 0, "y": 0}},
  "item_number": "1"|null,
  "zone": "zone name if applicable",
  "evidence": {{
    "calculated": "calculated value with formula",
    "specified": "value from drawing",
    "formula": "physics formula used",
    "handbook_reference": "Machinery Handbook section/table"
  }},
  "recommendation": "specific corrective action"
}}]

Be thorough. Check EVERY tolerance class, EVERY thread, EVERY mating feature.
Do NOT fabricate issues - only report genuine physics/fit problems.

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
        # Handle finding_type gracefully
        try:
            ftype = FindingType(f.get("finding_type", "PHYSICS_FAIL"))
        except ValueError:
            ftype = FindingType.PHYSICS_FAIL

        local_findings.append(
            AuditFinding(
                finding_type=ftype,
                severity=Severity(f.get("severity", "warning")),
                description=f.get("description", ""),
                coordinates=f.get("coordinates") or {},
                source_agent="physicist",
                evidence=f.get("evidence") or {},
                item_number=f.get("item_number"),
                category=f.get("category"),
                zone=f.get("zone"),
                affected_features=f.get("affected_features") or [],
                recommendation=f.get("recommendation"),
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
