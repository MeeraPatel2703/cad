"""Adversarial multi-model drawing review agent.

Round 1: Claude Vision does initial comparison
Round 2: Gemini audits Claude's findings against the same images
Round 3: Claude produces final merged report incorporating Gemini's challenges
"""
from __future__ import annotations

import base64
import json
import logging
import re
import tempfile
from pathlib import Path
from typing import Dict, List, Optional

import anthropic
import cv2
import numpy as np
import google.generativeai as genai
from google.generativeai.types import HarmCategory, HarmBlockThreshold
import fitz  # PyMuPDF
from PIL import Image

from app.config import settings

logger = logging.getLogger(__name__)

# Disable safety filters — technical drawings contain legitimate engineering
# terms (e.g. "pressure vessel", "explosive forming") that cause false positives.
_SAFETY = {
    HarmCategory.HARM_CATEGORY_HATE_SPEECH: HarmBlockThreshold.BLOCK_NONE,
    HarmCategory.HARM_CATEGORY_HARASSMENT: HarmBlockThreshold.BLOCK_NONE,
    HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT: HarmBlockThreshold.BLOCK_NONE,
    HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT: HarmBlockThreshold.BLOCK_NONE,
}


def _safe_gemini_text(response) -> str:
    """Extract text from a Gemini response, handling blocked / truncated cases.

    finish_reason values:
      1 = STOP (normal), 2 = MAX_TOKENS, 3 = SAFETY, 4 = RECITATION
    When finish_reason != 1 the ``.text`` quick accessor raises.
    """
    # Fast path — normal completion
    try:
        return response.text or ""
    except Exception:
        pass

    # Dig into candidates to salvage partial content or log why it failed
    if not response.candidates:
        logger.error("Gemini returned no candidates; prompt_feedback=%s", getattr(response, "prompt_feedback", None))
        return ""

    candidate = response.candidates[0]
    reason = getattr(candidate, "finish_reason", 0)
    ratings = getattr(candidate, "safety_ratings", None)
    logger.warning("Gemini finish_reason=%s  safety_ratings=%s", reason, ratings)

    # Try to get whatever partial text exists
    try:
        if candidate.content and candidate.content.parts:
            parts = [p.text for p in candidate.content.parts if hasattr(p, "text") and p.text]
            if parts:
                joined = "".join(parts)
                logger.info("Salvaged %d chars from truncated Gemini response", len(joined))
                return joined
    except Exception as exc:
        logger.error("Could not extract partial Gemini text: %s", exc)

    return ""

# ── Shared JSON schema ──

