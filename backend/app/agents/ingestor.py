"""Ingestor Agent – Spatial Architect for mechanical drawing extraction.

Converts "dumb pixels" into a structured Dynamic Machine State (DMS) JSON.
Implements:
1. Grid-based zone segmentation for coordinate mapping
2. BOM-first parsing to identify "Actors" (entities)
3. Entity binding - links dimensions to parent entities
"""
from __future__ import annotations

import asyncio
import base64
import io
import json
import logging
import re
from pathlib import Path
from typing import Optional, List, Dict, Tuple

import cv2
import numpy as np
from PIL import Image
from PyPDF2 import PdfReader
import google.generativeai as genai
from google.api_core.exceptions import ResourceExhausted

from app.config import settings
from app.agents.state import AuditState, MachineState

logger = logging.getLogger(__name__)

# Retry configuration for rate limits
MAX_RETRIES = 5
INITIAL_BACKOFF = 30  # seconds

# Grid configuration for spatial mapping
GRID_ROWS = 6  # A-F zones (common in engineering drawings)
GRID_COLS = 8  # 1-8 zones

EXTRACTION_PROMPT = """You are an expert mechanical engineering drawing reader.
Extract ALL dimensions and parts from this drawing.

IMPORTANT: Be thorough - extract EVERY dimension you can see. Missing a dimension is worse than misreading one.

COLOR NOTE: The image has been preprocessed to normalize ALL text colors (black, blue, red, etc.) into high-contrast format. Text that appears in different colors in the original drawing will all appear similar after preprocessing. Focus on the VALUES themselves, not the color of the text.

VALUE EXTRACTION - CRITICAL:
- Extract ONLY numeric values for dimensions
- Common OCR errors to avoid:
  * Letter O vs number 0: "O.5" is WRONG, should be "0.5"
  * Letter l/I vs number 1: "l.5" is WRONG, should be "1.5"
  * Letter S vs number 5: Check context carefully
- If you see what looks like a letter in a dimension, it's probably a number
- Examples of CORRECT extraction:
  * "Ø25.4" → value: 25.4 (not "O25.4" or "Ø25.4")
  * "0.05" → value: 0.05 (not "O.05")
  * "12.5" → value: 12.5 (not "l2.5" or "I2.5")

COORDINATE SYSTEM - CRITICAL FOR PRECISE BALLOON PLACEMENT:
Express coordinates as PERCENTAGES (0-100) of the IMAGE dimensions:
- x: 0 = left edge of image, 100 = right edge of image
- y: 0 = top edge of image, 100 = bottom edge of image

PRECISION REQUIREMENTS:
- Place coordinates at the EXACT CENTER of the dimension NUMBER/TEXT (not the extension lines)
- For "45.0 mm" dimension text, put coordinates at the center of "45.0"
- For diameter symbols like "Ø25", put coordinates at the center of the number
- For tolerances like "10 +0.1/-0.05", put coordinates at the main value "10"
- Be as precise as possible - estimate to one decimal place (e.g., 23.5, not just 24)

VISUAL GUIDE:
- Mentally divide the image into a 100x100 grid
- Find exactly where the dimension text sits on this grid
- A dimension at 1/4 from left and 1/3 from top = {{"x": 25.0, "y": 33.3}}

Return JSON with this structure:

1. "dimensions" array - for each dimension callout:
   - value: the numeric value (e.g., 25.0)
   - unit: "mm" or "in"
   - coordinates: location as PERCENTAGE of image size (x: 0-100, y: 0-100)
   - feature_type: what it measures (diameter, length, thickness, etc.)
   - tolerance_class: if shown (H7, g6, etc.)
   - upper_tol: upper tolerance if shown
   - lower_tol: lower tolerance if shown
   - item_number: BOM reference number if linked to a balloon

2. "part_list" array - from the Bill of Materials/Part List:
   - item_number: the reference number
   - description: part name
   - material: material specification
   - quantity: number required
   - weight: weight if shown
   - weight_unit: kg or lb

3. "zones" array - drawing views found:
   - name: "Top View", "Section A-A", etc.
   - grid_ref: location reference
   - features: list of features in that view

4. "gdt_callouts" array - GD&T symbols:
   - symbol: the GD&T symbol type
   - value: tolerance value
   - datum: datum reference letters
   - coordinates: location as PERCENTAGE (x: 0-100, y: 0-100)

5. "title_block" object:
   - title: drawing title
   - drawing_number: part/drawing number
   - revision: revision letter
   - material: general material spec
   - tolerance_general: default tolerance

FONT RECOGNITION - TECHNICAL DRAWING FONTS:
Dimension text in this drawing likely uses technical fonts (DIN 1451, Helvetica, or Roboto).

CRITICAL CHARACTER DISTINCTIONS:
- Zero vs Letter O: In dimension values it is ALWAYS zero. "0.5" never "O.5"
- One vs Letter I/l: In dimension values it is ALWAYS one. "12.5" never "I2.5" or "l2.5"
- 5 vs S: In dimensions it is ALWAYS 5. "45.5" never "4S.5"
- 6 vs b: In dimensions it is ALWAYS 6
- 8 vs B: In dimensions it is ALWAYS 8
- 2 vs Z: In dimensions it is ALWAYS 2

CONTEXT CLUES:
- Characters inside a DIMENSION (number with unit like "mm") must be digits (0-9)
- Characters in TEXT labels ("ID", "OD", "BORE") are letters
- Decimal points are ALWAYS surrounded by digits

FONT-SPECIFIC TIPS:
- Helvetica has very tight character spacing — do not merge adjacent digits
- DIN 1451 has geometric shapes — circles are always zero, not letter O
- Roboto has clear distinction between 0 and O — trust what you see

COMMON EXTRACTION ERRORS TO AVOID:

1. Decimal Point Errors:
   - WRONG: Reading "0.5" as "0 5" or "O.5"
   - WRONG: Reading "4.79" as "4 79" or "479"
   - RIGHT: Always include the decimal point in your extracted value

2. Similar Number Confusion (ESPECIALLY AT SMALL SIZES):
   - WRONG: Confusing "0.05" with "0.5" (10x difference)
   - WRONG: Confusing "45.0" with "48.0"
   - RIGHT: Read each digit carefully, especially after decimal points

   CRITICAL DIGIT PAIRS — these are the most commonly confused at small font sizes:
   - 3 vs 4: "3" has TWO CURVED bumps open on the left; "4" has an ANGULAR junction open at bottom
   - 3 vs 8: "3" is open on the left; "8" is CLOSED on both sides
   - 6 vs 8: "6" has ONE enclosed loop at bottom; "8" has TWO enclosed loops
   - 1 vs 7: "1" is a straight vertical stroke; "7" has a HORIZONTAL bar at top
   - 5 vs 6: "5" has a flat top and open bottom curve; "6" has a curved top flowing into closed bottom
   - 4 vs 9: "4" has angular strokes meeting at a junction; "9" has a closed loop at top

   DISAMBIGUATION STRATEGY for small text:
   - Count enclosed loops: 0 has 1 loop, 8 has 2 loops, 4 has 0 loops
   - Check if top is FLAT (4, 5, 7) or CURVED (3, 6, 8, 9)
   - Check if shape is OPEN on left side (3) or CLOSED (8)
   - When in doubt between 3 and 4: if the strokes are CURVED → 3; if ANGULAR → 4

3. Unit Extraction:
   - WRONG: Including unit in value: value: "25.4mm"
   - RIGHT: Separate value and unit: value: 25.4, unit: "mm"

4. Tolerance Extraction:
   - For "10 +0.1/-0.05": value: 10, upper_tol: 0.1, lower_tol: -0.05
   - For "25 ±0.05": value: 25, upper_tol: 0.05, lower_tol: -0.05
   - For "H7" tolerance class: include in tolerance_class field, not in value

5. Diameter vs Radius:
   - "Ø25.4" means DIAMETER = 25.4, feature_type: "diameter"
   - "R12.5" means RADIUS = 12.5, feature_type: "radius"
   - Never confuse these two

6. Multiple Views:
   - The SAME dimension appearing in different views should have the SAME value
   - If you see "45.0" in top view and "45.0" in side view, extract it ONCE
   - Cross-check your extractions across views for consistency

SMALL TEXT EXTRACTION - CRITICAL:
This drawing may contain very small dimension text (font size 4-8pt).

SMALL TEXT GUIDELINES:
- ZOOM IN mentally on small text - examine each digit individually
- Small decimal points are easy to miss: "0.05" not "0 05" or "005"
- In small fonts, "0" and "O" look nearly identical - context matters:
  * In dimension values: always "0" (zero)
  * In words like "OD", "BORE": always "O" (letter)
- Small "1" can look like "l" or "I" - in dimensions it's always "1"
- Double-check tolerances - they're often in smaller font than main dimension
  * "10 +0.1/-0.05" - the "+0.1/-0.05" is typically 60-70% the size of "10"

VERY SMALL DIGIT READING (< 8pt font) — READ EACH DIGIT STROKE BY STROKE:
- For EACH digit in a small number, ask: what strokes make up this character?
  * "3" = two curved arcs, both open on the left side
  * "4" = two straight lines meeting at an angle, with a horizontal crossbar
  * "8" = two stacked closed loops
  * "6" = one closed loop at bottom with a curved tail going up-left
  * "9" = one closed loop at top with a tail going down
  * "1" = single vertical line (possibly with serifs)
  * "7" = horizontal line at top with diagonal going down-left
- Example: "34.5" vs "44.5" — check the first digit: is it curved (3) or angular (4)?
- Example: "0.34" vs "0.84" — check the second digit: open left side (3) or two loops (8)?
- Example: "13.7" vs "17.7" — is the second digit curved bumps (3) or horizontal bar on top (7)?

QUALITY CHECK FOR SMALL TEXT:
□ Did I extract the tolerance values? (often smallest text)
□ Did I preserve decimal points in small numbers? (0.05 not 005)
□ Did I read each digit individually in small text? (especially 3/4/8)
□ Did I check superscripts/subscripts? (sometimes used for reference numbers)
□ Did I read dimension units even if they're tiny? (mm, in, etc.)

READING LETTERS ACCURATELY - CRITICAL:
Technical drawings contain BOTH numbers (in dimensions) AND letters (in labels, tolerances,
materials, datums, part descriptions, revision marks). You MUST read letters with the same
care as numbers. A misread letter is just as bad as a misread digit.

WHERE LETTERS ARE EXPECTED (do NOT convert to numbers here):
1. Tolerance classes: "H7", "g6", "h6", "js15", "N9" — these are ISO letter+number codes
   - The letter indicates the deviation zone: H, h, G, g, F, f, E, e, D, d, JS, js, K, k, M, m, N, n, P, p, R, r, S, s, T, t, U, u, X, x, Z, z
   - CRITICAL: "H7" ≠ "h7" (uppercase H = hole, lowercase h = shaft). Case matters!
   - WRONG: Reading "H7" as "H1" or "H7" as "117"
   - WRONG: Reading "g6" as "96" or "q6"

2. GD&T datum references: single uppercase letters "A", "B", "C", etc.
   - WRONG: Reading datum "B" as "8", datum "D" as "0", datum "G" as "6"
   - RIGHT: Datum letters are ALWAYS uppercase single letters in a box/triangle
   - Common misreads: A↔4, B↔8, D↔0, G↔6, I↔1, O↔0, S↔5, Z↔2

3. Material specifications: "AISI 316L", "Al 6061-T6", "SS304", "C45 Steel"
   - These mix letters and numbers — read EACH character in context
   - WRONG: Reading "316L" as "3161" (L is a letter, not the number 1)
   - WRONG: Reading "AISI" as "A1S1" (the I's are letters, not the number 1)
   - WRONG: Reading "Al 6061" as "A1 606I" (Al = Aluminium abbreviation)

4. Part descriptions: "BEARING", "BOLT", "SHAFT", "O-RING", "WASHER"
   - WRONG: Reading "BOLT" as "B0LT" or "80LT"
   - WRONG: Reading "BEARING" as "8EARING"
   - WRONG: Reading "O-RING" as "0-RING" (O is for the shape, it's a letter)

5. Revision letters: "Rev A", "Rev B", "Rev C"
   - WRONG: Reading "Rev B" as "Rev 8"
   - These are ALWAYS sequential letters (A, B, C, D...)

6. Drawing numbers: "DWG-A1234", "PT-2024-B"
   - Mix of letters and numbers — read each character individually

7. Section/view labels: "Section A-A", "Detail B", "View C-C"
   - ALWAYS letters, never numbers

LETTER CONFUSION PAIRS AT SMALL FONT SIZES:
| Letter | Confused with | How to distinguish |
|--------|--------------|-------------------|
| B | 8 | B has flat vertical back on LEFT; 8 has no flat side |
| D | 0 | D has flat vertical back on LEFT; 0 is fully round |
| G | 6 | G has a horizontal bar on the RIGHT side; 6 does not |
| I | 1 | I often has top and bottom serifs; 1 is plain vertical |
| O | 0 | In TEXT context it is always the letter O |
| S | 5 | S has TWO curves (top and bottom); 5 has a flat top |
| Z | 2 | Z has TWO horizontal bars (top and bottom); 2 has only one at bottom |
| l (ell) | 1 | lowercase L is taller than digits in most fonts |
| q | 9 | q has a DESCENDER going below the baseline; 9 does not |

RULE: Determine the CONTEXT first, then read the character:
- Inside a dimension callout (between extension lines) → characters are DIGITS
- Inside a tolerance box (e.g., H7) → first chars are LETTERS, last chars are DIGITS
- Inside a feature control frame → datum references are LETTERS
- Inside the BOM/title block/notes → characters could be either, read carefully

QUALITY CHECKLIST - Before returning your response:
- Did I extract ALL visible dimensions? (missing dimensions is worse than misreading)
- Are all decimal points preserved? (4.79 not 479 or 4 79)
- Are values separated from units? (25.4 and "mm", not "25.4mm")
- Are coordinates at the dimension NUMBER, not the extension lines?
- Did I cross-check dimensions that appear in multiple views?
- Did I preserve LETTERS in tolerance classes? (H7 not 117, g6 not 96)
- Did I preserve LETTERS in datum references? (A not 4, B not 8)
- Did I preserve LETTERS in material specs? (316L not 3161, AISI not A1S1)
- Did I preserve LETTERS in part descriptions? (BEARING not 8EARING)

Extract as many dimensions as possible. Every measurement visible should be captured."""

