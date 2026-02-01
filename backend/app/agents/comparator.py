"""Comparator Agent – Dimension matching and pass/fail evaluation between master and check drawings."""
from __future__ import annotations

import json
import math

import google.generativeai as genai

from app.config import settings
from app.agents.state import ComparisonState

MATCH_PROMPT = """You are a mechanical engineering dimension matching expert.
Given UNMATCHED dimensions from a master drawing and REMAINING dimensions from a check drawing,
match each master dimension to its corresponding check dimension based on:
- Feature type and description
- Zone/view location
- Approximate position on the drawing
- Similar nominal value (within reason for manufacturing variation)

Return a JSON array of matches:
[{{
  "master_index": 0,
  "check_index": 2,
  "confidence": 0.9,
  "reasoning": "Both are bore diameters in the top view at similar positions"
}}]

Only include matches you are confident about (confidence >= 0.7).
If a master dimension has no plausible match, omit it.

UNMATCHED MASTER DIMENSIONS:
{master_dims}

REMAINING CHECK DIMENSIONS:
{check_dims}
"""


def _find_best_match(
    master_dim: dict,
    check_dims: list[dict],
    used_indices: set[int],
) -> tuple[int, dict] | None:
    """Multi-pass deterministic matching for a single master dimension."""
    m_item = master_dim.get("item_number")
    m_zone = master_dim.get("zone")
    m_val = master_dim.get("value", 0)
    m_coords = master_dim.get("coordinates", {})

    best_idx = None
    best_score = -1

    for i, c_dim in enumerate(check_dims):
        if i in used_indices:
            continue

        c_val = c_dim.get("value", 0)
        score = 0

        # Pass 1: item_number + zone match
        if m_item and c_dim.get("item_number") == m_item:
            score += 5
        if m_zone and c_dim.get("zone") == m_zone:
            score += 3

        # Pass 2: value proximity
        if m_val != 0 and c_val != 0:
            ratio = abs(m_val - c_val) / max(abs(m_val), 0.001)
            if ratio < 0.01:       # within 1%
                score += 4
            elif ratio < 0.05:     # within 5%
                score += 3
            elif ratio < 0.20:     # within 20%
                score += 1
            elif ratio > 0.50:     # more than 50% off
                score -= 5

        # Pass 3: coordinate proximity
        mx, my = m_coords.get("x", -1), m_coords.get("y", -1)
        cx, cy = c_dim.get("coordinates", {}).get("x", -1), c_dim.get("coordinates", {}).get("y", -1)
        if mx >= 0 and cx >= 0:
            dist = math.hypot(mx - cx, my - cy)
            if dist < 50:
                score += 2
            elif dist < 150:
                score += 1

        # Tolerance class match
        if (
            master_dim.get("tolerance_class")
            and master_dim.get("tolerance_class") == c_dim.get("tolerance_class")
        ):
            score += 2

        # Unit match
        if master_dim.get("unit", "mm") == c_dim.get("unit", "mm"):
            score += 1

        if score > best_score:
            best_score = score
            best_idx = i

    if best_idx is not None and best_score >= 3:
        return best_idx, check_dims[best_idx]
    return None


def _evaluate_tolerance(master_dim: dict, check_dim: dict) -> tuple[str, float | None]:
    """Evaluate pass/fail/warning for a matched dimension pair. Returns (status, deviation)."""
    nominal = master_dim.get("nominal") or master_dim.get("value", 0)
    upper = master_dim.get("upper_tol") or 0
    lower = master_dim.get("lower_tol") or 0
    actual = check_dim.get("value", 0)

    if nominal == 0:
        return "pending", None

    deviation = actual - nominal

    # If no tolerance specified, check if values match closely
    if upper == 0 and lower == 0:
        if abs(deviation) < 0.001:
            return "pass", deviation
        elif abs(deviation) / abs(nominal) < 0.01:
            return "pass", deviation
        elif abs(deviation) / abs(nominal) < 0.05:
            return "warning", deviation
        else:
            return "fail", deviation

    # Check against tolerance band
    if lower <= deviation <= upper:
        return "pass", deviation

    # Check warning band (within 120% of tolerance)
    tol_range = max(abs(upper), abs(lower))
    if tol_range > 0 and abs(deviation) <= tol_range * 1.2:
        return "warning", deviation

    return "fail", deviation


