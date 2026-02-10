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


def _load_images(file_path: str, crop_region: Optional[Dict] = None) -> Tuple[List, Tuple[int, int]]:
    """Load drawing file and return list of image parts + dimensions.

    Returns dimensions matching the 2x rendered size used by the frontend.
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
        img_size = (img.width, img.height)
        if crop_region:
            x1, y1 = crop_region.get("x1", 0), crop_region.get("y1", 0)
            x2, y2 = crop_region.get("x2", img.width), crop_region.get("y2", img.height)
            img = img.crop((x1, y1, x2, y2))
            img_size = (x2 - x1, y2 - y1)
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


def _bind_dimensions_to_entities(
    dimensions: List[Dict],
    entity_registry: Dict[str, Dict],
    img_size: Tuple[int, int]
) -> List[Dict]:
    """
    Post-process dimensions to ensure entity binding.
    Scales coordinates from percentages to pixels, adds grid_ref, validates item_number.
    """
    img_width, img_height = img_size
    bound_dimensions = []

    for dim in dimensions:
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

    agent_log = state.get("agent_log", [])
    agent_log.append(log_entry)

    return {
        **state,
        "machine_state": machine_state,
        "agent_log": agent_log,
        "status": "ingested",
        "crop_region": None,
    }
