"""Hybrid PaddleOCR + Gemini Reasoning ingestor.

Strategy:
1. PaddleOCR extracts ALL text regions with precise bounding boxes
2. Gemini 2.5-pro (reasoning model) receives BOTH the image AND the OCR text list
3. Gemini cross-references its visual understanding with precise OCR data
4. Result: better accuracy because Gemini doesn't have to guess at text —
   it has exact strings + positions, and just needs to interpret them

This replaces the pure-vision approach in ingestor.py where Gemini Flash
had to do both OCR and interpretation simultaneously.
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
import google.generativeai as genai
from google.api_core.exceptions import ResourceExhausted

from app.config import settings
from app.agents.state import AuditState, MachineState
from app.agents.ocr_preprocess import run_paddle_ocr
from app.agents.ingestor import (
    _configure_genai,
    _load_images,
    _compute_grid_ref,
    _build_entity_registry,
    _scale_coordinates,
    _bind_dimensions_to_entities,
    _enrich_zones_with_grid,
    _validate_and_adjust_coordinates,
    _validate_extraction,
    MAX_RETRIES,
    INITIAL_BACKOFF,
    GRID_ROWS,
    GRID_COLS,
)

logger = logging.getLogger(__name__)

PADDLE_EXTRACTION_PROMPT = """You are an expert mechanical engineering drawing reader.

You have TWO sources of information:
1. The DRAWING IMAGE (visual) — you can see the full drawing
2. PRE-EXTRACTED OCR TEXT (below) — precise text regions detected by OCR with exact positions

Your job: Use BOTH sources to extract a complete, accurate Dynamic Machine State.

## HOW TO USE THE OCR DATA
The OCR data gives you EXACT text strings and their positions as percentages of the image.
- Trust OCR for numeric values (it reads digits precisely)
- Trust your VISUAL understanding for interpreting what each number means
  (is it a dimension? tolerance? part number?)
- Cross-reference: if OCR says "25.0" at position (34%, 55%), look at that spot
  in the image to understand WHAT that 25.0 measures
- Use OCR positions as your coordinate source — they are more precise than estimating

