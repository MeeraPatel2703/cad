"""Adversarial multi-model drawing review agent.

Round 1: Claude Vision does initial comparison
Round 2: Gemini audits Claude's findings against the same images
Round 3: Claude produces final merged report incorporating Gemini's challenges
"""
from __future__ import annotations

import base64
import json
import logging
from pathlib import Path

import anthropic
import google.generativeai as genai
import fitz  # PyMuPDF

from app.config import settings

logger = logging.getLogger(__name__)

# ── Shared JSON schema ──

RESULT_SCHEMA = """\
{
  "missing_dimensions": [
    {
      "value": "25.0",
      "type": "diameter",
      "location": "Mounting Hole — Bore Diameter",
      "description": "⌀25.0 H7 is present on master but absent from check"
    }
  ],
  "missing_tolerances": [
    {
      "value": "±0.1",
      "type": "tolerance",
      "location": "Base Plate — Overall Length (120mm)",
      "description": "Tolerance ±0.1 on 120mm dimension is missing from check"
    }
  ],
  "modified_values": [
    {
      "master_value": "50.0 ±0.1",
      "check_value": "50.0",
      "location": "Drive Shaft — Outer Diameter",
      "description": "Tolerance ±0.1 dropped from check drawing"
    }
  ],
  "summary": "3 dimensions missing, 1 tolerance missing, 1 value modified"
}"""

INSPECTOR_RULES = """\
You are a mechanical drawing checker. You will receive two engineering \
drawings: a MASTER (the reference) and a CHECK (the one being verified).

You have TWO jobs:
A) Find dimensions/tolerances on the MASTER that are TRULY MISSING from the CHECK.
B) Find dimensions/tolerances that EXIST ON BOTH but have DIFFERENT VALUES \
   (modified values). Pay special attention to subtle changes like decimal \
   shifts (0.5 vs 0.05), transposed digits (46.5 vs 45.6), dropped decimal \
   points (5 vs 0.5), or rounding differences.

Step 1 — READ THE MASTER. Go through EACH bordered section/view on the drawing \
(e.g. "Section A-A", "Detail B", named detail views). For each section, list \
every numerical callout: dimensions, tolerances, GD&T, surface finish, thread \
callouts, chamfer/radius notes.

Step 2 — FOR EACH callout, find the SAME FEATURE in the SAME SECTION on the \
CHECK drawing and determine:
  a) Is the callout present? If yes, does the value EXACTLY match?
  b) Watch for subtle value differences — compare digit by digit. \
     0.05 is NOT the same as 0.5. 22 is NOT the same as 2.2. \
     46.5 is NOT the same as 45.6.
  c) If the value exists but differs, report it as a MODIFIED VALUE.
  d) If the callout is completely absent, report it as MISSING.

Step 3 — VERIFICATION PASS. Before finalizing your report, go back through \
every item you flagged as MISSING and do one final check:
  - Search the ENTIRE check drawing for that exact value.
  - If you find it in the corresponding section, REMOVE it from your report.
  - A dimension is NOT missing if it appears on the check drawing in the \
    same section for the same feature.

CRITICAL RULES:
- FALSE POSITIVES are SERIOUS ERRORS. Reporting a dimension as missing \
  when it IS present on the check is unacceptable. When in doubt, do NOT \
  report it as missing.
- FALSE NEGATIVES for modified values are also serious. If a value differs \
  even slightly (e.g. 0.05 vs 0.5), you MUST catch it and report it.
- Compare values DIGIT BY DIGIT, including decimal places.
- DO NOT CONFUSE NEARBY VALUES. 22 and 23 are DIFFERENT dimensions on \
  DIFFERENT features. If the master shows 22 on one feature and 23 on \
  another, do not mix them up. Match each value to its specific feature \
  first, then compare master vs check for that same feature.
- NO DUPLICATES. Each finding must appear EXACTLY ONCE in your report. \
  Before adding an item, check if you already reported the same value at \
  the same location. If a dimension appears in multiple views, report it \
  only once using the most specific section/view name.
- A modified value should NEVER also appear as a missing dimension. If a \
  feature has a value on both drawings but they differ, report it ONLY as \
  a modified_value, NOT as missing_dimensions.

LOCATION NAMING: Engineering drawings have bordered sections, views, and \
named detail callouts. When reporting locations you MUST:
1. Use the EXACT section/view name printed on the drawing as your primary \
   identifier (e.g. "Section A-A", "Detail B", "CV Shell Fabrication Detail", \
   "CV Chute Flange Detail", "CV Manhole Cover Detail").
2. Then specify the feature and measurement within that section \
   (e.g. "Inner Diameter", "Wall Thickness", "Flange OD").
3. Format: "Section/View Name — Feature — Measurement" \
   (e.g. "Section A-A — Shell Wall — Thickness", \
   "CV Manhole Neck Detail — Neck OD").
4. NEVER use vague positional descriptions like "top-left", "bottom-right", \
   or "near the hole". Always use the actual printed section/view name.
5. If a dimension appears in the BOM or notes table, reference it as \
   "BOM — Item X — Description" or "General Notes — Note Y"."""


