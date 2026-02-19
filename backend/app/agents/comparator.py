"""Comparator Agent – Dimension matching and pass/fail evaluation between master and check drawings."""
from __future__ import annotations

import json
import math
from typing import Optional, List, Dict, Tuple, Set

import google.generativeai as genai
from google.generativeai.types import HarmCategory, HarmBlockThreshold

from app.config import settings
from app.agents.state import ComparisonState

# Comparison thresholds
TOLERANCE_THRESHOLD = 0.01  # Values must match within 0.01mm to be considered same dimension
DECIMAL_PLACE_THRESHOLD = 10  # 10x difference suggests decimal error (0.05 vs 0.5)

# Issue types
ISSUE_MODIFIED_VALUE = "modified_value"
ISSUE_MISSING_DIMENSION = "missing_dimension"
ISSUE_MISSING_TOLERANCE = "missing_tolerance"
ISSUE_SYMBOL_MISMATCH = "symbol_mismatch"

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


def _create_highlight_region(coords: Optional[Dict], radius: int = 80) -> Optional[Dict]:
    """Create a rectangular highlight region around coordinates."""
    if not coords or coords.get("x") is None or coords.get("y") is None:
        return None

    x = coords["x"]
    y = coords["y"]

    return {
        "x": max(0, x - radius),
        "y": max(0, y - radius),
        "width": radius * 2,
        "height": radius * 2,
    }


def _compare_dimension_values(master_val: float, check_val: float, feature_type: str) -> Dict:
    """Compare two dimension values and detect specific error types."""
    if master_val is None or check_val is None:
        return {"status": "warning", "issue": None}

    difference = abs(master_val - check_val)
    percent_diff = (difference / master_val * 100) if master_val != 0 else 0

    # Exact match or within tolerance
    if difference < TOLERANCE_THRESHOLD:
        return {"status": "pass", "issue": None, "difference": difference}

    # Check for decimal place error (0.05 vs 0.5, or 4.79 vs 479)
    min_val = min(abs(master_val), abs(check_val))
    if min_val > 0:
        ratio = max(abs(master_val), abs(check_val)) / min_val
        if ratio >= DECIMAL_PLACE_THRESHOLD:
            return {
                "status": "fail",
                "issue": ISSUE_MODIFIED_VALUE,
                "difference": difference,
                "description": f"Decimal place error: master={master_val}, check={check_val}. Values differ by {ratio:.1f}x",
            }

    # Regular value modification
    return {
        "status": "fail" if percent_diff > 5 else "warning",
        "issue": ISSUE_MODIFIED_VALUE,
        "difference": difference,
        "description": f"Value modified: master={master_val}, check={check_val} (diff: {difference:.2f}, {percent_diff:.1f}%)",
    }


def _compare_tolerances(master_dim: Dict, check_dim: Dict) -> Optional[Dict]:
    """Check if tolerances are present in master but missing from check."""
    master_upper = _to_float(master_dim.get("upper_tol"))
    master_lower = _to_float(master_dim.get("lower_tol"))
    check_upper = _to_float(check_dim.get("upper_tol"))
    check_lower = _to_float(check_dim.get("lower_tol"))

    # Master has tolerance but check doesn't
    if master_upper is not None or master_lower is not None:
        if check_upper is None and check_lower is None:
            return {
                "status": "fail",
                "issue": ISSUE_MISSING_TOLERANCE,
                "description": f"Tolerance missing in check: master has +{master_upper or 0}/-{abs(master_lower or 0)}, check has none",
            }

        # Tolerance values don't match
        if master_upper != check_upper or master_lower != check_lower:
            return {
                "status": "warning",
                "issue": ISSUE_MODIFIED_VALUE,
                "description": f"Tolerance modified: master +{master_upper or 0}/-{abs(master_lower or 0)}, check +{check_upper or 0}/-{abs(check_lower or 0)}",
            }

    return None