def _build_comparison(
    balloon_num: int, master_dim: dict, check_dim: dict | None,
) -> dict:
    """Build a comparison item dict from a master dimension and optional check dimension."""
    nominal = master_dim.get("nominal") or master_dim.get("value", 0)

    if check_dim is None:
        return {
            "balloon_number": balloon_num,
            "feature_description": _describe_dimension(master_dim),
            "zone": master_dim.get("zone"),
            "master_nominal": nominal,
            "master_upper_tol": master_dim.get("upper_tol"),
            "master_lower_tol": master_dim.get("lower_tol"),
            "master_unit": master_dim.get("unit", "mm"),
            "master_tolerance_class": master_dim.get("tolerance_class"),
            "check_actual": None,
            "deviation": None,
            "status": "not_found",
            "master_coordinates": master_dim.get("coordinates"),
            "check_coordinates": None,
            "notes": "No matching dimension found in check drawing",
        }

    status, deviation = _evaluate_tolerance(master_dim, check_dim)

    return {
        "balloon_number": balloon_num,
        "feature_description": _describe_dimension(master_dim),
        "zone": master_dim.get("zone"),
        "master_nominal": nominal,
        "master_upper_tol": master_dim.get("upper_tol"),
        "master_lower_tol": master_dim.get("lower_tol"),
        "master_unit": master_dim.get("unit", "mm"),
        "master_tolerance_class": master_dim.get("tolerance_class"),
        "check_actual": check_dim.get("value"),
        "deviation": round(deviation, 4) if deviation is not None else None,
        "status": status,
        "master_coordinates": master_dim.get("coordinates"),
        "check_coordinates": check_dim.get("coordinates"),
        "notes": "",
    }


def _describe_dimension(dim: dict) -> str:
    """Generate a human-readable description for a dimension."""
    parts = []
    if dim.get("tolerance_class"):
        parts.append(dim["tolerance_class"])
    if dim.get("zone"):
        parts.append(dim["zone"])
    if dim.get("item_number"):
        parts.append(f"Item {dim['item_number']}")
    val = dim.get("value", 0)
    unit = dim.get("unit", "mm")
    parts.append(f"{val} {unit}")
    return " / ".join(parts) if parts else f"{val} {unit}"


def _generate_balloons(
    dims: list[dict],
    comparisons: list[dict],
    role: str,
) -> list[dict]:
    """Generate balloon overlay data for a drawing."""
    balloons = []
    coord_key = "master_coordinates" if role == "master" else "check_coordinates"

    for comp in comparisons:
        coords = comp.get(coord_key)
        if not coords:
            continue
        val = comp["master_nominal"] if role == "master" else comp.get("check_actual")
        if val is None:
            continue
        balloons.append({
            "balloon_number": comp["balloon_number"],
            "value": val,
            "unit": comp.get("master_unit", "mm"),
            "coordinates": coords,
            "tolerance_class": comp.get("master_tolerance_class"),
            "nominal": comp.get("master_nominal"),
            "upper_tol": comp.get("master_upper_tol"),
            "lower_tol": comp.get("master_lower_tol"),
            "status": comp["status"],
        })

    return balloons