RESULT_SCHEMA = """\
{
  "missing_dimensions": [
    {
      "value": "25.0",
      "type": "diameter",
      "location": "Mounting Hole — Bore Diameter",
      "description": "⌀25.0 H7 is present on master but absent from check",
      "master_region": {"x": 45, "y": 62, "width": 8, "height": 5},
      "check_region": null
    }
  ],
  "missing_tolerances": [
    {
      "value": "±0.1",
      "type": "tolerance",
      "location": "Base Plate — Overall Length (120mm)",
      "description": "Tolerance ±0.1 on 120mm dimension is missing from check",
      "master_region": {"x": 30, "y": 40, "width": 10, "height": 6},
      "check_region": null
    }
  ],
  "modified_values": [
    {
      "master_value": "50.0 ±0.1",
      "check_value": "50.0",
      "location": "Drive Shaft — Outer Diameter",
      "description": "Tolerance ±0.1 dropped from check drawing",
      "master_region": {"x": 55, "y": 30, "width": 8, "height": 5},
      "check_region": {"x": 54, "y": 29, "width": 8, "height": 5}
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
- DISTINGUISH LETTERS FROM NUMBERS. Engineering drawings use fonts where \
  letters and digits look very similar. Pay careful attention to these \
  commonly confused characters:
    O (letter) vs 0 (zero) — O is a section/view label, 0 is a digit.
    l (lowercase L) vs 1 (one) — use context (units, nearby digits).
    I (letter I) vs 1 (one) — I appears in labels like "I-beam", not dimensions.
    4 vs L — in technical fonts the open-top 4 looks like an L. \
      If it's inside a dimension (e.g. "14.5", "R4"), it's 4. \
      If it's a material/spec suffix (e.g. "316L"), it's L.
    3 vs 5 — "3" has TWO CURVED bumps both open on the LEFT; "5" has a \
      FLAT horizontal top bar and ONE curve open on the left at bottom. \
      Key difference: "3" is open-left at BOTH top and bottom; "5" has \
      a FLAT top. 302 is NOT 50 — do not drop digits or merge "3"→"5". \
      If the top is FLAT → 5; if the top is a CURVED bump → 3.
    4 vs 5 — "4" has ALL STRAIGHT angular strokes with an OPEN top; \
      "5" has a FLAT top bar then CURVES down-right into a rounded bottom. \
      Key test: are ALL strokes STRAIGHT with no curves? → 4. \
      Is there a CURVED bottom portion? → 5. \
      14 is NOT 15, 40 is NOT 50, 24 is NOT 25, 44 is NOT 45, \
      54 is NOT 55, 400 is NOT 500, 1400 is NOT 1500.
    5 vs 4 — same pair, opposite direction. Check: does the bottom \
      CURVE (→ 5) or stay STRAIGHT (→ 4)? 55 is NOT 54, 25 is NOT 24.
    8 vs 4 — "8" has TWO enclosed loops stacked vertically; "4" has \
      ANGULAR open strokes with no loops. If you see enclosed loops → 8. \
      "3+8 THK" is NOT "3-4 THK", "18" is NOT "14", "80" is NOT "40".
    0 vs 9 — "0" is a CLOSED oval/ellipse with NO tail; "9" has a \
      closed loop at the TOP and a DESCENDING tail/stroke at the bottom. \
      Key difference: "0" is symmetric top-to-bottom; "9" has a tail \
      dropping below the loop. 10 is NOT 19, 100 is NOT 190, 200 is NOT 290, \
      50 is NOT 59, 30 is NOT 39. If there is NO descending tail → 0.
    9 vs 0 — same pair, opposite direction. "9" has a loop + tail; \
      "0" is a simple closed oval. 90 is NOT 00, 19 is NOT 10.
    9 vs 3 — "9" has a CLOSED loop at the top with a single DESCENDING \
      tail below; "3" has TWO open curved bumps on the LEFT with NO closed \
      loop. Key test: is there a CLOSED loop at top with a tail below → 9; \
      are there TWO open bumps on the left → 3. \
      9 is NOT 3, 19 is NOT 13, 90 is NOT 30, 900 is NOT 300, 49 is NOT 43.
    9 vs 5 — "9" has a CLOSED loop at the top with a DESCENDING tail; \
      "5" has a FLAT horizontal top bar (no loop) and ONE open curve at \
      the bottom. Key test: is the top a CLOSED LOOP? → 9. Is the top a \
      FLAT horizontal bar with no enclosure? → 5. \
      9 is NOT 5, 19 is NOT 15, 90 is NOT 50, 900 is NOT 500, 49 is NOT 45, \
      29 is NOT 25, 39 is NOT 35, 109 is NOT 105.
    2 vs 5 — "2" has a CURVED top loop flowing into a FLAT horizontal \
      bottom bar (baseline stroke); "5" has a FLAT horizontal TOP bar \
      flowing into a CURVED bottom. They are essentially MIRRORED: "2" \
      curves at TOP, flat at BOTTOM; "5" is flat at TOP, curves at BOTTOM. \
      Key test: where is the curve — top or bottom? Curve at TOP → 2; \
      curve at BOTTOM → 5. \
      2 is NOT 5, 12 is NOT 15, 20 is NOT 50, 200 is NOT 500, 25 is NOT 52, \
      32 is NOT 35, 42 is NOT 45, 120 is NOT 150, 250 is NOT 550.
    0 vs 5 — "0" is a fully CLOSED oval/ellipse with NO flat edges; \
      "5" has a FLAT horizontal top bar and an OPEN curve at the bottom \
      left. Key test: is the shape fully ENCLOSED with no flat bar? → 0. \
      Is there a FLAT top bar with an open curve below? → 5. \
      0 is NOT 5, 10 is NOT 15, 20 is NOT 25, 30 is NOT 35, 40 is NOT 45, \
      100 is NOT 150, 200 is NOT 250, 300 is NOT 350, 904 is NOT 954.
    4 vs 7 — "4" has THREE strokes forming a closed angular junction \
      (vertical, horizontal crossbar, and diagonal/vertical); "7" has only \
      TWO strokes — a HORIZONTAL top bar and a single DIAGONAL descending \
      stroke. Key test: count the strokes — three strokes with a crossbar → 4; \
      two strokes (top bar + diagonal) → 7. \
      4 is NOT 7, 14 is NOT 17, 40 is NOT 70, 400 is NOT 700, 24 is NOT 27, \
      34 is NOT 37, 44 is NOT 47, 904 is NOT 907, 104 is NOT 107.
    ( (parenthesis) vs 1 — "(" is a CURVED arc that bows to the RIGHT \
      with NO vertical straight segment; "1" is a STRAIGHT vertical stroke \
      (sometimes with a small serif at top or base). Key test: is the stroke \
      CURVED along its full length? → "(". Is it STRAIGHT/vertical? → 1. \
      Parentheses always appear in PAIRS — if you see a matching ")" nearby, \
      it is "(", not "1". "(2x)" is NOT "12x)", "( )" is NOT "1 )".
    S vs 5, B vs 8, G vs 6, Z vs 2, D vs 0.
    b vs 6, q vs 9 — in some fonts these are nearly identical.
- DISTINGUISH SYMBOLS CAREFULLY. Pay attention to mathematical and \
  annotation symbols:
    + (plus) vs - (minus/dash) — "+" has a VERTICAL stroke crossing a \
      horizontal stroke; "-" is a single horizontal stroke only. \
      At small sizes or low resolution the vertical stroke of "+" can \
      be lost, making it look like "-". If an expression has a number \
      on each side (e.g. "3+8"), look carefully for a vertical bar. \
      "3+8 THK" is NOT "3-4 THK".
    × (multiply) vs + (plus) — "×" is rotated 45°; "+" is axis-aligned.
    ± vs + — "±" has a horizontal bar below the plus sign.
  When reading a value, consider the CONTEXT: dimension callouts are \
  numbers (e.g. 10.5, ⌀25, R6), while section labels are letters \
  (e.g. A-A, B, Detail C). Thread callouts mix both (e.g. M10x1.5). \
  If a character is ambiguous, look at surrounding characters and the \
  type of callout to determine if it is a letter or number.
- READ ALL DIGITS — DO NOT TRUNCATE. You MUST read the COMPLETE number \
  including ALL digits. A very common error is reading only the first \
  2 digits and ignoring trailing zeros or digits. Examples:
    "2500" is NOT "25" — the trailing "00" are part of the value.
    "1500" is NOT "15" — read ALL four digits.
    "3000" is NOT "30" — do not drop trailing zeros.
    "1250" is NOT "125" and NOT "12" — every digit matters.
    "500" is NOT "50" and NOT "5".
  When you see a number, trace along the ENTIRE string of digits until \
  you reach a non-digit character (space, leader line, or edge). Do NOT \
  stop reading early. Trailing zeros are real digits, not artifacts.
- DO NOT SPLIT, MERGE, OR DROP DIGITS. A single digit must not be misread \
  as two digits, two adjacent digits must not be merged into one, and \
  digits must not be silently dropped. Common errors:
    9 misread as "30" (the loop of 9 looks like 3, the tail like 0) — \
      49 is NOT 430, 19 is NOT 130, 92 is NOT 302.
    8 misread as "30" or "80" — count the loops carefully.
    6 misread as "0" with a tail — 46 is NOT 40.
    "302" collapsed to "50" — the "3" was misread as "5" and "02" was \
      merged into "0". This is WRONG. Read each digit individually.
    "30" collapsed to "5" or "3" — two digits must not become one.
  COUNT THE DIGITS: if a value has 3 digits (e.g. "302"), it must be \
  reported as a 3-digit number. A 3-digit number CANNOT become a 2-digit \
  number — that is always a misread. Similarly, 2 digits cannot become 1. \
  4 digits cannot become 2 — "2500" must stay "2500", not "25". \
  If the master shows "302" and you read "50" on the check, re-examine \
  the check — you likely misread it. Verify digit count matches before \
  reporting any modified value.
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
   "BOM — Item X — Description" or "General Notes — Note Y".

BOUNDING BOX REGIONS: For EACH finding, provide approximate bounding box \
coordinates as PERCENTAGE values (0-100) relative to the full image \
dimensions. These are initial hints — OCR will refine the exact position \
automatically, so focus on getting the right GENERAL AREA rather than \
pixel-perfect coordinates.
- "master_region": {"x": <left%>, "y": <top%>, "width": <w%>, "height": <h%>} \
  — approximate region on the MASTER drawing where this callout appears.
- "check_region": {"x": <left%>, "y": <top%>, "width": <w%>, "height": <h%>} \
  — approximate region on the CHECK drawing (null if missing from check).
- For missing items: set check_region to null.
- For modified values: provide BOTH master_region and check_region.
- Typical width is 5-12%, typical height is 3-8%.
- Focus on extracting DIMENSION VALUES accurately — the coordinates will \
  be refined by OCR/CNN detection automatically."""


