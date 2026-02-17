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
      "location": "Top-left bore hole",
      "description": "⌀25.0 H7 is present on master but absent from check"
    }
  ],
  "missing_tolerances": [
    {
      "value": "±0.1",
      "type": "tolerance",
      "location": "Overall length 120mm",
      "description": "Tolerance ±0.1 on 120mm dimension is missing from check"
    }
  ],
  "modified_values": [
    {
      "master_value": "50.0 ±0.1",
      "check_value": "50.0",
      "location": "Shaft diameter",
      "description": "Tolerance ±0.1 dropped from check drawing"
    }
  ],
  "summary": "3 dimensions missing, 1 tolerance missing, 1 value modified"
}"""

INSPECTOR_RULES = """\
You are a mechanical drawing checker. You will receive two engineering \
drawings: a MASTER (the reference) and a CHECK (the one being verified).

YOUR JOB: Find callouts that are MISSING from the check, or where the \
VALUE HAS CHANGED between master and check.

PRIORITY CHECKS (do these FIRST):
- ANGLES: Compare every angle on the master (°, deg) to the check. \
  Read the exact numeric value carefully — 30° vs 45° vs 52° etc. \
  Angles are the most commonly modified values between revisions.
- TOLERANCES: Compare every tolerance — ±values, fit classes (H7/g6), \
  unilateral tolerances (+2°/-0° vs ±2°). Even small changes matter.
- DIMENSIONS: Compare every linear, diameter, and radius dimension.

Step 1 — READ THE MASTER carefully. For every callout, write down the \
exact numeric value you see. Pay special attention to angles and their \
tolerances.

Step 2 — READ THE CHECK carefully. For every callout on the master, find \
the corresponding one on the check. Compare the EXACT values. \
A 30° angle on the master that shows 45° on the check is a MODIFIED VALUE. \
A ±2° tolerance on the master that shows +2°/-0° on the check is MODIFIED.

Step 3 — Report:
  - MISSING: callouts on master that don't appear anywhere on check
  - MODIFIED: callouts where the value or tolerance CHANGED between drawings

Do NOT report items that are identical on both drawings. \
Do NOT hallucinate values — only report what you can clearly read. \
If you cannot read a value clearly, skip it."""


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
                            "PRIORITY: Start by comparing ALL ANGLES between the "
                            "two drawings. Read each angle value on the master, "
                            "then find it on the check and compare.\n\n"
                            "Step 1: Read the MASTER. List every angle (°), "
                            "dimension, tolerance, and callout with its exact value.\n\n"
                            "Step 2: For EACH one, find it on the CHECK. "
                            "Compare exact values — is it the same number? "
                            "Is the tolerance the same?\n\n"
                            "Step 3: Report MISSING items and MODIFIED values "
                            "(where the number changed between drawings).\n\n"
                            "Do NOT report items that match. "
                            "Do NOT guess values you can't clearly read.\n\n"
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
1. Read every callout on the MASTER drawing yourself.
2. Check each one against the CHECK drawing.
3. The previous inspector may have MISSED things. Find them.
4. The previous inspector may have been WRONG. Correct them.
5. Produce a COMPLETE report — include everything genuinely missing, \
   whether the previous inspector found it or not.

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
                            "MASTER but missing from the CHECK:\n\n"
                            f"INSPECTOR A:\n{claude_report}\n\n"
                            f"INSPECTOR B:\n{gemini_report}\n\n"
                            "Produce the FINAL report:\n"
                            "1. For EACH finding from either inspector, look at "
                            "the master drawing — is this callout really there? "
                            "Then look at the check — is it really missing?\n"
                            "2. KEEP anything genuinely on the master but not the check\n"
                            "3. REMOVE false positives only if you can clearly see "
                            "the item IS present on the check\n"
                            "4. If either inspector found it and you can't clearly "
                            "disprove it, KEEP it\n"
                            "5. Add anything both missed\n\n"
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
    if "summary" not in final_result:
        md = len(final_result["missing_dimensions"])
        mt = len(final_result["missing_tolerances"])
        mv = len(final_result["modified_values"])
        final_result["summary"] = (
            f"{md} dimensions missing, {mt} tolerances missing, {mv} values modified"
        )

    return final_result