def _compare_gdt_symbols(master_gdt: List[Dict], check_gdt: List[Dict]) -> List[Dict]:
    """Compare GD&T symbols between master and check, flag mismatches."""
    issues = []

    # Build lookup by grid location and symbol type
    check_symbols = {
        (g.get("grid_ref"), g.get("symbol")): g
        for g in check_gdt
    }

    for master_symbol in master_gdt:
        grid_ref = master_symbol.get("grid_ref")
        symbol_type = master_symbol.get("symbol")
        key = (grid_ref, symbol_type)

        if key not in check_symbols:
            # Symbol exists in master but not check
            # Check for parallel vs perpendicular confusion
            parallel_key = (grid_ref, "parallel")
            perpendicular_key = (grid_ref, "perpendicular")

            if symbol_type == "parallel" and perpendicular_key in check_symbols:
                issues.append({
                    "status": "fail",
                    "issue": ISSUE_SYMBOL_MISMATCH,
                    "description": f"GD&T symbol mismatch at {grid_ref}: master has parallel, check has perpendicular",
                })
            elif symbol_type == "perpendicular" and parallel_key in check_symbols:
                issues.append({
                    "status": "fail",
                    "issue": ISSUE_SYMBOL_MISMATCH,
                    "description": f"GD&T symbol mismatch at {grid_ref}: master has perpendicular, check has parallel",
                })
            else:
                issues.append({
                    "status": "warning",
                    "issue": ISSUE_MISSING_DIMENSION,
                    "description": f"GD&T symbol '{symbol_type}' at {grid_ref} found in master but missing from check",
                })

    return issues


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

        # Tolerance class match (case-sensitive — H7 ≠ h7 in ISO system)
        m_tc = (master_dim.get("tolerance_class") or "").strip()
        c_tc = (c_dim.get("tolerance_class") or "").strip()
        if m_tc and c_tc:
            if m_tc == c_tc:
                score += 2
            elif m_tc.lower() == c_tc.lower():
                # Same letters but different case — likely a misread, still a partial match
                score += 1

        # Unit match
        if master_dim.get("unit", "mm") == c_dim.get("unit", "mm"):
            score += 1

        # Reduce confidence for dimensions OCR couldn't verify
        if c_dim.get("ocr_verified") is False:
            score = max(0, score - 2)

        # Reduce confidence for dimensions with validation issues
        if c_dim.get("validation_failed"):
            score = max(0, score - 2)

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
    nominal = _to_float(master_dim.get("nominal")) or _to_float(master_dim.get("value"))

    master_coords = master_dim.get("coordinates")
    master_hl = _create_highlight_region(master_coords)

    master_ocr = master_dim.get("ocr_verified")

    if check_dim is None:
        return {
            "balloon_number": balloon_num,
            "feature_description": _describe_dimension(master_dim),
            "zone": master_dim.get("zone"),
            "master_nominal": nominal,
            "master_upper_tol": _to_float(master_dim.get("upper_tol")),
            "master_lower_tol": _to_float(master_dim.get("lower_tol")),
            "master_unit": master_dim.get("unit", "mm"),
            "master_tolerance_class": master_dim.get("tolerance_class"),
            "check_actual": None,
            "deviation": None,
            "status": "not_found",
            "master_coordinates": master_coords,
            "check_coordinates": None,
            "notes": "No matching dimension found in check drawing",
            "highlight_region": {**master_hl, "side": "master"} if master_hl else None,
            "check_highlight_region": None,
            "master_ocr_verified": master_ocr,
            "check_ocr_verified": None,
        }

    status, deviation = _evaluate_tolerance(master_dim, check_dim)
    check_coords = check_dim.get("coordinates")
    check_hl = _create_highlight_region(check_coords)
    check_ocr = check_dim.get("ocr_verified")

    # Flag for manual review if either dimension has validation issues
    requires_manual_review = False
    review_reason = None
    if master_dim.get("validation_failed") or check_dim.get("validation_failed"):
        import logging as _logging
        _logging.getLogger(__name__).warning(
            f"Comparing dimension with validation issues: "
            f"master={master_dim.get('value')}, check={check_dim.get('value')}"
        )
        requires_manual_review = True
        review_reason = "Possible letter-number confusion in extraction"

    # Detect tolerance class mismatch (letter-sensitive comparison)
    m_tol_class = (master_dim.get("tolerance_class") or "").strip()
    c_tol_class = (check_dim.get("tolerance_class") or "").strip()
    if m_tol_class and c_tol_class and m_tol_class != c_tol_class:
        if status == "pass":
            status = "warning"
        notes_parts = [f"Tolerance class changed: {m_tol_class} → {c_tol_class}"]
        if m_tol_class.lower() == c_tol_class.lower():
            notes_parts.append("(case difference only — verify H/h shaft/hole distinction)")
        requires_manual_review = True
        review_reason = f"Tolerance class mismatch: {m_tol_class} vs {c_tol_class}"

    # Build highlight regions for non-pass statuses
    highlight_region = None
    check_highlight_region = None
    if status in ("fail", "warning", "deviation"):
        if master_hl:
            highlight_region = {**master_hl, "side": "master"}
        if check_hl:
            check_highlight_region = {**check_hl, "side": "check"}

    # Add note if OCR verification failed on either side
    notes = ""
    if m_tol_class and c_tol_class and m_tol_class != c_tol_class:
        notes = f"Tolerance class changed: {m_tol_class} → {c_tol_class}. "
        if m_tol_class.lower() == c_tol_class.lower():
            notes += "(case difference — verify H/h shaft/hole distinction) "
    if master_ocr is False:
        notes += "OCR could not verify master value in image. "
    if check_ocr is False:
        notes += "OCR could not verify check value in image."
    if requires_manual_review and review_reason and "Tolerance class" not in notes:
        notes += f" {review_reason}."

    result = {
        "balloon_number": balloon_num,
        "feature_description": _describe_dimension(master_dim),
        "zone": master_dim.get("zone"),
        "master_nominal": nominal,
        "master_upper_tol": _to_float(master_dim.get("upper_tol")),
        "master_lower_tol": _to_float(master_dim.get("lower_tol")),
        "master_unit": master_dim.get("unit", "mm"),
        "master_tolerance_class": master_dim.get("tolerance_class"),
        "check_actual": _to_float(check_dim.get("value")),
        "deviation": round(deviation, 4) if deviation is not None else None,
        "status": status,
        "master_coordinates": master_coords,
        "check_coordinates": check_coords,
        "notes": notes.strip(),
        "highlight_region": highlight_region,
        "check_highlight_region": check_highlight_region,
        "master_ocr_verified": master_ocr,
        "check_ocr_verified": check_ocr,
    }

    if requires_manual_review:
        result["requires_manual_review"] = True
        result["review_reason"] = review_reason

    return result


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
            safety_settings={
                HarmCategory.HARM_CATEGORY_HATE_SPEECH: HarmBlockThreshold.BLOCK_NONE,
                HarmCategory.HARM_CATEGORY_HARASSMENT: HarmBlockThreshold.BLOCK_NONE,
                HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT: HarmBlockThreshold.BLOCK_NONE,
                HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT: HarmBlockThreshold.BLOCK_NONE,
            },
            request_options={"timeout": 600},
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