def _load_image_as_base64(file_path: str) -> tuple[str, str, tuple[int, int]]:
    """Load a PDF or image file and return (base64_data, media_type, (width, height))."""
    p = Path(file_path)
    suffix = p.suffix.lower()

    if suffix == ".pdf":
        doc = fitz.open(str(p))
        page = doc[0]
        mat = fitz.Matrix(2, 2)
        pix = page.get_pixmap(matrix=mat)
        img_bytes = pix.tobytes("png")
        dims = (pix.width, pix.height)
        doc.close()
        return base64.standard_b64encode(img_bytes).decode("utf-8"), "image/png", dims

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

    # Get image dimensions using Pillow
    with Image.open(p) as img:
        dims = img.size  # (width, height)

    return base64.standard_b64encode(img_bytes).decode("utf-8"), media_type, dims


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


def _scale_region(region: dict | None, img_width: int, img_height: int) -> dict | None:
    """Convert a percentage-based region (0-100) to pixel coordinates.

    Clamps values to valid ranges to prevent negative coords or overflow.
    """
    if not region:
        return None
    x = max(0, int(region.get("x", 0) / 100 * img_width))
    y = max(0, int(region.get("y", 0) / 100 * img_height))
    w = max(10, int(region.get("width", 8) / 100 * img_width))
    h = max(10, int(region.get("height", 5) / 100 * img_height))
    # Ensure box stays within image bounds
    if x + w > img_width:
        w = img_width - x
    if y + h > img_height:
        h = img_height - y
    return {"x": x, "y": y, "width": w, "height": h}


