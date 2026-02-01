"""Ingestor Agent – Gemini 1.5 Pro Vision extraction of mechanical drawings."""
from __future__ import annotations

import base64
import io
import json
from pathlib import Path

from PIL import Image
from PyPDF2 import PdfReader
import google.generativeai as genai

from app.config import settings
from app.agents.state import AuditState, MachineState

EXTRACTION_PROMPT = """You are an expert mechanical engineering drawing reader.
Analyze this technical drawing and extract ALL information in structured JSON.

Return JSON with these exact keys:
{
  "zones": [{"name": "Top View"|"Section A-A"|"Detail B"|..., "bounds": {"x1":0,"y1":0,"x2":100,"y2":100}, "features": ["bore","shaft",...]}],
  "dimensions": [{"value": 25.0, "unit": "mm", "tolerance_class": "H7"|null, "zone": "Top View", "item_number": "1"|null, "coordinates": {"x":50,"y":30}, "nominal": 25.0, "upper_tol": 0.021, "lower_tol": 0.0}],
  "part_list": [{"item_number": "1", "description": "Housing", "material": "Steel AISI 1045", "quantity": 1, "weight": 12.5, "weight_unit": "kg"}],
  "gdt_callouts": [{"symbol": "⌀", "value": 0.05, "datum": "A", "zone": "Top View", "coordinates": {"x":60,"y":40}}],
  "raw_text": "all readable text on the drawing",
  "title_block": {"title": "", "drawing_number": "", "revision": "", "scale": "", "material": "", "tolerance_general": ""}
}

Rules:
1. Parse the Part List / BOM first if visible
2. Segment the drawing into zones (views, sections, details)
3. Extract ALL dimensions with coordinates (approximate pixel positions)
4. Distinguish nominal dimensions from tolerances and GD&T symbols
5. Link dimensions to item numbers where possible
6. Be thorough – missing a dimension is worse than misreading one
"""

RESCAN_PROMPT = """You previously extracted data from this drawing but some values were suspect.
Focus specifically on the highlighted region and re-extract dimensions carefully.
Apply higher scrutiny to numerical values. Return the same JSON format as before but only for items in this region.
"""


def _configure_genai():
    genai.configure(api_key=settings.GOOGLE_API_KEY)


def _load_images(file_path: str, crop_region: dict | None = None) -> list:
    """Load drawing file and return list of PIL images."""
    path = Path(file_path)
    images = []

    if path.suffix.lower() == ".pdf":
        # For PDF, we convert pages to images using PyPDF2 metadata
        # and pass the PDF bytes directly to Gemini
        reader = PdfReader(str(path))
        # Gemini can handle PDFs directly, but we'll extract page count
        # and send as file
        with open(path, "rb") as f:
            pdf_bytes = f.read()
        return [{"mime_type": "application/pdf", "data": base64.b64encode(pdf_bytes).decode()}]
    else:
        img = Image.open(path)
        if crop_region:
            x1, y1 = crop_region.get("x1", 0), crop_region.get("y1", 0)
            x2, y2 = crop_region.get("x2", img.width), crop_region.get("y2", img.height)
            img = img.crop((x1, y1, x2, y2))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        images.append({"mime_type": "image/png", "data": base64.b64encode(buf.getvalue()).decode()})

    return images


async def run_ingestor(state: AuditState) -> AuditState:
    """Extract MachineState from drawing using Gemini Vision."""
    _configure_genai()

    file_path = state["file_path"]
    crop_region = state.get("crop_region")
    is_rescan = crop_region is not None

    prompt = RESCAN_PROMPT if is_rescan else EXTRACTION_PROMPT
    image_parts = _load_images(file_path, crop_region)

    model = genai.GenerativeModel(settings.VISION_MODEL)

    content_parts = []
    for img in image_parts:
        content_parts.append({"inline_data": img})
    content_parts.append(prompt)

    response = await model.generate_content_async(
        content_parts,
        generation_config=genai.GenerationConfig(
            response_mime_type="application/json",
            temperature=0.1,
        ),
    )

    def fix_json(text: str) -> dict:
        """Attempt to fix and parse malformed JSON from Gemini."""
        import re
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
            # Last resort: try to extract just dimensions
            dims_match = re.search(r'"dimensions"\s*:\s*\[(.*?)\]', text, re.DOTALL)
            if dims_match:
                try:
                    dims_text = '[' + dims_match.group(1) + ']'
                    dims_text = re.sub(r',\s*([}\]])', r'\1', dims_text)
                    dims = json.loads(dims_text)
                    return {"dimensions": dims, "zones": [], "part_list": [], "gdt_callouts": []}
                except:
                    pass
            return {}

    try:
        extracted = json.loads(response.text)
    except json.JSONDecodeError:
        extracted = fix_json(response.text)

    if is_rescan and state.get("machine_state"):
        # Merge rescan results into existing state
        existing = state["machine_state"]
        for key in ["dimensions", "part_list", "gdt_callouts"]:
            if key in extracted:
                existing[key] = extracted[key]
        machine_state = existing
    else:
        machine_state = MachineState(**extracted).model_dump()

    log_entry = {
        "agent": "ingestor",
        "action": "rescan" if is_rescan else "full_extraction",
        "zones_found": len(machine_state.get("zones", [])),
        "dimensions_found": len(machine_state.get("dimensions", [])),
        "parts_found": len(machine_state.get("part_list", [])),
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
