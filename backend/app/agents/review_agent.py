"""Claude-powered drawing review agent.

Sends master + check drawing images to Claude Vision and returns
a structured report of missing/modified dimensions and tolerances.
"""
from __future__ import annotations

import base64
import json
import logging
from pathlib import Path

import anthropic
import fitz  # PyMuPDF

from app.config import settings

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """\
You are an expert mechanical engineering drawing inspector. Your job is to \
compare a MASTER drawing against a CHECK drawing and report exactly what is \
missing or different.

Rules:
1. Examine every single dimension, tolerance, GD&T callout, surface finish, \
   and note on the MASTER drawing.
2. For each item, verify it exists on the CHECK drawing with the same value.
3. Report ONLY items that are MISSING from the check or have DIFFERENT values.
4. For each finding, provide:
   - The exact value as it appears on the master
   - The type (dimension, tolerance, GD&T, surface_finish, note)
   - A human-readable location description (e.g. "top-left bore hole", \
     "flange outer diameter", "Section A-A")
   - A brief description of what is missing or changed
5. Be extremely thorough — check every number, symbol, tolerance callout, \
   datum reference, and geometric tolerance frame.
6. If a dimension exists on both drawings but the tolerance is missing on the \
   check, report it as a missing tolerance.
7. If the check drawing has all dimensions and tolerances matching the master, \
   report empty lists.

You MUST respond with valid JSON only, no markdown fences, using this exact schema:
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
}
"""


def _load_image_as_base64(file_path: str) -> tuple[str, str]:
    """Load a PDF or image file and return (base64_data, media_type)."""
    p = Path(file_path)
    suffix = p.suffix.lower()

    if suffix == ".pdf":
        doc = fitz.open(str(p))
        page = doc[0]
        mat = fitz.Matrix(2, 2)  # 2x resolution
        pix = page.get_pixmap(matrix=mat)
        img_bytes = pix.tobytes("png")
        doc.close()
        return base64.standard_b64encode(img_bytes).decode("utf-8"), "image/png"

    # Direct image file
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


async def run_review(master_path: str, check_path: str) -> dict:
    """Compare master and check drawings using Claude Vision.

    Returns a dict with missing_dimensions, missing_tolerances,
    modified_values, and summary.
    """
    if not settings.ANTHROPIC_API_KEY:
        raise ValueError("ANTHROPIC_API_KEY is not configured")

    logger.info("Loading images for review: master=%s, check=%s", master_path, check_path)

    master_b64, master_media = _load_image_as_base64(master_path)
    check_b64, check_media = _load_image_as_base64(check_path)

    client = anthropic.AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)

    message = await client.messages.create(
        model="claude-sonnet-4-5-20250929",
        max_tokens=4096,
        system=SYSTEM_PROMPT,
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Here is the MASTER drawing:"},
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": master_media,
                            "data": master_b64,
                        },
                    },
                    {"type": "text", "text": "Here is the CHECK drawing:"},
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": check_media,
                            "data": check_b64,
                        },
                    },
                    {
                        "type": "text",
                        "text": (
                            "Compare the master against the check. "
                            "Report every dimension, tolerance, and callout "
                            "that is on the master but missing or different on the check. "
                            "Respond with JSON only."
                        ),
                    },
                ],
            }
        ],
    )

    raw = message.content[0].text
    logger.info("Claude review response length: %d chars", len(raw))

    # Strip markdown fences if present
    text = raw.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text[3:]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()

    try:
        result = json.loads(text)
    except json.JSONDecodeError:
        logger.error("Failed to parse Claude response as JSON: %s", text[:500])
        result = {
            "missing_dimensions": [],
            "missing_tolerances": [],
            "modified_values": [],
            "summary": "Error: Could not parse review results",
            "raw_response": raw,
        }

    # Ensure all expected keys exist
    result.setdefault("missing_dimensions", [])
    result.setdefault("missing_tolerances", [])
    result.setdefault("modified_values", [])
    if "summary" not in result:
        md = len(result["missing_dimensions"])
        mt = len(result["missing_tolerances"])
        mv = len(result["modified_values"])
        result["summary"] = (
            f"{md} dimensions missing, {mt} tolerances missing, {mv} values modified"
        )

    return result