def _scale_review_regions(
    result: dict,
    master_dims: tuple[int, int],
    check_dims: tuple[int, int],
) -> dict:
    """Scale all master_region/check_region fields from percentages to pixels."""
    mw, mh = master_dims
    cw, ch = check_dims

    for category in ("missing_dimensions", "missing_tolerances", "modified_values"):
        for item in result.get(category, []):
            item["master_region"] = _scale_region(item.get("master_region"), mw, mh)
            item["check_region"] = _scale_region(item.get("check_region"), cw, ch)

    return result


# ── OCR-based coordinate detection ──


def _rasterize_for_ocr(file_path: str) -> str:
    """Ensure we have a rasterized image file for OCR.

    If file_path is a PDF, rasterize it to a temp PNG and return that path.
    Otherwise return the original path.
    """
    p = Path(file_path)
    if p.suffix.lower() == ".pdf":
        doc = fitz.open(str(p))
        page = doc[0]
        pix = page.get_pixmap(matrix=fitz.Matrix(2, 2))
        tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
        pix.save(tmp.name)
        doc.close()
        logger.info("Rasterized PDF → %s for OCR", tmp.name)
        return tmp.name
    return str(p)


def _batch_ocr_detect(image_path: str) -> List[Dict]:
    """Run OCR once on the full image, return all detected text with coordinates."""
    import pytesseract
    from pytesseract import Output

    img = cv2.imread(image_path, cv2.IMREAD_GRAYSCALE)
    if img is None:
        logger.warning("Could not load image for OCR: %s", image_path)
        return []

    img_h, img_w = img.shape
    detections = []

    # Run Tesseract with sparse text mode
    for psm in (11, 6):
        try:
            ocr_data = pytesseract.image_to_data(
                img, output_type=Output.DICT, config=f"--psm {psm}"
            )
            for i in range(len(ocr_data["text"])):
                text = ocr_data["text"][i].strip()
                conf = ocr_data["conf"][i]
                if not text or conf < 30:
                    continue
                detections.append({
                    "text": text,
                    "confidence": conf / 100.0,
                    "left": ocr_data["left"][i],
                    "top": ocr_data["top"][i],
                    "width": ocr_data["width"][i],
                    "height": ocr_data["height"][i],
                    "img_w": img_w,
                    "img_h": img_h,
                })
        except Exception as exc:
            logger.warning("Tesseract psm %d failed: %s", psm, exc)

    logger.info("Tesseract detected %d text regions on %s", len(detections), image_path)
    return detections