RESCAN_PROMPT = """You previously extracted data from this drawing but some values were suspect.
Focus specifically on the highlighted region and re-extract dimensions carefully.
Apply higher scrutiny to numerical values.

IMPORTANT: Maintain the same entity binding - link dimensions to item_numbers from the original BOM.
Return the same JSON format as before but only for items in this region.
Include the grid_ref for each dimension based on its position.
"""


def _configure_genai():
    genai.configure(api_key=settings.GOOGLE_API_KEY)


def _load_images(
    file_path: str,
    crop_region: Optional[Dict] = None,
    target_dpi: int = 300,
) -> Tuple[List, Tuple[int, int]]:
    """Load drawing file and return list of image parts + dimensions.

    Returns dimensions matching the 2x rendered size used by the frontend.
    target_dpi controls upscaling aggressiveness (default 300, use 400+ for small text).
    """
    path = Path(file_path)
    images = []
    img_size = (0, 0)

    if path.suffix.lower() == ".pdf":
        # For PDF, pass bytes directly to Gemini
        with open(path, "rb") as f:
            pdf_bytes = f.read()
        images = [{"mime_type": "application/pdf", "data": base64.b64encode(pdf_bytes).decode()}]

        # Get actual PDF dimensions and calculate 2x rendered size (matching frontend)
        try:
            import fitz  # PyMuPDF
            doc = fitz.open(path)
            page = doc[0]
            # Frontend renders at 2x scale
            img_size = (int(page.rect.width * 2), int(page.rect.height * 2))
            doc.close()
        except Exception:
            # Fallback to A4 at 2x if PyMuPDF fails
            img_size = (2384, 1684)  # Landscape A4 at 2x
    else:
        img = Image.open(path)

        # Upscale for small text before any other processing
        img = _upscale_for_small_text(img, target_dpi=target_dpi)

        if crop_region:
            x1, y1 = crop_region.get("x1", 0), crop_region.get("y1", 0)
            x2, y2 = crop_region.get("x2", img.width), crop_region.get("y2", img.height)
            img = img.crop((x1, y1, x2, y2))

        # Only downscale if absolutely massive (to avoid memory issues)
        MAX_DIMENSION = 4096
        if img.width > MAX_DIMENSION or img.height > MAX_DIMENSION:
            scale = MAX_DIMENSION / max(img.width, img.height)
            new_w = int(img.width * scale)
            new_h = int(img.height * scale)
            logger.warning(f"Image extremely large, downscaling from {img.width}x{img.height} to {new_w}x{new_h}")
            img = img.resize((new_w, new_h), Image.Resampling.LANCZOS)

        img_size = (img.width, img.height)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        images.append({"mime_type": "image/png", "data": base64.b64encode(buf.getvalue()).decode()})

    return images, img_size


def _compute_grid_ref(x: int, y: int, img_width: int, img_height: int) -> str:
    """Compute grid reference (e.g., 'C4') from pixel coordinates."""
    if img_width == 0 or img_height == 0:
        return "A1"

    col = min(int(x / img_width * GRID_COLS), GRID_COLS - 1)
    row = min(int(y / img_height * GRID_ROWS), GRID_ROWS - 1)

    row_letter = chr(ord('A') + row)
    col_number = col + 1

    return f"{row_letter}{col_number}"


