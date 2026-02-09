"""Comparator Agent – Dimension matching and pass/fail evaluation between master and check drawings."""
from __future__ import annotations

import json
import math
from typing import Optional, List, Dict, Tuple, Set

import google.generativeai as genai

from app.config import settings
from app.agents.state import ComparisonState

MATCH_PROMPT = """You are a mechanical engineering dimension matching expert.
The CHECK drawing is a CUSTOMIZED version of the MASTER drawing. Values may intentionally differ.

Match each master dimension to its corresponding check dimension based on:
1. Feature type (diameter, length, thickness, etc.)
2. Zone/view location
3. Position on drawing (approximate)
4. Part/item it belongs to
5. Functional purpose (NOT exact value - values may differ intentionally)

IMPORTANT: The check drawing may have DIFFERENT values than master. This is expected.
Match by WHAT is being measured, not the measurement value itself.

Return a JSON array of matches:
[{{
  "master_index": 0,
  "check_index": 2,
  "confidence": 0.9,
  "reasoning": "Both are bore diameters in the top view at similar positions"
}}]

Include matches with confidence >= 0.5 (we want to catch intentional deviations).
If a master dimension truly has no corresponding feature in check, omit it.

MASTER DIMENSIONS:
{master_dims}

CHECK DIMENSIONS:
{check_dims}
"""


def _to_float(val) -> Optional[float]:
    """Safely convert a value to float, returning None if not possible."""
    if val is None:
        return None
    if isinstance(val, (int, float)):
        return float(val)
    if isinstance(val, str):
        # Try to extract numeric value from string
        import re
        # Handle fractions like "1/2", "3/4"
        frac_match = re.match(r'^(\d+)/(\d+)$', val.strip())
        if frac_match:
            return float(frac_match.group(1)) / float(frac_match.group(2))
        # Handle mixed fractions like "1 1/2"
        mixed_match = re.match(r'^(\d+)\s+(\d+)/(\d+)$', val.strip())
        if mixed_match:
            return float(mixed_match.group(1)) + float(mixed_match.group(2)) / float(mixed_match.group(3))
        # Try direct conversion
        try:
            # Remove common suffixes/units
            cleaned = re.sub(r'[^\d.\-+]', '', val.split()[0] if val.split() else val)
            if cleaned:
                return float(cleaned)
        except (ValueError, IndexError):
            pass
    return None


def _find_best_match(
    master_dim: dict,
    check_dims: List[dict],
    used_indices: Set[int],
) -> Optional[Tuple[int, dict]]:
    """Multi-pass deterministic matching for a single master dimension.

    Matches by FEATURE TYPE and LOCATION, not by value (since check may be customized).
    """
    m_item = master_dim.get("item_number")
    m_zone = master_dim.get("zone")
    m_feature = (master_dim.get("feature_type") or "").lower()
    m_val_raw = master_dim.get("value")
    m_val = _to_float(m_val_raw)
    m_val = m_val if m_val is not None else 0
    m_coords = master_dim.get("coordinates") or {}

    best_idx = None
    best_score = -1

    for i, c_dim in enumerate(check_dims):
        if i in used_indices:
            continue

        c_val_raw = c_dim.get("value")
        c_val = _to_float(c_val_raw)
        c_val = c_val if c_val is not None else 0
        c_feature = (c_dim.get("feature_type") or "").lower()
        score = 0

        # Feature type match (most important for customized drawings)
        if m_feature and c_feature:
            if m_feature == c_feature:
                score += 6  # Exact feature match
            elif m_feature in c_feature or c_feature in m_feature:
                score += 4  # Partial match (e.g., "diameter" in "bore_diameter")

        # Zone match
        if m_zone and c_dim.get("zone") == m_zone:
            score += 3

        # Item number match
        if m_item and c_dim.get("item_number") == m_item:
            score += 3

        # Value proximity (less strict - customizations expected)
        if m_val != 0 and c_val != 0:
            ratio = abs(m_val - c_val) / max(abs(m_val), 0.001)
            if ratio < 0.01:       # within 1% - likely same dimension
                score += 3
            elif ratio < 0.10:     # within 10%
                score += 2
            elif ratio < 0.30:     # within 30% - possible customization
                score += 1
            # Don't penalize large differences - may be intentional

        # Coordinate proximity
        mx, my = m_coords.get("x", -1), m_coords.get("y", -1)
        cx, cy = c_dim.get("coordinates", {}).get("x", -1), c_dim.get("coordinates", {}).get("y", -1)
        if mx >= 0 and cx >= 0:
            dist = math.hypot(mx - cx, my - cy)
            if dist < 100:
                score += 3
            elif dist < 250:
                score += 2
            elif dist < 400:
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

    # Lower threshold to catch more matches (customized values will differ)
    if best_idx is not None and best_score >= 2:
        return best_idx, check_dims[best_idx]
    return None