def _batch_cnn_detect(image_path: str) -> List[Dict]:
    """Run EasyOCR (CNN) once on the full image, return all detected text."""
    try:
        from app.agents.ocr_engine import extract_dimensions_with_cnn
        cnn_dims = extract_dimensions_with_cnn(image_path)

        img = cv2.imread(image_path)
        if img is None:
            return []
        img_h, img_w = img.shape[:2]

        detections = []
        for dim in cnn_dims:
            bbox = dim.get("bbox", {})
            detections.append({
                "text": dim.get("text", ""),
                "value": dim.get("value"),
                "confidence": dim.get("confidence", 0.5),
                "left": bbox.get("x", 0),
                "top": bbox.get("y", 0),
                "width": bbox.get("width", 50),
                "height": bbox.get("height", 30),
                "img_w": img_w,
                "img_h": img_h,
            })
        logger.info("EasyOCR detected %d text regions on %s", len(detections), image_path)
        return detections
    except Exception as exc:
        logger.warning("CNN detection failed: %s", exc)
        return []


def _find_value_in_detections(
    dimension_value: str,
    detections: List[Dict],
    ai_region: Optional[Dict] = None,
) -> Optional[Dict]:
    """Search OCR detections for a dimension value, return percentage-based region."""
    if not detections:
        return None

    # Build search variants for the value
    search_variants = [dimension_value]
    try:
        fval = float(dimension_value)
        if fval == int(fval):
            search_variants.append(str(int(fval)))
        search_variants.append(f"{fval:.1f}")
        search_variants.append(f"{fval:.2f}")
    except (ValueError, TypeError):
        pass
    # Also try without special chars (±, Ø, etc.)
    cleaned = re.sub(r"[±Øø⌀°]", "", dimension_value).strip()
    if cleaned and cleaned not in search_variants:
        search_variants.append(cleaned)

    best_match = None
    best_score = 0  # Combined confidence + proximity to AI estimate

    for det in detections:
        text = det["text"]
        conf = det["confidence"]

        matched = False
        for variant in search_variants:
            if variant in text:
                matched = True
                break

        if not matched:
            continue

        img_w = det["img_w"]
        img_h = det["img_h"]

        # Convert to percentage-based region
        cx_pct = (det["left"] + det["width"] / 2) / img_w * 100
        cy_pct = (det["top"] + det["height"] / 2) / img_h * 100
        w_pct = max(det["width"] * 2 / img_w * 100, 3)  # padding
        h_pct = max(det["height"] * 2 / img_h * 100, 2)

        # Score: confidence + proximity bonus if near AI estimate
        score = conf
        if ai_region:
            ai_cx = ai_region.get("x", 50) + ai_region.get("width", 10) / 2
            ai_cy = ai_region.get("y", 50) + ai_region.get("height", 5) / 2
            dist = ((cx_pct - ai_cx) ** 2 + (cy_pct - ai_cy) ** 2) ** 0.5
            # Bonus for being close to AI estimate (within 20% = full bonus)
            proximity_bonus = max(0, 0.3 * (1 - dist / 30))
            score += proximity_bonus

        if score > best_score:
            best_score = score
            best_match = {
                "x": cx_pct - w_pct / 2,
                "y": cy_pct - h_pct / 2,
                "width": w_pct,
                "height": h_pct,
            }

    return best_match


