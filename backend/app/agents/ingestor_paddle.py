"""Layered PaddleOCR + Gemini Vision fusion ingestor.

Strategy (layered fusion):
1. PaddleOCR → precise text bounding boxes (accurate positions, but misses some)
2. Gemini Flash Vision → comprehensive extraction (catches everything, less precise coords)
3. Fusion → for each Gemini dimension, find closest PaddleOCR text and snap coordinates
   to the OCR bounding box center. Gemini's completeness + PaddleOCR's positioning.

This avoids the problem where:
- PaddleOCR alone misses dimensions (arrows, symbols it can't read)
- Gemini Vision alone has imprecise coordinate placement
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import re
from typing import Optional, List, Dict, Tuple

import google.generativeai as genai
from google.api_core.exceptions import ResourceExhausted

from app.config import settings
from app.agents.state import AuditState, MachineState
from app.agents.ocr_preprocess import run_paddle_ocr
from app.agents.ingestor import (
    run_ingestor,
    _configure_genai,
    _load_images,
    _compute_grid_ref,
    _build_entity_registry,
    _scale_coordinates,
    _bind_dimensions_to_entities,
    _enrich_zones_with_grid,
    _validate_and_adjust_coordinates,
    _validate_extraction,
)

logger = logging.getLogger(__name__)


def _fuzzy_number_match(gemini_val: float, ocr_text: str) -> bool:
    """Check if a Gemini dimension value matches an OCR text string."""
    if gemini_val is None:
        return False
    # Extract numbers from OCR text
    numbers = re.findall(r'[+-]?\d+\.?\d*', ocr_text)
    for num_str in numbers:
        try:
            ocr_val = float(num_str)
            if ocr_val == 0:
                continue
            # Exact match or very close
            if abs(gemini_val - ocr_val) < 0.01:
                return True
            # Within 1% (rounding differences)
            if abs(gemini_val - ocr_val) / max(abs(ocr_val), 0.001) < 0.01:
                return True
        except ValueError:
            continue
    return False


def _snap_to_ocr(
    dimensions: List[Dict],
    ocr_regions: List[Dict],
    img_w: int,
    img_h: int,
) -> Tuple[List[Dict], int, int]:
    """Snap Gemini dimension coordinates to nearest matching PaddleOCR text position.

    For each Gemini dimension:
    1. Find OCR regions whose text matches the dimension value
    2. Among matches, pick the one closest to Gemini's estimated position
    3. Replace coordinates with the OCR bounding box center (more precise)

    Returns (snapped_dimensions, snap_count, total_count).
    """
    if not ocr_regions:
        return dimensions, 0, len(dimensions)

    snap_count = 0

    for dim in dimensions:
        gemini_val = dim.get("value")
        gemini_coords = dim.get("coordinates", {})
        gx = gemini_coords.get("x", 0)
        gy = gemini_coords.get("y", 0)

        # Gemini coords are percentages (0-100), OCR coords are also percentages
        # Normalize: if Gemini coords look like pixels (>100), convert to pct
        if gx > 100 or gy > 100:
            gx_pct = (gx / img_w * 100) if img_w > 0 else gx
            gy_pct = (gy / img_h * 100) if img_h > 0 else gy
        else:
            gx_pct = gx
            gy_pct = gy

        best_ocr = None
        best_dist = float("inf")

        for region in ocr_regions:
            ocr_text = region.get("text", "")
            ocr_type = region.get("type", "")
            pos = region.get("position", {})
            ox_pct = pos.get("x_pct", 0)
            oy_pct = pos.get("y_pct", 0)

            # Skip non-dimension OCR regions
            if ocr_type in ("section_label", "material", "surface_finish"):
                continue

            # Check if the OCR text matches the dimension value
            value_matches = _fuzzy_number_match(gemini_val, ocr_text)

            if not value_matches:
                continue

            # Distance in percentage space
            dist = math.hypot(gx_pct - ox_pct, gy_pct - oy_pct)

            # Accept if within 15% of image (generous to catch offset estimates)
            if dist < 15 and dist < best_dist:
                best_dist = dist
                best_ocr = region

        if best_ocr:
            pos = best_ocr["position"]
            dim["coordinates"] = {"x": pos["x_pct"], "y": pos["y_pct"]}
            dim["_snapped_from_ocr"] = True
            dim["_snap_distance"] = round(best_dist, 2)
            snap_count += 1

    return dimensions, snap_count, len(dimensions)


async def run_ingestor_paddle(state: AuditState) -> AuditState:
    """Extract DMS using layered PaddleOCR + Gemini Vision fusion.

    Pipeline:
    1. PaddleOCR → precise text positions
    2. Gemini Flash Vision → comprehensive extraction (same as original ingestor)
    3. Fusion → snap Gemini coordinates to PaddleOCR positions
    4. Post-processing (grid refs, entity binding, coordinate validation)
    """
    file_path = state["file_path"]

    # ---------- Phase 1: PaddleOCR (precise positions) ----------
    logger.info("LayeredIngestor: Phase 1 — PaddleOCR on %s", file_path)
    ocr_result = None
    try:
        ocr_result = run_paddle_ocr(file_path)
        ocr_summary = ocr_result["summary"]
        logger.info(
            "LayeredIngestor: PaddleOCR found %d texts (%d dims, %d tols)",
            ocr_summary["total_texts"],
            ocr_summary["dimensions"],
            ocr_summary["tolerances"],
        )
    except Exception as e:
        logger.warning("PaddleOCR failed: %s — continuing with Gemini only", e)

    # ---------- Phase 2: Gemini Flash Vision (comprehensive) ----------
    logger.info("LayeredIngestor: Phase 2 — Gemini Flash extraction")
    gemini_result = await run_ingestor(state)

    machine_state = gemini_result.get("machine_state", {})
    if not machine_state:
        logger.warning("LayeredIngestor: Gemini returned empty machine_state")
        return gemini_result

    # ---------- Phase 3: Fusion — snap coordinates ----------
    if ocr_result and ocr_result.get("text_regions"):
        logger.info("LayeredIngestor: Phase 3 — Fusing coordinates")
        dimensions = machine_state.get("dimensions", [])
        img_w = ocr_result["image_size"]["width"]
        img_h = ocr_result["image_size"]["height"]

        dimensions, snap_count, total = _snap_to_ocr(
            dimensions, ocr_result["text_regions"], img_w, img_h
        )

        # Clean up internal fields
        for d in dimensions:
            d.pop("_snapped_from_ocr", None)
            d.pop("_snap_distance", None)

        machine_state["dimensions"] = dimensions

        logger.info(
            "LayeredIngestor: Snapped %d/%d dimension coordinates to PaddleOCR positions",
            snap_count, total,
        )

        # Update the agent log with fusion stats
        agent_log = gemini_result.get("agent_log", [])
        agent_log.append({
            "agent": "ingestor",
            "action": "paddle_ocr_fusion",
            "ocr_texts_found": ocr_result["summary"]["total_texts"],
            "dimensions_snapped": snap_count,
            "dimensions_total": total,
            "snap_rate": round(snap_count / max(total, 1) * 100, 1),
        })
        gemini_result["agent_log"] = agent_log
    else:
        logger.info("LayeredIngestor: No OCR data — using Gemini coordinates as-is")

    gemini_result["machine_state"] = machine_state
    return gemini_result