## CRITICAL RULES
- Use OCR text values for dimensions — do NOT re-read numbers from the image
- Use the OCR bounding box center (x_pct, y_pct) as the coordinate for each dimension
- Use your vision to determine feature_type, tolerance_class associations, and BOM links
- If OCR detected a number but you can see from the image it's not a dimension
  (e.g., it's a part number, revision, date), exclude it from dimensions
- If you see a dimension in the image that OCR missed, include it with your best reading

## OUTPUT FORMAT
Return JSON with this structure:

1. "dimensions" array - for each dimension callout:
   - value: the numeric value (prefer OCR text if available)
   - unit: "mm" or "in"
   - coordinates: USE THE OCR POSITION {{"x": x_pct, "y": y_pct}} from the OCR data
   - feature_type: MUST be one of: "linear", "diameter", "radius", "angular", "thickness", "thread", "chamfer", "depth"
   - tolerance_class: if shown (H7, g6, etc.)
   - upper_tol: upper tolerance if shown
   - lower_tol: lower tolerance if shown
   - item_number: BOM reference number if linked to a balloon
   - ocr_source: true if this dimension came from OCR data, false if you read it yourself

2. "part_list" array - from the Bill of Materials/Part List:
   - item_number, description, material, quantity, weight, weight_unit

3. "zones" array - drawing views found:
   - name, grid_ref, features

4. "gdt_callouts" array:
   - symbol, value, datum, coordinates (from OCR positions)

5. "title_block" object:
   - title, drawing_number, revision, material, tolerance_general

Be thorough. Every measurement visible should be captured.
Prefer OCR text values over your own reading for numeric accuracy.

## OCR TEXT REGIONS DETECTED:
{ocr_data}

Now analyze the drawing image above together with this OCR data."""


async def run_ingestor_paddle(state: AuditState) -> AuditState:
    """Extract DMS using PaddleOCR pre-processing + Gemini reasoning model.

    Pipeline:
    1. Run PaddleOCR → get text regions with bounding boxes
    2. Format OCR results as structured text
    3. Send image + OCR text to Gemini 2.5-pro
    4. Post-process same as original ingestor (grid refs, entity binding, etc.)
    """
    _configure_genai()

    file_path = state["file_path"]
    crop_region = state.get("crop_region")
    is_rescan = crop_region is not None

    # ---------- Phase 1: PaddleOCR ----------
    logger.info("PaddleIngestor: running PaddleOCR on %s", file_path)
    try:
        ocr_result = run_paddle_ocr(file_path)
    except Exception as e:
        logger.error("PaddleOCR failed: %s — falling back to pure-vision ingestor", e)
        from app.agents.ingestor import run_ingestor
        return await run_ingestor(state)

    ocr_summary = ocr_result["summary"]
    logger.info(
        "PaddleIngestor: OCR found %d texts (%d dims, %d tols, %d GDT)",
        ocr_summary["total_texts"],
        ocr_summary["dimensions"],
        ocr_summary["tolerances"],
        ocr_summary["gdt"],
    )

    # ---------- Phase 2: Format OCR data for the prompt ----------
    ocr_lines = []
    for i, region in enumerate(ocr_result["grouped_regions"]):
        pos = region["position"]
        ocr_lines.append(
            f"  [{i+1}] \"{region['text']}\" "
            f"type={region['type']} "
            f"conf={region['confidence']:.2f} "
            f"pos=({pos['x_pct']:.1f}%, {pos['y_pct']:.1f}%) "
            f"bbox=({pos.get('x1_pct', 0):.1f}%-{pos.get('x2_pct', 0):.1f}%, "
            f"{pos.get('y1_pct', 0):.1f}%-{pos.get('y2_pct', 0):.1f}%)"
        )
    ocr_text_block = "\n".join(ocr_lines) if ocr_lines else "(No text detected by OCR)"

    prompt = PADDLE_EXTRACTION_PROMPT.format(ocr_data=ocr_text_block)

    # ---------- Phase 3: Load image + send to Gemini reasoning model ----------
    image_parts, img_size = _load_images(file_path, crop_region)

    # Use the reasoning model (gemini-2.5-pro) instead of flash
    model = genai.GenerativeModel(settings.REASONING_MODEL)

    content_parts = []
    for img in image_parts:
        content_parts.append({"inline_data": img})
    content_parts.append(prompt)

    logger.info(
        "PaddleIngestor: sending image + %d OCR regions to %s",
        len(ocr_result["grouped_regions"]),
        settings.REASONING_MODEL,
    )

    response = None
    for attempt in range(MAX_RETRIES):
        try:
            logger.info("PaddleIngestor: API call attempt %d/%d", attempt + 1, MAX_RETRIES)
            response = await model.generate_content_async(
                content_parts,
                generation_config=genai.GenerationConfig(
                    response_mime_type="application/json",
                    temperature=0.1,
                ),
                request_options={"timeout": 600},
            )
            break
        except ResourceExhausted:
            if attempt < MAX_RETRIES - 1:
                backoff = INITIAL_BACKOFF * (2 ** attempt)
                logger.warning("Rate limited. Waiting %ds before retry %d/%d", backoff, attempt + 2, MAX_RETRIES)
                await asyncio.sleep(backoff)
            else:
                logger.error("Rate limit exhausted after max retries")
                raise

    if response is None:
        raise RuntimeError("Failed to get response from Gemini API")

    resp_text = response.text or ""
    logger.info("PaddleIngestor: Gemini response length: %d chars", len(resp_text))

    # ---------- Phase 4: Parse JSON (same robust parsing as original) ----------
    def fix_json(text: str) -> dict:
        start = text.find("{")
        end = text.rfind("}") + 1
        if start < 0 or end <= start:
            return {}
        text = text[start:end]
        text = re.sub(r',\s*([}\]])', r'\1', text)
        text = re.sub(r':\s*None\b', ': null', text)
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return {}

    try:
        extracted = json.loads(resp_text)
        if isinstance(extracted, list):
            if len(extracted) == 1 and isinstance(extracted[0], dict):
                extracted = extracted[0]
            elif len(extracted) > 1:
                merged = {}
                for item in extracted:
                    if isinstance(item, dict):
                        for k, v in item.items():
                            if k in merged and isinstance(merged[k], list) and isinstance(v, list):
                                merged[k].extend(v)
                            else:
                                merged[k] = v
                extracted = merged
            else:
                extracted = {}
    except json.JSONDecodeError as e:
        logger.warning("JSON parse failed: %s, attempting fix", e)
        extracted = fix_json(resp_text)

    dims_count = len(extracted.get("dimensions", []))
    ocr_sourced = sum(1 for d in extracted.get("dimensions", []) if d.get("ocr_source"))
    logger.info(
        "PaddleIngestor: extracted %d dimensions (%d from OCR, %d from vision)",
        dims_count, ocr_sourced, dims_count - ocr_sourced,
    )

    # ---------- Phase 5: Post-processing (same as original ingestor) ----------
    part_list = extracted.get("part_list", [])
    entity_registry = _build_entity_registry(part_list)

    zones = extracted.get("zones", [])
    zones = _enrich_zones_with_grid(zones, img_size)
    extracted["zones"] = zones

    dimensions = extracted.get("dimensions", [])
    # Strip the ocr_source field before binding (not part of the schema)
    for d in dimensions:
        d.pop("ocr_source", None)
    dimensions = _bind_dimensions_to_entities(dimensions, entity_registry, img_size)
    dimensions = _validate_and_adjust_coordinates(dimensions, file_path, img_size)
    extracted["dimensions"] = dimensions

    gdt_callouts = extracted.get("gdt_callouts", [])
    for callout in gdt_callouts:
        coords = callout.get("coordinates") or {}
        scaled_coords = _scale_coordinates(coords, img_size[0], img_size[1])
        callout["coordinates"] = scaled_coords
        x, y = scaled_coords.get("x", 0), scaled_coords.get("y", 0)
        if not callout.get("grid_ref"):
            callout["grid_ref"] = _compute_grid_ref(x, y, img_size[0], img_size[1])
    extracted["gdt_callouts"] = gdt_callouts

    validation = _validate_extraction(extracted, entity_registry)

    if is_rescan and state.get("machine_state"):
        existing = state["machine_state"]
        for key in ["dimensions", "part_list", "gdt_callouts"]:
            if key in extracted and extracted[key]:
                existing[key] = extracted[key]
        machine_state = existing
    else:
        try:
            machine_state = MachineState(**extracted).model_dump()
        except Exception as e:
            logger.warning("MachineState validation failed: %s — using raw dict", e)
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
        "action": "paddle_ocr_extraction" if not is_rescan else "paddle_ocr_rescan",
        "zones_found": len(machine_state.get("zones", [])),
        "dimensions_found": len(machine_state.get("dimensions", [])),
        "parts_found": len(machine_state.get("part_list", [])),
        "gdt_callouts_found": len(machine_state.get("gdt_callouts", [])),
        "entity_binding": validation,
        "image_size": {"width": img_size[0], "height": img_size[1]},
        "ocr_summary": ocr_summary,
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