def _refine_regions_with_ocr(
    result: dict,
    master_path: str,
    check_path: str,
) -> dict:
    """Refine AI-estimated regions using OCR + CNN coordinate detection.

    Strategy: run OCR/CNN once per image (batch), then match each finding.
    Falls back to AI estimation if OCR/CNN can't find a value.
    """
    # Rasterize if PDF
    master_ocr_path = _rasterize_for_ocr(master_path)
    check_ocr_path = _rasterize_for_ocr(check_path)

    # Batch OCR: run once per image
    master_tess = _batch_ocr_detect(master_ocr_path)
    check_tess = _batch_ocr_detect(check_ocr_path)

    # Batch CNN: only if USE_CNN_OCR is enabled
    master_cnn = []
    check_cnn = []
    if settings.USE_CNN_OCR:
        master_cnn = _batch_cnn_detect(master_ocr_path)
        check_cnn = _batch_cnn_detect(check_ocr_path)

    # Combine Tesseract + CNN detections per image
    master_all = master_tess + master_cnn
    check_all = check_tess + check_cnn

    stats = {"ocr_detected": 0, "cnn_detected": 0, "ai_fallback": 0}

    for category in ("missing_dimensions", "missing_tolerances", "modified_values"):
        for item in result.get(category, []):
            # Determine the value to search for on each drawing
            if category == "modified_values":
                master_val = str(item.get("master_value", ""))
                check_val = str(item.get("check_value", ""))
            else:
                master_val = str(item.get("value", ""))
                check_val = None  # Missing from check

            # Refine master_region
            ai_master = item.get("master_region")
            if master_val:
                ocr_match = _find_value_in_detections(
                    master_val, master_tess, ai_master
                )
                if ocr_match:
                    item["master_region"] = ocr_match
                    item["master_detection_method"] = "ocr_detected"
                    stats["ocr_detected"] += 1
                else:
                    # Try CNN
                    cnn_match = _find_value_in_detections(
                        master_val, master_cnn, ai_master
                    )
                    if cnn_match:
                        item["master_region"] = cnn_match
                        item["master_detection_method"] = "cnn_detected"
                        stats["cnn_detected"] += 1
                    else:
                        item["master_detection_method"] = "ai_fallback"
                        stats["ai_fallback"] += 1

            # Refine check_region
            ai_check = item.get("check_region")
            if check_val and ai_check is not None:
                ocr_match = _find_value_in_detections(
                    check_val, check_tess, ai_check
                )
                if ocr_match:
                    item["check_region"] = ocr_match
                    item["check_detection_method"] = "ocr_detected"
                else:
                    cnn_match = _find_value_in_detections(
                        check_val, check_cnn, ai_check
                    )
                    if cnn_match:
                        item["check_region"] = cnn_match
                        item["check_detection_method"] = "cnn_detected"
                    else:
                        item["check_detection_method"] = "ai_fallback"
            else:
                item["check_detection_method"] = "none"

            # Assign coordinate confidence
            method_conf = {
                "cnn_detected": 0.95,
                "ocr_detected": 0.85,
                "ai_fallback": 0.5,
                "none": 0.3,
            }
            m_conf = method_conf.get(item.get("master_detection_method", "ai_fallback"), 0.5)
            c_conf = method_conf.get(item.get("check_detection_method", "none"), 0.3)
            item["coordinate_confidence"] = round((m_conf + c_conf) / 2, 2)

    logger.info(
        "Region refinement: %d OCR-detected, %d CNN-detected, %d AI-fallback",
        stats["ocr_detected"], stats["cnn_detected"], stats["ai_fallback"],
    )

    # Clean up temp files
    if master_ocr_path != master_path:
        Path(master_ocr_path).unlink(missing_ok=True)
    if check_ocr_path != check_path:
        Path(check_ocr_path).unlink(missing_ok=True)

    return result