def _build_entity_registry(part_list: List[Dict]) -> Dict[str, Dict]:
    """Build a lookup dictionary of entities from BOM."""
    registry = {}
    for part in part_list:
        item_num = str(part.get("item_number", ""))
        if item_num:
            registry[item_num] = {
                "description": part.get("description", ""),
                "material": part.get("material", ""),
                "quantity": part.get("quantity", 1),
                "weight": part.get("weight"),
                "weight_unit": part.get("weight_unit", "kg"),
            }
    return registry


def _scale_coordinates(coords: Dict, img_width: int, img_height: int) -> Dict:
    """
    Convert percentage-based coordinates (0-100) to actual pixel coordinates.
    Handles different drawing scales by using percentage-based positioning.
    """
    if not coords:
        return {"x": 0, "y": 0}

    x = coords.get("x", 0)
    y = coords.get("y", 0)

    # Handle None values
    if x is None:
        x = 0
    if y is None:
        y = 0

    # Convert to float for calculation
    try:
        x = float(x)
        y = float(y)
    except (TypeError, ValueError):
        return {"x": 0, "y": 0}

    # Detect if coordinates are percentages (0-100 range typical)
    # vs already being pixel values (would be in the hundreds/thousands)
    if 0 <= x <= 100 and 0 <= y <= 100:
        # Percentage - scale to actual pixels based on this image's dimensions
        x = int((x / 100.0) * img_width)
        y = int((y / 100.0) * img_height)
    else:
        # Already pixel values - just ensure they're integers
        x = int(x)
        y = int(y)

    # Clamp to image bounds to prevent out-of-bounds balloons
    x = max(0, min(x, img_width - 1))
    y = max(0, min(y, img_height - 1))

    return {"x": x, "y": y}


def _validate_and_adjust_coordinates(
    dimensions: List[Dict],
    file_path: str,
    img_size: Tuple[int, int]
) -> List[Dict]:
    """
    Validate that balloon coordinates land on actual drawing content.
    If a coordinate is on empty/white space, search nearby for content.
    """
    path = Path(file_path)
    img_width, img_height = img_size

    # Load image for pixel analysis
    try:
        if path.suffix.lower() == ".pdf":
            import fitz
            png_path = path.with_suffix('.png')
            if not png_path.exists():
                doc = fitz.open(str(path))
                page = doc[0]
                mat = fitz.Matrix(2, 2)
                pix = page.get_pixmap(matrix=mat)
                pix.save(str(png_path))
                doc.close()
            img = Image.open(png_path).convert('RGB')
        else:
            img = Image.open(path).convert('RGB')
    except Exception as e:
        logger.warning(f"Could not load image for coordinate validation: {e}")
        return dimensions

    actual_width, actual_height = img.size

    def is_on_content(x: int, y: int, radius: int = 10) -> bool:
        """Check if pixel area has drawing content (not white/empty)."""
        non_white = 0
        samples = 0
        for dx in range(-radius, radius + 1, 3):
            for dy in range(-radius, radius + 1, 3):
                sx = max(0, min(x + dx, actual_width - 1))
                sy = max(0, min(y + dy, actual_height - 1))
                r, g, b = img.getpixel((sx, sy))
                samples += 1
                if r < 240 or g < 240 or b < 240:
                    non_white += 1
        return (non_white / samples) >= 0.15 if samples > 0 else False

    def find_nearest_content(x: int, y: int, max_search: int = 100) -> Tuple[int, int]:
        """Search in expanding circles to find nearest content."""
        # Search in a spiral pattern
        for radius in range(10, max_search, 10):
            # Check 8 directions at this radius
            for angle_step in range(8):
                import math
                angle = (angle_step / 8) * 2 * math.pi
                nx = int(x + radius * math.cos(angle))
                ny = int(y + radius * math.sin(angle))
                nx = max(0, min(nx, actual_width - 1))
                ny = max(0, min(ny, actual_height - 1))
                if is_on_content(nx, ny, radius=5):
                    return nx, ny
        return x, y  # No content found, return original

    adjusted_count = 0
    for dim in dimensions:
        coords = dim.get("coordinates", {})
        if not coords:
            continue

        x = coords.get("x", 0)
        y = coords.get("y", 0)

        # Scale coordinates to actual image size if needed
        if actual_width != img_width or actual_height != img_height:
            x = int(x * actual_width / img_width) if img_width > 0 else x
            y = int(y * actual_height / img_height) if img_height > 0 else y

        x = max(0, min(int(x), actual_width - 1))
        y = max(0, min(int(y), actual_height - 1))

        if not is_on_content(x, y):
            # Find nearest content
            new_x, new_y = find_nearest_content(x, y)
            if (new_x, new_y) != (x, y):
                # Scale back to original coordinate system
                if actual_width != img_width:
                    new_x = int(new_x * img_width / actual_width)
                if actual_height != img_height:
                    new_y = int(new_y * img_height / actual_height)
                dim["coordinates"] = {"x": new_x, "y": new_y}
                dim["coordinate_adjusted"] = True
                adjusted_count += 1

    if adjusted_count > 0:
        logger.info(f"Adjusted {adjusted_count} balloon coordinates to valid content areas")

    return dimensions


def _upscale_for_small_text(pil_image: Image.Image, target_dpi: int = 300) -> Image.Image:
    """Upscale image to make small text readable for OCR.

    Small fonts (< 8pt at 150 DPI) need upscaling to be OCR-readable.
    Target: Dimension text should be at least 30-40 pixels tall.
    For very small text (< 10px chars), target 400+ DPI.
    """
    width, height = pil_image.size

    # Estimate current DPI from image dimensions
    # Standard engineering drawing sizes:
    #   A4 (210x297mm) at 150 DPI ≈ 1240x1754
    #   A3 (297x420mm) at 150 DPI ≈ 1754x2480
    #   A4 at 200 DPI ≈ 1654x2339
    estimated_dpi = 150
    if width > 3000 or height > 3000:
        estimated_dpi = 200
    if width > 4500 or height > 4500:
        estimated_dpi = 300  # Already high-res scan

    if estimated_dpi < target_dpi:
        scale_factor = target_dpi / estimated_dpi
        new_width = int(width * scale_factor)
        new_height = int(height * scale_factor)

        logger.info(
            f"Upscaling for small text: {width}x{height} → {new_width}x{new_height} "
            f"(estimated {estimated_dpi} DPI → {target_dpi} DPI, scale: {scale_factor:.2f}x)"
        )

        upscaled = pil_image.resize(
            (new_width, new_height),
            Image.Resampling.LANCZOS,
        )
        return upscaled

    logger.info(f"Image resolution sufficient ({estimated_dpi} DPI), no upscaling needed")
    return pil_image


def _detect_small_text(img_array: np.ndarray) -> Dict:
    """Detect if image contains very small text that may be hard to read.

    Returns severity info and recommended DPI:
    - Very small (< 10px median): target 450 DPI — digits like 3/4/8 blur together
    - Small (< 15px median): target 400 DPI — fine features lost
    - Moderate (< 20px median): target 350 DPI — standard small text
    """
    if len(img_array.shape) == 3:
        gray = cv2.cvtColor(img_array, cv2.COLOR_RGB2GRAY)
    else:
        gray = img_array

    _, binary = cv2.threshold(gray, 127, 255, cv2.THRESH_BINARY_INV)
    contours, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    char_heights = []
    for contour in contours:
        area = cv2.contourArea(contour)
        if 10 < area < 5000:  # Likely text
            _, _, w, h = cv2.boundingRect(contour)
            # Filter for plausible text aspect ratios (not too wide/tall)
            if 0.2 < (w / max(h, 1)) < 3.0:
                char_heights.append(h)

    if not char_heights:
        return {"has_small_text": False, "target_dpi": 300}

    median_height = float(np.median(char_heights))
    min_height = float(np.min(char_heights))
    p10_height = float(np.percentile(char_heights, 10))  # 10th percentile — smallest text

    has_small_text = median_height < 20 or p10_height < 12

    # Determine target DPI based on severity
    if p10_height < 8:
        target_dpi = 450  # Very small — 3/4/8 indistinguishable
        severity = "very_small"
    elif p10_height < 12:
        target_dpi = 400  # Small — fine curves lost
        severity = "small"
    elif median_height < 20:
        target_dpi = 350  # Moderate
        severity = "moderate"
    else:
        target_dpi = 300
        severity = "normal"

    result = {
        "has_small_text": has_small_text,
        "severity": severity,
        "median_char_height": median_height,
        "min_char_height": min_height,
        "p10_char_height": p10_height,
        "target_dpi": target_dpi,
        "recommendation": "upscale" if has_small_text else "normal",
    }

    if has_small_text:
        logger.warning(
            f"Small text detected ({severity}): median={median_height:.1f}px, "
            f"p10={p10_height:.1f}px, min={min_height:.1f}px → target {target_dpi} DPI"
        )

    return result