def _load_image_as_base64(file_path: str) -> tuple[str, str]:
    """Load a PDF or image file and return (base64_data, media_type)."""
    p = Path(file_path)
    suffix = p.suffix.lower()

    if suffix == ".pdf":
        doc = fitz.open(str(p))
        page = doc[0]
        mat = fitz.Matrix(2, 2)
        pix = page.get_pixmap(matrix=mat)
        img_bytes = pix.tobytes("png")
        doc.close()
        return base64.standard_b64encode(img_bytes).decode("utf-8"), "image/png"

    img_bytes = p.read_bytes()
    media_types = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".tiff": "image/tiff",
        ".tif": "image/tiff",
        ".bmp": "image/bmp",
        ".webp": "image/webp",
    }
    media_type = media_types.get(suffix, "image/png")
    return base64.standard_b64encode(img_bytes).decode("utf-8"), media_type


def _parse_json(raw: str) -> dict | None:
    """Try to extract JSON from a response that may have markdown fences."""
    text = raw.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text[3:]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        logger.error("JSON parse failed: %s", text[:500])
        return None


def _image_content_blocks(master_b64, master_media, check_b64, check_media):
    """Build the image content blocks for Claude messages."""
    return [
        {"type": "text", "text": "MASTER drawing:"},
        {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": master_media,
                "data": master_b64,
            },
        },
        {"type": "text", "text": "CHECK drawing:"},
        {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": check_media,
                "data": check_b64,
            },
        },
    ]


# ── Round 1: Claude initial review ──

async def _claude_initial_review(
    client: anthropic.AsyncAnthropic,
    master_b64: str, master_media: str,
    check_b64: str, check_media: str,
) -> tuple[dict | None, str]:
    """Claude does the first pass comparison. Returns (parsed_dict, raw_text)."""
    logger.info("Round 1: Claude initial review")

    message = await client.messages.create(
        model="claude-sonnet-4-5-20250929",
        max_tokens=8096,
        system=INSPECTOR_RULES,
        messages=[
            {
                "role": "user",
                "content": [
                    *_image_content_blocks(master_b64, master_media, check_b64, check_media),
                    {
                        "type": "text",
                        "text": (
                            "Step 1: Identify every bordered section/view on the "
                            "MASTER drawing (Section A-A, Detail B, named views, "
                            "etc.). For each section, list every dimension, "
                            "tolerance, GD&T callout, surface finish, and note.\n\n"
                            "Step 2: For EACH callout, find the same feature in "
                            "the same section on the CHECK drawing.\n"
                            "  - If the callout is MISSING entirely → missing_dimensions\n"
                            "  - If the value DIFFERS (compare digit by digit, "
                            "including decimals — 0.05 ≠ 0.5, 22 ≠ 2.2) → modified_values\n"
                            "  - If the value matches exactly → do NOT report it\n\n"
                            "Step 3: VERIFICATION — go back through every item "
                            "you flagged as missing. Search the check drawing one "
                            "more time for that exact value in the corresponding "
                            "section. If you find it, REMOVE it from your report.\n\n"
                            "Use the exact printed section/view names for locations.\n\n"
                            "Respond with JSON only:\n" + RESULT_SCHEMA
                        ),
                    },
                ],
            }
        ],
    )

    raw = message.content[0].text
    logger.info("Claude round 1: %d chars", len(raw))
    return _parse_json(raw), raw