def _compare_part_lists(
    master_parts: List[Dict],
    check_parts: List[Dict],
    next_balloon: int,
) -> Tuple[List[Dict], List[Dict]]:
    """Compare BOM/part lists between master and check drawings.

    Returns (comparisons, bom_mismatches) where:
    - comparisons: list of comparison items for the inspection table
    - bom_mismatches: structured list of BOM differences for logging
    """
    comparisons = []
    bom_mismatches = []

    # Index check parts by item_number for fast lookup
    check_by_item: Dict[str, Dict] = {}
    for part in check_parts:
        item_num = str(part.get("item_number", "")).strip()
        if item_num:
            check_by_item[item_num] = part

    balloon = next_balloon

    for m_part in master_parts:
        m_item = str(m_part.get("item_number", "")).strip()
        if not m_item:
            continue

        m_desc = m_part.get("description", "")
        m_material = m_part.get("material", "")
        m_qty = m_part.get("quantity")

        c_part = check_by_item.pop(m_item, None)

        if c_part is None:
            # Entire part missing from check BOM
            comparisons.append({
                "balloon_number": balloon,
                "feature_description": f"BOM Item {m_item} — {m_desc}",
                "zone": "BOM",
                "master_nominal": None,
                "master_upper_tol": None,
                "master_lower_tol": None,
                "master_unit": "",
                "master_tolerance_class": None,
                "check_actual": None,
                "deviation": None,
                "status": "missing",
                "master_coordinates": None,
                "check_coordinates": None,
                "notes": f"BOM Item {m_item} ({m_desc}, {m_material}) present in master but missing from check",
            })
            bom_mismatches.append({
                "type": "missing_part",
                "item_number": m_item,
                "master_description": m_desc,
                "master_material": m_material,
            })
            balloon += 1
            continue

        # Part exists in both — check for value mismatches
        c_desc = c_part.get("description", "")
        c_material = c_part.get("material", "")
        c_qty = c_part.get("quantity")

        diffs = []
        if m_desc.strip().lower() != c_desc.strip().lower():
            diffs.append(f"description: '{m_desc}' → '{c_desc}'")
        if m_material.strip().lower() != c_material.strip().lower():
            diffs.append(f"material: '{m_material}' → '{c_material}'")
        if m_qty is not None and c_qty is not None and str(m_qty) != str(c_qty):
            diffs.append(f"quantity: {m_qty} → {c_qty}")

        if diffs:
            comparisons.append({
                "balloon_number": balloon,
                "feature_description": f"BOM Item {m_item} — {m_desc}",
                "zone": "BOM",
                "master_nominal": None,
                "master_upper_tol": None,
                "master_lower_tol": None,
                "master_unit": "",
                "master_tolerance_class": None,
                "check_actual": None,
                "deviation": None,
                "status": "fail",
                "master_coordinates": None,
                "check_coordinates": None,
                "notes": f"BOM mismatch for Item {m_item}: {'; '.join(diffs)}",
            })
            bom_mismatches.append({
                "type": "modified_part",
                "item_number": m_item,
                "differences": diffs,
            })
            balloon += 1

    # Check parts not in master (extra items in check)
    for c_item, c_part in check_by_item.items():
        c_desc = c_part.get("description", "")
        comparisons.append({
            "balloon_number": balloon,
            "feature_description": f"BOM Item {c_item} — {c_desc}",
            "zone": "BOM",
            "master_nominal": None,
            "master_upper_tol": None,
            "master_lower_tol": None,
            "master_unit": "",
            "master_tolerance_class": None,
            "check_actual": None,
            "deviation": None,
            "status": "warning",
            "master_coordinates": None,
            "check_coordinates": None,
            "notes": f"BOM Item {c_item} ({c_desc}) found in check but not in master",
        })
        bom_mismatches.append({
            "type": "extra_part",
            "item_number": c_item,
            "check_description": c_desc,
        })
        balloon += 1

    return comparisons, bom_mismatches