# ── Round 1: Claude initial review ──

async def _claude_initial_review(
    client: anthropic.AsyncAnthropic,
    master_b64: str, master_media: str,
    check_b64: str, check_media: str,
) -> tuple[dict | None, str]:
    """Claude does the first pass comparison. Returns (parsed_dict, raw_text)."""
    logger.info("Round 1: Claude initial review")

    message = await client.messages.create(
        model="claude-opus-4-6",
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
                            "For EACH finding, include master_region and check_region "
                            "bounding boxes as percentage coordinates (0-100) showing "
                            "where the dimension appears on each drawing. Set "
                            "check_region to null for missing items.\n\n"
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
6. For EACH finding, include master_region and check_region bounding \
   boxes as percentage coordinates (0-100) showing where the dimension \
   appears on each drawing. Set check_region to null for missing items.

Respond with JSON only:
{RESULT_SCHEMA}"""

    content_parts = [
        {"inline_data": {"mime_type": master_media, "data": master_b64}},
        "MASTER drawing (above)",
        {"inline_data": {"mime_type": check_media, "data": check_b64}},
        "CHECK drawing (above)",
        prompt,
    ]

    try:
        response = await model.generate_content_async(
            content_parts,
            generation_config=genai.GenerationConfig(
                temperature=0.1,
                max_output_tokens=32768,
            ),
            safety_settings=_SAFETY,
        )
    except Exception as exc:
        logger.error("Gemini audit API call failed: %s", exc)
        return None, f"[Gemini error: {exc}]"

    raw = _safe_gemini_text(response)
    if not raw:
        logger.error("Gemini round 2 returned no usable text")
        return None, ""
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
        model="claude-opus-4-6",
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
                            "STEP 6 — BOUNDING BOXES:\n"
                            "For EACH finding, include master_region and check_region "
                            "bounding boxes as percentage coordinates (0-100) showing "
                            "where the dimension appears on each drawing. Set "
                            "check_region to null for missing items.\n\n"
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
    Round 3: Claude produces final merged report incorporating Gemini's challenges
    """
    if not settings.ANTHROPIC_API_KEY:
        raise ValueError("ANTHROPIC_API_KEY is not configured")
    if not settings.GOOGLE_API_KEY:
        raise ValueError("GOOGLE_API_KEY is not configured")

    master_b64, master_media, master_dims = _load_image_as_base64(master_path)
    check_b64, check_media, check_dims = _load_image_as_base64(check_path)

    client = anthropic.AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)

    # Round 1: Claude
    claude_result, claude_raw = await _claude_initial_review(
        client, master_b64, master_media, check_b64, check_media,
    )

    # Round 2: Gemini audits (non-fatal — if Gemini fails we continue with Claude only)
    gemini_result, gemini_raw = await _gemini_audit(
        master_b64, master_media, check_b64, check_media,
        claude_raw,
    )

    if gemini_result is None and not gemini_raw:
        logger.warning("Gemini audit returned nothing — proceeding with Claude-only results")

    # Round 3: Claude final merge
    final_result, final_raw = await _claude_final_merge(
        client, master_b64, master_media, check_b64, check_media,
        claude_raw, gemini_raw or "[Gemini audit unavailable — rely on your own Round 1 findings]",
    )

    if final_result is None:
        # Fallback chain: Gemini → Claude round 1 → empty
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

    # Refine AI-estimated regions using OCR + CNN detection
    _refine_regions_with_ocr(final_result, master_path, check_path)

    # Scale percentage-based regions to pixel coordinates
    _scale_review_regions(final_result, master_dims, check_dims)

    return final_result