# ── Round 2: Gemini audits Claude's findings ──

async def _gemini_audit(
    master_b64: str, master_media: str,
    check_b64: str, check_media: str,
    claude_findings: str,
) -> tuple[dict | None, str]:
    """Gemini reviews both drawings AND Claude's findings. Challenges missed items."""
    logger.info("Round 2: Gemini audit of Claude's findings")

    genai.configure(api_key=settings.GOOGLE_API_KEY)
    model = genai.GenerativeModel(settings.VISION_MODEL)

    prompt = f"""{INSPECTOR_RULES}

A previous inspector checked these drawings and reported:

<previous_report>
{claude_findings}
</previous_report>

DO YOUR OWN INDEPENDENT CHECK:
1. Go through each bordered section/view on the MASTER drawing. For each \
   section, list every numerical callout.
2. Find the same feature in the same section on the CHECK drawing. Compare \
   values DIGIT BY DIGIT including decimal places (0.05 ≠ 0.5, 22 ≠ 2.2).
3. The previous inspector may have MISSED subtle value modifications — \
   look carefully for decimal shifts, transposed digits, rounding errors.
4. The previous inspector may have FALSE POSITIVES — they may have flagged \
   dimensions that ARE present on the check. Verify each finding and remove \
   any that are actually present on the check.
5. Produce a COMPLETE report — missing items AND modified values. \
   Use the exact printed section/view names for locations.

Respond with JSON only:
{RESULT_SCHEMA}"""

    content_parts = [
        {"inline_data": {"mime_type": master_media, "data": master_b64}},
        "MASTER drawing (above)",
        {"inline_data": {"mime_type": check_media, "data": check_b64}},
        "CHECK drawing (above)",
        prompt,
    ]

    response = await model.generate_content_async(
        content_parts,
        generation_config=genai.GenerationConfig(
            temperature=0.1,
            max_output_tokens=8192,
        ),
    )

    raw = response.text
    logger.info("Gemini round 2: %d chars", len(raw))
    return _parse_json(raw), raw


# ── Round 3: Claude final merge ──

async def _claude_final_merge(
    client: anthropic.AsyncAnthropic,
    master_b64: str, master_media: str,
    check_b64: str, check_media: str,
    claude_report: str,
    gemini_report: str,
) -> tuple[dict | None, str]:
    """Claude gets the final word — merges both reports, re-checks the images."""
    logger.info("Round 3: Claude final merge")

    message = await client.messages.create(
        model="claude-sonnet-4-5-20250929",
        max_tokens=8096,
        system=INSPECTOR_RULES,
        messages=[
            {
                "role": "user",
                "content": [
                    *_image_content_blocks(master_b64, master_media, check_b64, check_media),
                    {
                        "type": "text",
                        "text": (
                            "Two inspectors independently checked what's on the "
                            "MASTER but missing or modified on the CHECK:\n\n"
                            f"INSPECTOR A:\n{claude_report}\n\n"
                            f"INSPECTOR B:\n{gemini_report}\n\n"
                            "Produce the FINAL report:\n\n"
                            "STEP 1 — ELIMINATE FALSE POSITIVES:\n"
                            "For EACH 'missing' finding, look at the CHECK drawing "
                            "in the corresponding section. If the value IS present "
                            "on the check, REMOVE it. Do not keep it just because "
                            "an inspector flagged it.\n\n"
                            "STEP 2 — CATCH MODIFIED VALUES:\n"
                            "For each dimension on the master, compare with the "
                            "check DIGIT BY DIGIT including decimal places. "
                            "Subtle changes like 0.05→0.5 or 46.5→45.6 "
                            "MUST be caught and reported as modified_values.\n\n"
                            "STEP 3 — DO NOT CONFUSE NEARBY VALUES:\n"
                            "22 and 23 are DIFFERENT dimensions for DIFFERENT "
                            "features. Match each value to its specific feature "
                            "first, then compare master vs check for that feature. "
                            "Never assume two close numbers refer to the same thing.\n\n"
                            "STEP 4 — DEDUPLICATE:\n"
                            "Each finding must appear EXACTLY ONCE. If a value is "
                            "reported as modified_values, it must NOT also appear in "
                            "missing_dimensions. Remove all duplicate entries — same "
                            "value + same location = one entry only.\n\n"
                            "STEP 5 — VERIFY LOCATIONS:\n"
                            "Every location must reference the exact printed "
                            "section/view name from the drawing (e.g. 'Section A-A', "
                            "'CV Shell Fabrication Detail'). Never use vague "
                            "positional descriptions.\n\n"
                            "Respond with JSON only:\n" + RESULT_SCHEMA
                        ),
                    },
                ],
            }
        ],
    )

    raw = message.content[0].text
    logger.info("Claude round 3 (final): %d chars", len(raw))
    return _parse_json(raw), raw