def _detect_font_characteristics(img_array: np.ndarray) -> Dict:
    """Analyze image to detect font characteristics (informational).

    Helps tune preprocessing for DIN 1451, Helvetica, or Roboto.
    """
    if len(img_array.shape) == 3:
        gray = cv2.cvtColor(img_array, cv2.COLOR_RGB2GRAY)
    else:
        gray = img_array

    _, binary = cv2.threshold(gray, 127, 255, cv2.THRESH_BINARY_INV)
    contours, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    stroke_widths = []
    for contour in contours:
        area = cv2.contourArea(contour)
        if 10 < area < 1000:
            x, y, w, h = cv2.boundingRect(contour)
            stroke_widths.append(w)

    if stroke_widths:
        avg_stroke = float(np.mean(stroke_widths))
        stroke_variance = float(np.std(stroke_widths))
        uniform = stroke_variance < 2
        return {
            "avg_stroke_width": round(avg_stroke, 1),
            "stroke_variance": round(stroke_variance, 1),
            "uniform_strokes": uniform,
            "likely_font": "DIN 1451 or Roboto" if uniform else "Helvetica or similar",
        }

    return {}


def _preprocess_for_ocr(pil_image: Image.Image) -> Image.Image:
    """Preprocess optimized for technical drawing fonts (DIN 1451, Helvetica, Roboto).

    These fonts have specific characteristics:
    - DIN 1451: Geometric, uniform stroke width, open apertures
    - Helvetica: Sans-serif, tight spacing, similar characters (0/O, 1/I)
    - Roboto: Modern sans-serif, geometric, clear numerals
    """
    img_array = np.array(pil_image)

    # Detect font characteristics for logging
    font_info = _detect_font_characteristics(img_array)
    if font_info:
        logger.info(f"Font characteristics: {font_info}")

    # Handle color images - extract all color channels
    if len(img_array.shape) == 3:
        blue_channel = img_array[:, :, 2]   # Blue text (common in CAD)
        red_channel = img_array[:, :, 0]    # Red text (revisions/notes)
        green_channel = img_array[:, :, 1]  # Green text (less common)
        gray = cv2.cvtColor(img_array, cv2.COLOR_RGB2GRAY)

        # Combine all channels - take minimum to capture dark text in any color
        combined = np.minimum.reduce([blue_channel, red_channel, green_channel, gray])

        unique_colors = len(np.unique(img_array.reshape(-1, img_array.shape[2]), axis=0))
        logger.info(f"Original image had {unique_colors} unique colors, normalized in preprocessing")
    else:
        combined = img_array

    # Apply CLAHE (Contrast Limited Adaptive Histogram Equalization) BEFORE
    # binarization — this is critical for small digits where 3/4/8 blur together.
    # CLAHE enhances local contrast so thin strokes and curves in small glyphs
    # remain distinguishable after thresholding.
    clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
    enhanced = clahe.apply(combined)

    # Invert so text becomes bright on dark background
    inverted = cv2.bitwise_not(enhanced)

    # For small text, use gentler denoising to preserve detail
    # Bilateral filter with smaller spatial window preserves edges (digit curves)
    denoised = cv2.bilateralFilter(inverted, d=3, sigmaColor=75, sigmaSpace=75)

    # Calculate adaptive block size based on image resolution
    # Smaller images (or small text) need smaller blocks to preserve detail
    img_height = img_array.shape[0]
    if img_height < 1000:
        block_size = 7   # Small image = small block
    elif img_height < 2000:
        block_size = 9
    else:
        block_size = 11  # Large image = larger block

    # Ensure block_size is odd
    block_size = block_size if block_size % 2 == 1 else block_size + 1

    logger.info(f"Using adaptive threshold block size: {block_size} (image height: {img_height}px)")

    binary = cv2.adaptiveThreshold(
        denoised, 255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY,
        blockSize=block_size,
        C=2,
    )

    # For small text, skip aggressive morphological operations that damage characters.
    # The distinguishing features between 3 and 4 are:
    #   3 = two curved bumps (open on left)
    #   4 = angular junction (open at bottom)
    # Aggressive opening/closing destroys these fine differences.
    # Only do very light cleaning with a tiny kernel.
    kernel_tiny = np.ones((1, 1), np.uint8)
    binary = cv2.morphologyEx(binary, cv2.MORPH_OPEN, kernel_tiny, iterations=1)

    # Light sharpening to accentuate edges (curve vs angle distinction in 3/4)
    kernel_sharpen_light = np.array([[ 0, -1,  0],
                                     [-1,  5, -1],
                                     [ 0, -1,  0]])
    sharpened = cv2.filter2D(binary, -1, kernel_sharpen_light)

    logger.info("Preprocessing complete (CLAHE + adaptive threshold, optimized for small digits)")

    return Image.fromarray(sharpened)


def _verify_dimensions_with_ocr(
    dimensions: List[Dict],
    image_path: str,
) -> List[Dict]:
    """Use Tesseract OCR to verify that extracted dimension values exist in the image.

    Flags dimensions where Gemini's extracted value can't be found by OCR,
    indicating potential misreads or hallucinations.
    """
    import pytesseract

    path = Path(image_path)

    # Resolve image path (convert PDF to PNG if needed)
    if path.suffix.lower() == ".pdf":
        png_path = path.with_suffix(".png")
        if not png_path.exists():
            try:
                import fitz
                doc = fitz.open(str(path))
                page = doc[0]
                pix = page.get_pixmap(matrix=fitz.Matrix(2, 2))
                pix.save(str(png_path))
                doc.close()
            except Exception:
                logger.warning("No PNG available for OCR verification")
                return dimensions
        img_path = str(png_path)
    else:
        img_path = str(path)

    try:
        img = Image.open(img_path)

        # Upscale for small text before preprocessing
        img = _upscale_for_small_text(img, target_dpi=300)

        # Preprocess to normalize all text colors (black, blue, red, etc.)
        img = _preprocess_for_ocr(img)

        # For small text, try multiple Tesseract PSM modes
        # PSM 6 = uniform block
        # PSM 11 = sparse text (better for small isolated numbers)
        # PSM 13 = raw line (best for dimension callouts)
        tesseract_configs = [
            "--psm 11 --oem 1 -c tessedit_char_whitelist=0123456789.+-/",  # Sparse text
            "--psm 6 --oem 3 -c tessedit_char_whitelist=0123456789.+-/ -c preserve_interword_spaces=0",  # Block
            "--psm 13 --oem 1 -c tessedit_char_whitelist=0123456789.",  # Raw line
        ]

        ocr_numbers = set()
        combined_text = ""

        for config in tesseract_configs:
            try:
                ocr_text = pytesseract.image_to_string(img, config=config)
                numbers = set(re.findall(r"\d+\.?\d*", ocr_text))
                ocr_numbers.update(numbers)
                combined_text += " " + ocr_text
                logger.info(f"OCR pass with config '{config.split('--psm')[1][:4].strip()}' found {len(numbers)} numbers")
            except Exception as e:
                logger.warning(f"OCR pass failed: {e}")
                continue

        # Also add concatenated versions (for "4 79" → "479")
        ocr_numbers_no_space = set(re.findall(r"\d+", combined_text.replace(" ", "")))
        ocr_numbers.update(ocr_numbers_no_space)

        logger.info(f"Combined OCR found {len(ocr_numbers)} unique numbers across all passes")

        verified = 0
        failed = 0

        for dim in dimensions:
            value = dim.get("value")
            if value is None:
                continue

            try:
                fval = float(value)
            except (TypeError, ValueError):
                continue

            # Try multiple string representations of the value
            value_strings = [
                str(value),
                f"{fval:.1f}",
                f"{fval:.2f}",
                f"{fval:.3f}",
            ]
            if fval == int(fval):
                value_strings.append(str(int(fval)))

            found = any(v in ocr_numbers for v in value_strings)

            if found:
                dim["ocr_verified"] = True
                verified += 1
            else:
                dim["ocr_verified"] = False
                dim["ocr_verification_failed"] = True
                original_confidence = dim.get("confidence", 1.0)
                dim["confidence"] = original_confidence * 0.6
                failed += 1
                logger.warning(
                    f"OCR verification failed for dimension: {value} "
                    f"(feature: {dim.get('feature_type', 'unknown')})"
                )

        logger.info(f"OCR verification: {verified} verified, {failed} failed")

    except Exception as e:
        logger.error(f"OCR verification error: {e}")
        # Don't fail the pipeline — just skip verification

    return dimensions