def _evaluate_tolerance(master_dim: dict, check_dim: dict) -> Tuple[str, Optional[float]]:
    """Evaluate status for a matched dimension pair.

    Returns (status, deviation) where status is:
    - "pass": Values match within tolerance
    - "warning": Values close but borderline
    - "deviation": Intentional change from master (for review)
    - "fail": Out of specified tolerance
    """
    nominal_raw = master_dim.get("nominal") or master_dim.get("value")
    nominal = _to_float(nominal_raw)
    nominal = nominal if nominal is not None else 0
    upper = _to_float(master_dim.get("upper_tol")) or 0
    lower = _to_float(master_dim.get("lower_tol")) or 0
    actual_raw = check_dim.get("value")
    actual = _to_float(actual_raw)
    actual = actual if actual is not None else 0

    if nominal == 0:
        return "pending", None

    deviation = actual - nominal
    pct_change = abs(deviation) / abs(nominal) if nominal != 0 else 0

    # If no tolerance specified, classify by deviation amount
    if upper == 0 and lower == 0:
        if abs(deviation) < 0.001:
            return "pass", deviation
        elif pct_change < 0.01:  # <1% - essentially same
            return "pass", deviation
        elif pct_change < 0.05:  # 1-5% - minor difference
            return "warning", deviation
        else:
            # Significant change - likely intentional customization
            return "deviation", deviation

    # Check against tolerance band
    if lower <= deviation <= upper:
        return "pass", deviation

    # Check warning band (within 120% of tolerance)
    tol_range = max(abs(upper), abs(lower))
    if tol_range > 0 and abs(deviation) <= tol_range * 1.2:
        return "warning", deviation

    # Outside tolerance - could be intentional customization or error
    # Use "deviation" for large changes (likely intentional), "fail" for small overruns
    if pct_change > 0.10:  # >10% change - likely intentional
        return "deviation", deviation
    else:
        return "fail", deviation


def _build_comparison(
    balloon_num: int, master_dim: dict, check_dim: Optional[dict],
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
    dims: List[dict],
    comparisons: List[dict],
    role: str,
) -> List[dict]:
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
    unmatched: List[Tuple[int, dict]],
    remaining_check: List[dict],
) -> List[dict]:
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
        if mi is not None and ci is not None and conf >= 0.5:
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
    used_check_indices: Set[int] = set()

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
    deviation_count = sum(1 for c in comparisons if c["status"] == "deviation")
    not_found = sum(1 for c in comparisons if c["status"] == "not_found")
    total = len(comparisons)

    # Score: pass + deviations (intentional changes are OK) vs total matched
    matched = total - not_found
    score = round(((pass_count + deviation_count) / max(matched, 1)) * 100, 1) if matched > 0 else 0

    summary = {
        "total_dimensions": total,
        "pass": pass_count,
        "fail": fail_count,
        "warning": warning_count,
        "deviation": deviation_count,
        "not_found": not_found,
        "score": score,
    }

    log_entry = {
        "agent": "comparator",
        "action": "dimension_comparison",
        "master_dims": len(master_dims),
        "check_dims": len(check_dims),
        "matched": matched,
        "not_found": not_found,
        "pass": pass_count,
        "fail": fail_count,
        "warning": warning_count,
        "deviation": deviation_count,
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