def _find_missing_dimensions(
    master_dims: List[Dict],
    check_dims: List[Dict],
    comparisons: List[Dict],
) -> List[Dict]:
    """Find dimensions in master that don't appear in check drawing.

    Any comparison with status 'not_found' means the master dimension had no
    corresponding feature in the check. Re-label these as 'missing' with a
    clearer description.
    """
    missing = []
    for comp in comparisons:
        if comp["status"] == "not_found":
            comp["status"] = "missing"
            comp["notes"] = (
                f"Dimension {comp.get('master_nominal', '?')} "
                f"{comp.get('master_unit', 'mm')} "
                f"({comp.get('feature_description', 'unknown')}) "
                f"found in master but missing from check drawing"
            )
            # Ensure highlight points to master side for missing dims
            master_coords = comp.get("master_coordinates")
            if master_coords and not comp.get("highlight_region"):
                hl = _create_highlight_region(master_coords)
                if hl:
                    comp["highlight_region"] = {**hl, "side": "master"}
            missing.append(comp)
    return missing


async def run_comparator(state: ComparisonState) -> ComparisonState:
    """Compare master and check machine states dimension-by-dimension."""
    import logging
    logger = logging.getLogger(__name__)

    master_ms = state.get("master_machine_state") or {}
    check_ms = state.get("check_machine_state") or {}

    master_dims = master_ms.get("dimensions", [])
    check_dims = check_ms.get("dimensions", [])

    # Sanity check: compare extraction rates between master and check
    master_dim_count = len(master_dims)
    check_dim_count = len(check_dims)

    if master_dim_count > 0:
        ratio = check_dim_count / master_dim_count
        if ratio < 0.7 or ratio > 1.3:
            logger.warning(
                f"Dimension count mismatch: master={master_dim_count}, check={check_dim_count}. "
                f"This might indicate text color issues affecting extraction."
            )

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

    # Phase 2.5: Enhanced value & tolerance comparison on matched pairs
    for comp in comparisons:
        if comp["status"] in ("not_found",):
            continue  # Skip unmatched — handled in Phase 3

        master_nominal = comp.get("master_nominal")
        check_actual = comp.get("check_actual")

        if master_nominal is not None and check_actual is not None:
            # Compare values with decimal place detection
            value_result = _compare_dimension_values(
                master_nominal, check_actual,
                comp.get("feature_description", ""),
            )
            if value_result.get("issue"):
                comp["issue"] = value_result["issue"]
                comp["notes"] = value_result.get("description", comp.get("notes", ""))
                # Only upgrade status (don't downgrade a fail to warning)
                if value_result["status"] == "fail" or comp["status"] == "pass":
                    comp["status"] = value_result["status"]

        # Compare tolerances for each matched pair
        # Build pseudo-dim dicts from the comparison item
        master_tol_dim = {
            "upper_tol": comp.get("master_upper_tol"),
            "lower_tol": comp.get("master_lower_tol"),
        }
        # Reconstruct check tolerances from the matched check dim if available
        check_tol_dim = {"upper_tol": None, "lower_tol": None}
        check_coords = comp.get("check_coordinates")
        if check_coords is not None:
            # Find the check dim by coordinates match
            for c_dim in check_dims:
                c_coords = c_dim.get("coordinates", {})
                if c_coords and c_coords == check_coords:
                    check_tol_dim = {
                        "upper_tol": c_dim.get("upper_tol"),
                        "lower_tol": c_dim.get("lower_tol"),
                    }
                    break

        tol_result = _compare_tolerances(master_tol_dim, check_tol_dim)
        if tol_result:
            comp["tolerance_issue"] = tol_result
            if comp["status"] == "pass":
                comp["status"] = tol_result["status"]
            comp["notes"] = tol_result.get("description", comp.get("notes", ""))

    # Phase 2.6: Compare GD&T symbols
    master_gdt = master_ms.get("gdt_callouts", [])
    check_gdt = check_ms.get("gdt_callouts", [])
    gdt_issues = _compare_gdt_symbols(master_gdt, check_gdt)

    if gdt_issues:
        gdt_balloon_start = max((c["balloon_number"] for c in comparisons), default=0) + 1
        for idx, gdt_issue in enumerate(gdt_issues):
            comparisons.append({
                "balloon_number": gdt_balloon_start + idx,
                "feature_description": f"GD&T — {gdt_issue.get('description', 'Symbol issue')}",
                "zone": "GD&T",
                "master_nominal": None,
                "master_upper_tol": None,
                "master_lower_tol": None,
                "master_unit": "",
                "master_tolerance_class": None,
                "check_actual": None,
                "deviation": None,
                "status": gdt_issue["status"],
                "issue": gdt_issue.get("issue"),
                "master_coordinates": None,
                "check_coordinates": None,
                "notes": gdt_issue.get("description", ""),
            })
        logger.info(f"GD&T comparison: {len(gdt_issues)} issues found")

    # Phase 3: Detect dimensions missing from check
    missing_dims = _find_missing_dimensions(master_dims, check_dims, comparisons)
    if missing_dims:
        logger.warning(f"Found {len(missing_dims)} dimensions in master but not in check")

    # Phase 4: Compare BOM / part lists
    master_parts = master_ms.get("part_list", [])
    check_parts = check_ms.get("part_list", [])
    next_balloon = max((c["balloon_number"] for c in comparisons), default=0) + 1
    bom_comparisons, bom_mismatches = _compare_part_lists(master_parts, check_parts, next_balloon)
    if bom_comparisons:
        comparisons.extend(bom_comparisons)
        logger.info(f"BOM comparison: {len(bom_mismatches)} mismatches found")

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
    missing_count = sum(1 for c in comparisons if c["status"] == "missing")
    total = len(comparisons)

    # Score: pass + deviations (intentional changes are OK) vs total
    matched = total - missing_count
    score = round(((pass_count + deviation_count) / max(matched, 1)) * 100, 1) if matched > 0 else 0

    summary = {
        "total_dimensions": total,
        "pass": pass_count,
        "fail": fail_count,
        "warning": warning_count,
        "deviation": deviation_count,
        "missing": missing_count,
        "bom_mismatches": len(bom_mismatches),
        "gdt_issues": len(gdt_issues),
        "score": score,
    }

    log_entry = {
        "agent": "comparator",
        "action": "dimension_comparison",
        "master_dims": len(master_dims),
        "check_dims": len(check_dims),
        "matched": matched,
        "missing_from_check": missing_count,
        "pass": pass_count,
        "fail": fail_count,
        "warning": warning_count,
        "deviation": deviation_count,
        "gdt_issues": len(gdt_issues),
        "bom_master_parts": len(master_parts),
        "bom_check_parts": len(check_parts),
        "bom_mismatches": len(bom_mismatches),
    }

    # Flag extraction ratio issues in log
    if master_dim_count > 0:
        ratio = check_dim_count / master_dim_count
        if ratio < 0.7 or ratio > 1.3:
            log_entry["dimension_count_mismatch"] = True
            log_entry["extraction_ratio"] = round(ratio, 2)

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