def _verify_dimensions_with_region_ocr(
    dimensions: List[Dict],
    image_path: str,
    img_size: Tuple[int, int],
) -> List[Dict]:
    """Per-dimension region-based OCR for small text verification.

    Instead of running OCR on the full image, crops a tight region around each
    dimension's known coordinates and OCRs at high zoom. This dramatically
    improves accuracy for very small digits (3 vs 4, 3 vs 8, 6 vs 8, 1 vs 7).
    """
    import pytesseract

    path = Path(image_path)

    # Load raw image
    try:
        if path.suffix.lower() == ".pdf":
            png_path = path.with_suffix(".png")
            if not png_path.exists():
                return dimensions
            pil_img = Image.open(str(png_path)).convert("L")
        else:
            pil_img = Image.open(str(path)).convert("L")
    except Exception as e:
        logger.warning(f"Region OCR: could not load image: {e}")
        return dimensions

    src_w, src_h = pil_img.size
    img_w, img_h = img_size
    verified_count = 0
    corrected_count = 0

    # Config tuned for single-number crops
    region_config = "--psm 7 --oem 1 -c tessedit_char_whitelist=0123456789."

    for dim in dimensions:
        coords = dim.get("coordinates", {})
        value = dim.get("value")
        if not coords or value is None:
            continue

        cx = coords.get("x", 0)
        cy = coords.get("y", 0)

        # Map from img_size coordinate space to actual image pixels
        if img_w > 0 and img_h > 0:
            px = int(cx * src_w / img_w)
            py = int(cy * src_h / img_h)
        else:
            px, py = int(cx), int(cy)

        # Crop a region around the dimension (±80px generous window)
        margin = 80
        x1 = max(0, px - margin)
        y1 = max(0, py - margin)
        x2 = min(src_w, px + margin)
        y2 = min(src_h, py + margin)

        if x2 - x1 < 20 or y2 - y1 < 10:
            continue

        crop = pil_img.crop((x1, y1, x2, y2))

        # Upscale the crop aggressively (4x) so tiny digits become large
        crop_w, crop_h = crop.size
        upscale = 4
        crop = crop.resize(
            (crop_w * upscale, crop_h * upscale),
            Image.Resampling.LANCZOS,
        )

        # Apply CLAHE on the crop for maximum contrast
        crop_arr = np.array(crop)
        clahe = cv2.createCLAHE(clipLimit=4.0, tileGridSize=(4, 4))
        crop_arr = clahe.apply(crop_arr)

        # Binarize
        _, crop_bin = cv2.threshold(crop_arr, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        crop_img = Image.fromarray(crop_bin)

        try:
            ocr_text = pytesseract.image_to_string(crop_img, config=region_config).strip()
            ocr_numbers = re.findall(r"\d+\.?\d*", ocr_text)
        except Exception:
            continue

        if not ocr_numbers:
            continue

        try:
            expected = float(value)
        except (TypeError, ValueError):
            continue

        # Check if OCR found something matching or close
        best_match = None
        best_diff = float("inf")
        for n in ocr_numbers:
            try:
                ocr_val = float(n)
                diff = abs(ocr_val - expected)
                if diff < best_diff:
                    best_diff = diff
                    best_match = ocr_val
            except ValueError:
                continue

        if best_match is None:
            continue

        if best_diff < 0.01:
            # Perfect match — boost confidence
            dim["region_ocr_verified"] = True
            dim["confidence"] = min(1.0, dim.get("confidence", 1.0) * 1.1)
            verified_count += 1
        elif best_diff <= 1.5 and best_diff > 0.01:
            # Close but different — possible 3↔4, 3↔8, 6↔8, 1↔7 confusion
            # Only override if OCR confidence is higher (value closer to a "round" number)
            dim["region_ocr_candidate"] = best_match
            dim["region_ocr_diff"] = best_diff

            # Flag for re-verification — the digit-level confusion is real
            if dim.get("confidence", 1.0) < 0.85:
                old_val = dim["value"]
                dim["value"] = best_match
                dim["region_ocr_corrected"] = True
                dim["region_ocr_original"] = old_val
                dim["confidence"] = 0.7
                corrected_count += 1
                logger.info(
                    f"Region OCR corrected: {old_val} → {best_match} "
                    f"(diff={best_diff:.2f}, possible digit confusion)"
                )

    logger.info(
        f"Region OCR: {verified_count} verified, {corrected_count} corrected "
        f"out of {len(dimensions)} dimensions"
    )
    return dimensions


def _normalize_dimension_value(value_str) -> Optional[float]:
    """Normalize OCR'd dimension values with font-specific character disambiguation.

    Targets common confusion in technical drawing fonts:
    - DIN 1451: O has slash, 0 is round — usually clear
    - Helvetica: 0/O very similar, 1/I/l very similar — HIGH CONFUSION RISK
    - Roboto: Clear distinction but small sizes can confuse 6/b, 8/B
    """
    if value_str is None:
        return None

    s = str(value_str).strip()
    if not s:
        return None

    original = s

    # Font-aware letter-to-number replacements, ordered by priority
    replacements = [
        # Zero disambiguation (critical for Helvetica)
        (r'\bO\b', '0'),               # Standalone letter O -> 0
        (r'\bo\b', '0'),               # Standalone letter o -> 0
        (r'O(?=\.)', '0'),             # O before decimal -> 0
        (r'(?<=\d)O(?=\d)', '0'),      # O between digits -> 0
        (r'^O(?=\d)', '0'),            # O at start before digit -> 0
        (r'(?<=\d)O$', '0'),           # O at end after digit -> 0
        (r'(?<=\.)O(?=\d)', '0'),      # O after decimal point -> 0
        (r'(?<=\.)O$', '0'),           # O after decimal at end -> 0
        # One disambiguation (critical for Helvetica narrow spacing)
        (r'\bl\b', '1'),               # Standalone lowercase L -> 1
        (r'\bI\b', '1'),               # Standalone uppercase I -> 1
        (r'l(?=\.)', '1'),             # l before decimal -> 1
        (r'I(?=\.)', '1'),             # I before decimal -> 1
        (r'^l(?=\d)', '1'),            # l at start before digit -> 1
        (r'^I(?=\d)', '1'),            # I at start before digit -> 1
        (r'(?<=\.)l', '1'),            # l after decimal -> 1
        (r'(?<=\.)I', '1'),            # I after decimal -> 1
        # Six disambiguation (Roboto small sizes)
        (r'(?<=\d)b(?=[\d.])', '6'),   # b between digit and digit/dot -> 6
        (r'(?<=\d)b$', '6'),           # b at end after digit -> 6
        # Eight disambiguation (Roboto small sizes)
        (r'(?<=\d)B(?=[\d.])', '8'),   # B between digit and digit/dot -> 8
        # Five disambiguation
        (r'(?<=\d)S(?=[\d.])', '5'),   # S between digit and digit/dot -> 5
        # Two disambiguation
        (r'(?<=\d)Z(?=[\d.])', '2'),   # Z between digit and digit/dot -> 2
    ]

    for pattern, replacement in replacements:
        s = re.sub(pattern, replacement, s)

    if s != original:
        logger.info(f"Corrected letter-number confusion: '{original}' -> '{s}'")

    # Fix space instead of decimal point: "4 79" or "12 5"
    match = re.match(r'^(\d+)\s+(\d{1,3})$', s)
    if match:
        s = f"{match.group(1)}.{match.group(2)}"
        logger.info(f"Fixed space-as-decimal: '{original}' -> '{s}'")

    # Remove any remaining non-numeric characters (except decimal point)
    s = re.sub(r'[^\d.]', '', s)

    # Handle multiple decimal points
    if s.count('.') > 1:
        logger.warning(f"Multiple decimal points in value: {original}")
        parts = s.split('.')
        s = parts[0] + '.' + ''.join(parts[1:])

    # Must contain at least one digit
    if not re.search(r'\d', s):
        logger.warning(f"No digits in dimension value: {original}")
        return None

    try:
        result = float(s)
        if result < 0.001 or result > 100000:
            logger.warning(f"Dimension value out of reasonable range: {result} (from '{original}')")
        return result
    except (ValueError, TypeError):
        logger.warning(f"Could not normalize dimension value: {original} -> {s}")
        return None


def _validate_dimension_pattern(dimension: Dict) -> bool:
    """Validate that a dimension looks like a real dimension, not garbage OCR.

    Returns False if the dimension appears to have letter-number confusion.
    """
    value = dimension.get("value")
    if value is None:
        return False

    value_str = str(value)
    red_flags = []

    # Contains letters that shouldn't be in a numeric dimension
    # Exclude E/e (scientific notation), letters used in tolerances (H, g, etc.)
    if re.search(r'[A-DF-HJ-NP-RT-WY-Zaceghjkmnpqrtuvwxyz]', value_str):
        red_flags.append("contains_letters")

    # Too many decimal places (OCR artifact)
    if '.' in value_str and len(value_str.split('.')[1]) > 3:
        red_flags.append("too_many_decimals")

    # Alternating letters and numbers (OCR confusion)
    if re.search(r'\d[A-Za-z]\d|\d[A-Za-z][A-Za-z]\d', value_str):
        red_flags.append("alternating_chars")

    if red_flags:
        logger.warning(
            f"Dimension validation failed: value={value}, "
            f"feature={dimension.get('feature_type')}, flags={red_flags}"
        )
        dimension["validation_failed"] = True
        dimension["validation_flags"] = red_flags
        return False

    return True


def _validate_font_specific_errors(dimensions: List[Dict]) -> List[Dict]:
    """Check for common OCR errors specific to technical drawing fonts."""
    for dim in dimensions:
        value = dim.get("value")
        if value is None:
            continue

        value_str = str(value)
        flags = []

        # Check for letter contamination (common in Helvetica)
        if re.search(r'[OoIlSZBb]', value_str):
            flags.append("possible_letter_contamination")

        # Check for missing decimal — large integers may be mis-reads of decimals
        # e.g., "479" might really be "4.79", "1265" might be "12.65"
        if '.' not in value_str and value_str.isdigit():
            try:
                int_val = int(value_str)
                if int_val > 100 and len(value_str) >= 3:
                    flags.append("possible_missing_decimal")
            except ValueError:
                pass

        # Unlikely dimension range for mechanical drawings
        try:
            fval = float(value)
            if fval < 0.001 or fval > 10000:
                flags.append("unlikely_dimension_range")
        except (ValueError, TypeError):
            pass

        if flags:
            dim["font_validation_flags"] = flags
            dim["confidence"] = dim.get("confidence", 1.0) * 0.8
            logger.warning(f"Font-specific validation flags for {value}: {flags}")

    return dimensions


def _normalize_text_value(text: str, field_type: str = "general") -> str:
    """Normalize text fields by fixing number→letter confusion.

    This is the REVERSE of _normalize_dimension_value — it corrects cases where
    numbers were incorrectly read in place of letters in text contexts.

    field_type controls which corrections are applied:
      - "tolerance_class": e.g., "H7", "g6" — letter prefix + number suffix
      - "datum": single uppercase letter, e.g., "A", "B", "C"
      - "material": e.g., "AISI 316L", "Al 6061-T6"
      - "description": e.g., "BEARING", "BOLT", "SHAFT"
      - "general": apply all heuristics
    """
    if not text or not isinstance(text, str):
        return text or ""

    original = text.strip()
    s = original

    if field_type == "tolerance_class":
        # ISO tolerance classes: one or two letters + digits (H7, g6, js15, IT7)
        # Fix number→letter in the letter prefix
        m = re.match(r'^(\d+)(\d+)$', s)
        if m and len(s) <= 3:
            # Fully numeric like "117" — could be "H7" misread
            # Can't fix without more context, just flag it
            pass

        # Fix common misreads in the letter portion
        # "1" at start should often be "I" (as in IT7, IT8)
        s = re.sub(r'^1(?=T\d)', 'I', s)
        # "8" at start in tolerance context → likely "B" (B5, B6 rare but exist)
        # "0" at start → likely "O" (rare)
        # "6" at start → likely "G" (G6, G7 exist)
        s = re.sub(r'^6(?=\d)', 'G', s)
        # "5" at start → likely "S" (S6, S7 exist)
        s = re.sub(r'^5(?=\d)', 'S', s)

    elif field_type == "datum":
        # Datum references are single uppercase letters
        # Fix common number→letter confusions
        datum_fixes = {
            "4": "A", "8": "B", "0": "D", "6": "G",
            "1": "I", "5": "S", "2": "Z", "9": "g",
        }
        if len(s) == 1 and s in datum_fixes:
            fixed = datum_fixes[s]
            logger.info(f"Datum letter correction: '{s}' → '{fixed}'")
            s = fixed

    elif field_type in ("material", "description", "general"):
        # Fix digit→letter confusion in words
        # Only apply to sequences that look like they should be words
        words = s.split()
        fixed_words = []
        for word in words:
            # Check if word is a mix of letters and digits — possible confusion
            has_letters = bool(re.search(r'[A-Za-z]', word))
            has_digits = bool(re.search(r'\d', word))

            if has_letters and has_digits:
                w = word
                # 0 inside a word → O (like "B0LT" → "BOLT")
                w = re.sub(r'(?<=[A-Za-z])0(?=[A-Za-z])', 'O', w)
                # 1 surrounded by or adjacent to uppercase → I (like "A1S1" → "AISI")
                w = re.sub(r'(?<=[A-Z])1(?=[A-Z])', 'I', w)
                w = re.sub(r'(?<=[A-Z])1$', 'I', w)  # trailing 1 after uppercase
                w = re.sub(r'(?<=[a-z])1(?=[a-z])', 'l', w)
                w = re.sub(r'(?<=[a-z])1$', 'l', w)  # trailing 1 after lowercase
                # 8 at start of word before letters → B (like "8EARING" → "BEARING")
                w = re.sub(r'^8(?=[A-Za-z]{2,})', 'B', w)
                # 5 at start of word before letters → S (like "5TEEL" → "STEEL")
                w = re.sub(r'^5(?=[A-Za-z]{2,})', 'S', w)
                # 6 inside a word → G
                w = re.sub(r'(?<=[A-Za-z])6(?=[A-Za-z])', 'G', w)
                if w != word:
                    logger.info(f"Text letter correction ({field_type}): '{word}' → '{w}'")
                fixed_words.append(w)
            elif re.match(r'^\d+[A-Za-z]$', word):
                # Number ending in letter — like "316L", keep as-is (correct)
                fixed_words.append(word)
            else:
                fixed_words.append(word)
        s = " ".join(fixed_words)

    if s != original:
        logger.info(f"Text normalization ({field_type}): '{original}' → '{s}'")

    return s


def _validate_and_normalize_text_fields(extracted: Dict) -> Dict:
    """Validate and normalize all text fields in the extraction result.

    Applies context-aware letter correction to:
    - Tolerance classes on dimensions
    - GD&T datum references
    - Part list descriptions and materials
    - Title block fields
    """
    # 1. Normalize tolerance_class on each dimension
    for dim in extracted.get("dimensions", []):
        tc = dim.get("tolerance_class")
        if tc and isinstance(tc, str):
            dim["tolerance_class"] = _normalize_text_value(tc, "tolerance_class")

    # 2. Normalize GD&T datum references
    for callout in extracted.get("gdt_callouts", []):
        datum = callout.get("datum")
        if datum and isinstance(datum, str):
            # Datum can be multi-letter like "A|B" or "A-B" — normalize each
            parts = re.split(r'[|/\-,\s]+', datum)
            fixed_parts = [_normalize_text_value(p.strip(), "datum") for p in parts if p.strip()]
            callout["datum"] = "|".join(fixed_parts) if len(fixed_parts) > 1 else (fixed_parts[0] if fixed_parts else datum)

        # Also normalize the symbol field
        symbol = callout.get("symbol")
        if symbol and isinstance(symbol, str):
            callout["symbol"] = _normalize_text_value(symbol, "description")

    # 3. Normalize part list descriptions and materials
    for part in extracted.get("part_list", []):
        desc = part.get("description")
        if desc and isinstance(desc, str):
            part["description"] = _normalize_text_value(desc, "description")

        mat = part.get("material")
        if mat and isinstance(mat, str):
            part["material"] = _normalize_text_value(mat, "material")

    # 4. Normalize title block
    title_block = extracted.get("title_block", {})
    if isinstance(title_block, dict):
        for field in ("title", "material", "drawing_number"):
            val = title_block.get(field)
            if val and isinstance(val, str):
                title_block[field] = _normalize_text_value(val, "material" if field == "material" else "general")

        # Revision is usually a single letter
        rev = title_block.get("revision")
        if rev and isinstance(rev, str) and len(rev.strip()) == 1:
            title_block["revision"] = _normalize_text_value(rev.strip(), "datum")

    extracted["title_block"] = title_block

    return extracted


def _bind_dimensions_to_entities(
    dimensions: List[Dict],
    entity_registry: Dict[str, Dict],
    img_size: Tuple[int, int]
) -> List[Dict]:
    """
    Post-process dimensions to ensure entity binding.
    Scales coordinates from percentages to pixels, adds grid_ref, validates item_number.
    Normalizes dimension values and validates patterns.
    """
    img_width, img_height = img_size
    bound_dimensions = []

    for dim in dimensions:
        # Normalize dimension value to fix OCR letter-number errors
        raw_value = dim.get("value")
        if raw_value is not None:
            normalized = _normalize_dimension_value(raw_value)
            if normalized is not None:
                if normalized != raw_value:
                    logger.info(f"Normalized dimension value: {raw_value} -> {normalized}")
                    dim["value_normalized"] = True
                dim["value"] = normalized
            else:
                logger.error(f"Failed to normalize dimension value: {raw_value}")
                dim["value_invalid"] = True

        # Validate the dimension pattern
        if not _validate_dimension_pattern(dim):
            dim["confidence"] = dim.get("confidence", 1.0) * 0.3

        # Scale coordinates from percentage to pixels
        coords = dim.get("coordinates") or {}
        scaled_coords = _scale_coordinates(coords, img_width, img_height)
        dim["coordinates"] = scaled_coords

        x = scaled_coords.get("x", 0)
        y = scaled_coords.get("y", 0)

        # Compute grid reference if not present
        if not dim.get("grid_ref"):
            dim["grid_ref"] = _compute_grid_ref(x, y, img_width, img_height)

        # Validate entity binding
        item_num = dim.get("item_number")
        if item_num:
            item_num = str(item_num)
            if item_num in entity_registry:
                # Valid binding - enrich with entity info
                dim["entity_description"] = entity_registry[item_num].get("description", "")
            else:
                # Item number not in BOM - flag but keep
                dim["binding_status"] = "unverified"
        else:
            dim["binding_status"] = "unbound"

        bound_dimensions.append(dim)

    return bound_dimensions


def _enrich_zones_with_grid(zones: List[Dict], img_size: Tuple[int, int]) -> List[Dict]:
    """Add grid references to zones based on their bounds."""
    img_width, img_height = img_size
    enriched = []

    for zone in zones:
        bounds = zone.get("bounds") or {}
        x1, y1 = bounds.get("x1", 0), bounds.get("y1", 0)
        x2, y2 = bounds.get("x2", img_width), bounds.get("y2", img_height)

        # Compute grid span
        start_ref = _compute_grid_ref(x1, y1, img_width, img_height)
        end_ref = _compute_grid_ref(x2, y2, img_width, img_height)

        if start_ref == end_ref:
            zone["grid_ref"] = start_ref
        else:
            zone["grid_ref"] = f"{start_ref}-{end_ref}"

        enriched.append(zone)

    return enriched


REVERIFY_PROMPT = """You are re-checking specific dimension values from a mechanical drawing.
The initial extraction flagged these dimensions as potentially incorrect.

For each dimension listed below, look at the drawing and verify or correct the value.
Return ONLY a JSON array of objects, one per dimension, with these fields:
- original_value: the value from the initial extraction
- corrected_value: the correct value you read from the drawing (or same as original if correct)
- confidence: your confidence 0.0 to 1.0
- correction_note: brief explanation if you changed the value, or "confirmed" if unchanged

Dimensions to verify:
{dim_list}

Look carefully at each one. Pay special attention to:
- Decimal points (0.5 vs 0.05 vs 5.0)
- Similar-looking digits at small sizes:
  * 3 vs 4: curved arcs (3) vs angular junction (4)
  * 3 vs 8: open left side (3) vs two closed loops (8)
  * 6 vs 8: one bottom loop (6) vs two stacked loops (8)
  * 1 vs 7: straight vertical (1) vs horizontal bar + diagonal (7)
  * 5 vs 6: flat top + open bottom (5) vs curved top + closed bottom (6)
- Letter-number confusion — CONTEXT MATTERS:
  * In dimension VALUES: O→0, I→1, l→1, S→5, B→8, Z→2 (always digits)
  * In tolerance classes (H7, g6): PRESERVE the letter — H≠11, g≠9
  * In datum references (A, B, C): PRESERVE the letter — B≠8, D≠0, G≠6
  * In material/description text: PRESERVE letters — 316L≠3161, AISI≠A1S1
- For each suspect character, first determine CONTEXT (dimension vs text), then read
"""


async def _verify_critical_dimensions(
    dimensions: List[Dict],
    image_parts: List[Dict],
    model,
) -> List[Dict]:
    """Re-extract dimensions with low confidence or that failed validation.

    Sends suspect dimensions back to Gemini with a focused verification prompt.
    """
    suspect_dims = [
        d for d in dimensions
        if d.get("validation_failed")
        or d.get("ocr_verified") is False
        or d.get("confidence", 1.0) < 0.7
    ]

    if not suspect_dims:
        logger.info("No dimensions need re-verification")
        return dimensions

    logger.info(f"Re-verifying {len(suspect_dims)} suspect dimensions")

    # Build a summary of what to re-check
    dim_list_lines = []
    for i, d in enumerate(suspect_dims):
        val = d.get("value", "?")
        ft = d.get("feature_type", "unknown")
        coords = d.get("coordinates", {})
        reason = []
        if d.get("validation_failed"):
            reason.append("validation_failed")
        if d.get("ocr_verified") is False:
            reason.append("ocr_mismatch")
        if d.get("confidence", 1.0) < 0.7:
            reason.append(f"low_confidence={d.get('confidence', '?')}")
        dim_list_lines.append(
            f"  {i+1}. value={val}, type={ft}, "
            f"approx_location=({coords.get('x', '?')}, {coords.get('y', '?')}), "
            f"flags=[{', '.join(reason)}]"
        )

    dim_list_str = "\n".join(dim_list_lines)
    prompt = REVERIFY_PROMPT.replace("{dim_list}", dim_list_str)

    content_parts = []
    for img in image_parts:
        content_parts.append({"inline_data": img})
    content_parts.append(prompt)

    try:
        response = await model.generate_content_async(
            content_parts,
            generation_config=genai.GenerationConfig(
                response_mime_type="application/json",
                temperature=0.05,
            ),
            request_options={"timeout": 300},
        )

        corrections = json.loads(response.text)
        if not isinstance(corrections, list):
            corrections = [corrections] if isinstance(corrections, dict) else []

        applied = 0
        for i, corr in enumerate(corrections):
            if i >= len(suspect_dims):
                break
            dim = suspect_dims[i]
            corrected = corr.get("corrected_value")
            note = corr.get("correction_note", "")
            conf = corr.get("confidence", 0.5)

            if corrected is not None and corrected != dim.get("value"):
                old_val = dim.get("value")
                try:
                    dim["value"] = float(corrected)
                except (TypeError, ValueError):
                    dim["value"] = corrected
                dim["reverified"] = True
                dim["reverify_note"] = note
                dim["confidence"] = conf
                logger.info(f"Reverification corrected: {old_val} -> {corrected} ({note})")
                applied += 1
            elif note == "confirmed":
                dim["reverified"] = True
                dim["confidence"] = max(dim.get("confidence", 0.5), conf)

        logger.info(f"Reverification complete: {applied} corrections applied out of {len(suspect_dims)} checked")

    except Exception as e:
        logger.warning(f"Reverification pass failed (non-fatal): {e}")

    return dimensions


def _validate_extraction(extracted: Dict, entity_registry: Dict[str, Dict]) -> Dict:
    """Validate and report on extraction quality."""
    dims = extracted.get("dimensions", [])
    parts = extracted.get("part_list", [])

    total_dims = len(dims)
    bound_dims = sum(1 for d in dims if d.get("item_number"))
    coords_present = sum(1 for d in dims if d.get("coordinates", {}).get("x") is not None)

    validation = {
        "total_dimensions": total_dims,
        "bound_to_entity": bound_dims,
        "binding_rate": round(bound_dims / max(total_dims, 1) * 100, 1),
        "coordinates_present": coords_present,
        "coordinate_rate": round(coords_present / max(total_dims, 1) * 100, 1),
        "entities_in_bom": len(entity_registry),
        "quality_score": "good" if (bound_dims / max(total_dims, 1) > 0.7) else "needs_review"
    }

    return validation


async def run_ingestor(state: AuditState) -> AuditState:
    """
    Extract Dynamic Machine State (DMS) from drawing using Gemini Vision.

    Implements:
    1. Grid-based zone segmentation
    2. BOM-first entity identification
    3. Dimension-to-entity binding
    """
    _configure_genai()

    file_path = state["file_path"]
    crop_region = state.get("crop_region")
    is_rescan = crop_region is not None

    prompt = RESCAN_PROMPT if is_rescan else EXTRACTION_PROMPT
    image_parts, img_size = _load_images(file_path, crop_region)

    # Detect small text for adaptive processing
    small_text_info = {"has_small_text": False, "target_dpi": 300}
    try:
        path = Path(file_path)
        if path.suffix.lower() != ".pdf":
            detect_img = Image.open(path)
            small_text_info = _detect_small_text(np.array(detect_img))
            if small_text_info.get("has_small_text"):
                # Re-load with higher DPI if small text needs more aggressive upscaling
                recommended_dpi = small_text_info.get("target_dpi", 300)
                if recommended_dpi > 300:
                    logger.warning(
                        f"Small text detected ({small_text_info.get('severity')}) — "
                        f"re-loading image at {recommended_dpi} DPI"
                    )
                    image_parts, img_size = _load_images(
                        file_path, crop_region, target_dpi=recommended_dpi
                    )
    except Exception as e:
        logger.warning(f"Small text detection failed (non-fatal): {e}")

    model = genai.GenerativeModel(settings.VISION_MODEL)

    content_parts = []
    for img in image_parts:
        content_parts.append({"inline_data": img})
    content_parts.append(prompt)

    # Retry logic with exponential backoff for rate limiting
    logger.info("Ingestor: sending %s to Gemini (%s, %d content parts)", "rescan" if is_rescan else "extraction", settings.VISION_MODEL, len(content_parts))
    response = None
    for attempt in range(MAX_RETRIES):
        try:
            logger.info("Ingestor: Gemini API call attempt %d/%d...", attempt + 1, MAX_RETRIES)
            response = await model.generate_content_async(
                content_parts,
                generation_config=genai.GenerationConfig(
                    response_mime_type="application/json",
                    temperature=0.1,
                ),
                request_options={"timeout": 600},
            )
            break  # Success, exit retry loop
        except ResourceExhausted as e:
            if attempt < MAX_RETRIES - 1:
                backoff = INITIAL_BACKOFF * (2 ** attempt)
                logger.warning(f"Rate limited (429). Waiting {backoff}s before retry {attempt + 2}/{MAX_RETRIES}...")
                await asyncio.sleep(backoff)
            else:
                logger.error("Rate limit exhausted after max retries")
                raise

    if response is None:
        raise RuntimeError("Failed to get response from Gemini API")

    # Log raw response for debugging
    response_len = len(response.text) if response.text else 0
    logger.info(f"Gemini response length: {response_len} chars")
    if response_len == 0:
        logger.error("Gemini returned empty response!")
    elif response_len < 500:
        logger.info(f"Gemini raw response: {response.text}")
    else:
        logger.info(f"Gemini response preview: {response.text[:500]}...")

    def fix_json(text: str) -> dict:
        """Attempt to fix and parse malformed JSON from Gemini."""
        # Find JSON object
        start = text.find("{")
        end = text.rfind("}") + 1
        if start < 0 or end <= start:
            return {}
        text = text[start:end]
        # Fix trailing commas before ] or }
        text = re.sub(r',\s*([}\]])', r'\1', text)
        # Fix unquoted None/null
        text = re.sub(r':\s*None\b', ': null', text)
        # Try parsing
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        # Extract individual dimension objects using balanced brace matching
        dimensions = []
        dims_start = text.find('"dimensions"')
        if dims_start >= 0:
            # Find the opening bracket
            bracket_start = text.find('[', dims_start)
            if bracket_start >= 0:
                # Parse each dimension object by matching braces
                i = bracket_start + 1
                while i < len(text):
                    # Skip whitespace
                    while i < len(text) and text[i] in ' \t\n\r,':
                        i += 1
                    if i >= len(text) or text[i] == ']':
                        break
                    if text[i] == '{':
                        # Find matching closing brace
                        depth = 1
                        obj_start = i
                        i += 1
                        while i < len(text) and depth > 0:
                            if text[i] == '{':
                                depth += 1
                            elif text[i] == '}':
                                depth -= 1
                            i += 1
                        if depth == 0:
                            obj_text = text[obj_start:i]
                            # Fix and parse this object
                            obj_text = re.sub(r',\s*}', '}', obj_text)
                            obj_text = re.sub(r':\s*None\b', ': null', obj_text)
                            try:
                                dim = json.loads(obj_text)
                                if 'value' in dim or 'coordinates' in dim:
                                    dimensions.append(dim)
                            except json.JSONDecodeError:
                                pass
                    else:
                        i += 1

        if dimensions:
            logger.info(f"fix_json recovered {len(dimensions)} dimensions from truncated response")
            return {"dimensions": dimensions, "zones": [], "part_list": [], "gdt_callouts": []}

        return {}

    try:
        logger.info(f"Raw Gemini response (first 2000 chars): {response.text[:2000] if response.text else 'None'}")
        extracted = json.loads(response.text)
        logger.info(f"Parsed type: {type(extracted).__name__}")
        # Gemini sometimes wraps the object in an array — unwrap it
        if isinstance(extracted, list):
            if len(extracted) == 1 and isinstance(extracted[0], dict):
                extracted = extracted[0]
                logger.info("Unwrapped single-element array to dict")
            elif len(extracted) > 1:
                # Merge multiple dicts into one
                merged = {}
                for item in extracted:
                    if isinstance(item, dict):
                        for k, v in item.items():
                            if k in merged and isinstance(merged[k], list) and isinstance(v, list):
                                merged[k].extend(v)
                            else:
                                merged[k] = v
                extracted = merged
                logger.info("Merged %d array elements into single dict", len(extracted))
            else:
                extracted = {}
        logger.info(f"JSON parsed successfully: {len(extracted.get('dimensions', []))} dimensions")
    except json.JSONDecodeError as e:
        logger.warning(f"JSON parse failed: {e}, attempting fix_json")
        extracted = fix_json(response.text)
        if not extracted or not extracted.get("dimensions"):
            logger.error(f"fix_json returned empty result, response was: {response.text[:1000] if response.text else 'None'}")

    # Phase 0: Normalize text fields (letters in tolerance classes, datums, materials, etc.)
    extracted = _validate_and_normalize_text_fields(extracted)

    # Phase 1: Build entity registry from BOM
    part_list = extracted.get("part_list", [])
    entity_registry = _build_entity_registry(part_list)

    # Phase 2: Enrich zones with grid references
    zones = extracted.get("zones", [])
    zones = _enrich_zones_with_grid(zones, img_size)
    extracted["zones"] = zones

    # Phase 3: Bind dimensions to entities and add grid refs
    dimensions = extracted.get("dimensions", [])
    dimensions = _bind_dimensions_to_entities(dimensions, entity_registry, img_size)

    # Phase 3b: Validate and adjust coordinates to ensure they land on drawing content
    dimensions = _validate_and_adjust_coordinates(dimensions, file_path, img_size)

    # Phase 3c: OCR verification of extracted values (full-image)
    dimensions = _verify_dimensions_with_ocr(dimensions, file_path)

    # Phase 3c2: Region-based OCR for per-dimension verification (small digit accuracy)
    dimensions = _verify_dimensions_with_region_ocr(dimensions, file_path, img_size)

    # Phase 3d: Quality check for letter-number confusion
    invalid_count = sum(1 for d in dimensions if d.get("validation_failed"))
    normalized_count = sum(1 for d in dimensions if d.get("value_normalized"))
    if invalid_count > 0:
        logger.warning(
            f"Found {invalid_count} dimensions with validation issues (possible letter-number confusion)"
        )
    if normalized_count > 0:
        logger.info(f"Normalized {normalized_count} dimension values (letter-number corrections applied)")

    # Phase 3e: Font-specific error validation
    dimensions = _validate_font_specific_errors(dimensions)

    # Phase 3f: Re-verify suspect dimensions with a focused Gemini pass
    dimensions = await _verify_critical_dimensions(dimensions, image_parts, model)

    # Phase 3g: Reduce confidence for all dimensions if small text was detected
    if small_text_info.get("has_small_text"):
        for dim in dimensions:
            original_conf = dim.get("confidence", 1.0)
            dim["confidence"] = original_conf * 0.9
            dim["small_text_detected"] = True
        logger.info("Reduced confidence for dimensions due to small text detection")

    extracted["dimensions"] = dimensions

    # Phase 4: Bind GD&T callouts similarly (with coordinate scaling)
    gdt_callouts = extracted.get("gdt_callouts", [])
    for callout in gdt_callouts:
        coords = callout.get("coordinates") or {}
        scaled_coords = _scale_coordinates(coords, img_size[0], img_size[1])
        callout["coordinates"] = scaled_coords
        x, y = scaled_coords.get("x", 0), scaled_coords.get("y", 0)
        if not callout.get("grid_ref"):
            callout["grid_ref"] = _compute_grid_ref(x, y, img_size[0], img_size[1])
    extracted["gdt_callouts"] = gdt_callouts

    # Validate extraction quality
    validation = _validate_extraction(extracted, entity_registry)

    if is_rescan and state.get("machine_state"):
        # Merge rescan results into existing state
        existing = state["machine_state"]
        for key in ["dimensions", "part_list", "gdt_callouts"]:
            if key in extracted and extracted[key]:
                existing[key] = extracted[key]
        machine_state = existing
    else:
        # Validate with MachineState, but fall back to raw extracted if validation fails
        try:
            machine_state = MachineState(**extracted).model_dump()
        except Exception as validation_error:
            logger.warning(f"MachineState validation failed: {validation_error}")
            logger.warning("Using raw extracted data instead")
            # Use raw extracted dict but ensure required keys exist
            machine_state = {
                "zones": extracted.get("zones", []),
                "dimensions": extracted.get("dimensions", []),
                "part_list": extracted.get("part_list", []),
                "gdt_callouts": extracted.get("gdt_callouts", []),
                "raw_text": extracted.get("raw_text", ""),
                "title_block": extracted.get("title_block", {}),
            }

    log_entry = {
        "agent": "ingestor",
        "action": "rescan" if is_rescan else "full_extraction",
        "zones_found": len(machine_state.get("zones", [])),
        "dimensions_found": len(machine_state.get("dimensions", [])),
        "parts_found": len(machine_state.get("part_list", [])),
        "gdt_callouts_found": len(machine_state.get("gdt_callouts", [])),
        "entity_binding": validation,
        "image_size": {"width": img_size[0], "height": img_size[1]},
    }
    if invalid_count > 0:
        log_entry["dimensions_with_validation_issues"] = invalid_count
    if normalized_count > 0:
        log_entry["dimensions_normalized"] = normalized_count
    if small_text_info.get("has_small_text"):
        log_entry["small_text_detected"] = True
        log_entry["median_char_height"] = small_text_info.get("median_char_height")
        log_entry["min_char_height"] = small_text_info.get("min_char_height")

    agent_log = state.get("agent_log", [])
    agent_log.append(log_entry)

    return {
        **state,
        "machine_state": machine_state,
        "agent_log": agent_log,
        "status": "ingested",
        "crop_region": None,
    }