async def _llm_match_dimensions(
    unmatched: list[tuple[int, dict]],
    remaining_check: list[dict],
) -> list[dict]:
    """Use Gemini to match remaining unmatched dimensions."""
    if not unmatched or not remaining_check:
        return [_build_comparison(bn, md, None) for bn, md in unmatched]

    genai.configure(api_key=settings.GOOGLE_API_KEY)
    model = genai.GenerativeModel(settings.REASONING_MODEL)

    master_for_prompt = [
        {"index": i, "dim": dim} for i, (_, dim) in enumerate(unmatched)
    ]
    check_for_prompt = [
        {"index": i, "dim": dim} for i, dim in enumerate(remaining_check)
    ]

    prompt = MATCH_PROMPT.format(
        master_dims=json.dumps(master_for_prompt, indent=2),
        check_dims=json.dumps(check_for_prompt, indent=2),
    )

    try:
        response = await model.generate_content_async(
            prompt,
            generation_config=genai.GenerationConfig(
                response_mime_type="application/json",
                temperature=0.1,
            ),
        )
        # Robust JSON parsing
        text = response.text
        try:
            matches = json.loads(text)
        except json.JSONDecodeError:
            import re
            # Fix common JSON issues
            text = re.sub(r',\s*([}\]])', r'\1', text)  # Remove trailing commas
            start = text.find('[')
            end = text.rfind(']') + 1
            if start >= 0 and end > start:
                try:
                    matches = json.loads(text[start:end])
                except:
                    matches = []
            else:
                matches = []
        if isinstance(matches, dict):
            matches = matches.get("matches", [])
    except Exception:
        matches = []

    matched_master = set()
    comparisons = []

    for m in matches:
        mi = m.get("master_index")
        ci = m.get("check_index")
        conf = m.get("confidence", 0)
        if mi is not None and ci is not None and conf >= 0.7:
            if mi < len(unmatched) and ci < len(remaining_check):
                bn, md = unmatched[mi]
                cd = remaining_check[ci]
                comparisons.append(_build_comparison(bn, md, cd))
                matched_master.add(mi)

    # Mark truly unmatched as not_found
    for i, (bn, md) in enumerate(unmatched):
        if i not in matched_master:
            comparisons.append(_build_comparison(bn, md, None))

    return comparisons


async def run_comparator(state: ComparisonState) -> ComparisonState:
    """Compare master and check machine states dimension-by-dimension."""
    master_ms = state.get("master_machine_state") or {}
    check_ms = state.get("check_machine_state") or {}

    master_dims = master_ms.get("dimensions", [])
    check_dims = check_ms.get("dimensions", [])

    # Phase 1: Deterministic matching
    comparisons = []
    unmatched_master = []
    used_check_indices: set[int] = set()

    for i, m_dim in enumerate(master_dims):
        balloon_num = i + 1
        match = _find_best_match(m_dim, check_dims, used_check_indices)
        if match:
            check_idx, check_dim = match
            used_check_indices.add(check_idx)
            comparisons.append(_build_comparison(balloon_num, m_dim, check_dim))
        else:
            unmatched_master.append((balloon_num, m_dim))

    # Phase 2: LLM-assisted matching for remaining
    if unmatched_master:
        remaining_check = [
            d for i, d in enumerate(check_dims) if i not in used_check_indices
        ]
        llm_comparisons = await _llm_match_dimensions(unmatched_master, remaining_check)
        comparisons.extend(llm_comparisons)

    # Sort by balloon number
    comparisons.sort(key=lambda c: c["balloon_number"])

    # Generate balloon data for both drawings
    master_balloons = _generate_balloons(master_dims, comparisons, "master")
    check_balloons = _generate_balloons(check_dims, comparisons, "check")

    # Summary stats
    pass_count = sum(1 for c in comparisons if c["status"] == "pass")
    fail_count = sum(1 for c in comparisons if c["status"] == "fail")
    warning_count = sum(1 for c in comparisons if c["status"] == "warning")
    not_found = sum(1 for c in comparisons if c["status"] == "not_found")
    total = len(comparisons)

    summary = {
        "total_dimensions": total,
        "pass": pass_count,
        "fail": fail_count,
        "warning": warning_count,
        "not_found": not_found,
        "score": round((pass_count / max(total, 1)) * 100, 1),
    }

    log_entry = {
        "agent": "comparator",
        "action": "dimension_comparison",
        "master_dims": len(master_dims),
        "check_dims": len(check_dims),
        "matched": total - not_found,
        "not_found": not_found,
        "pass": pass_count,
        "fail": fail_count,
        "warning": warning_count,
    }

    agent_log = list(state.get("agent_log", []))
    agent_log.append(log_entry)

    return {
        **state,
        "comparison_items": comparisons,
        "master_balloon_data": master_balloons,
        "check_balloon_data": check_balloons,
        "summary": summary,
        "agent_log": agent_log,
        "status": "compared",
    }