# ── Main entry point ──

async def run_review(master_path: str, check_path: str) -> dict:
    """Run adversarial multi-model review.

    Round 1: Claude initial review
    Round 2: Gemini audits Claude's findings
    Round 3: Claude merges both reports into final result
    """
    if not settings.ANTHROPIC_API_KEY:
        raise ValueError("ANTHROPIC_API_KEY is not configured")
    if not settings.GOOGLE_API_KEY:
        raise ValueError("GOOGLE_API_KEY is not configured")

    master_b64, master_media = _load_image_as_base64(master_path)
    check_b64, check_media = _load_image_as_base64(check_path)

    client = anthropic.AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)

    # Round 1: Claude
    claude_result, claude_raw = await _claude_initial_review(
        client, master_b64, master_media, check_b64, check_media,
    )

    # Round 2: Gemini audits
    gemini_result, gemini_raw = await _gemini_audit(
        master_b64, master_media, check_b64, check_media,
        claude_raw,
    )

    # Round 3: Claude final merge
    final_result, final_raw = await _claude_final_merge(
        client, master_b64, master_media, check_b64, check_media,
        claude_raw, gemini_raw,
    )

    if final_result is None:
        # Fallback to Gemini result, then Claude result
        final_result = gemini_result or claude_result or {
            "missing_dimensions": [],
            "missing_tolerances": [],
            "modified_values": [],
            "summary": "Error: Could not parse review results",
        }

    # Ensure all keys exist
    final_result.setdefault("missing_dimensions", [])
    final_result.setdefault("missing_tolerances", [])
    final_result.setdefault("modified_values", [])

    # ── Server-side deduplication ──
    # Remove duplicates within each list (same value + same location)
    def _dedup(items, key_fields):
        seen = set()
        unique = []
        for item in items:
            key = tuple(str(item.get(f, "")).strip().lower() for f in key_fields)
            if key not in seen:
                seen.add(key)
                unique.append(item)
        return unique

    final_result["missing_dimensions"] = _dedup(
        final_result["missing_dimensions"], ("value", "location")
    )
    final_result["missing_tolerances"] = _dedup(
        final_result["missing_tolerances"], ("value", "location")
    )
    final_result["modified_values"] = _dedup(
        final_result["modified_values"], ("master_value", "location")
    )

    # Remove from missing_dimensions any value that already appears in modified_values
    modified_keys = {
        (str(mv.get("master_value", "")).strip().lower(), mv.get("location", "").strip().lower())
        for mv in final_result["modified_values"]
    }
    final_result["missing_dimensions"] = [
        md for md in final_result["missing_dimensions"]
        if (str(md.get("value", "")).strip().lower(), md.get("location", "").strip().lower())
        not in modified_keys
    ]

    if "summary" not in final_result:
        md = len(final_result["missing_dimensions"])
        mt = len(final_result["missing_tolerances"])
        mv = len(final_result["modified_values"])
        final_result["summary"] = (
            f"{md} dimensions missing, {mt} tolerances missing, {mv} values modified"
        )

    return final_result
