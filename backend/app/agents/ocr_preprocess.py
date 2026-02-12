"""PaddleOCR pre-processing for mechanical drawings.

Runs PaddleOCR on a drawing image and returns structured text regions
with bounding boxes, confidence scores, and spatial grouping.
This gives downstream LLMs precise text locations instead of relying
on their own (often imprecise) OCR from vision input.
"""
from __future__ import annotations

import logging
import math
from pathlib import Path
from typing import List, Dict, Tuple, Optional

from PIL import Image

logger = logging.getLogger(__name__)

# Lazy-loaded singleton to avoid slow import at module level
_ocr_instance = None


def _get_ocr():
    """Lazy-init PaddleOCR (downloads model on first call)."""
    global _ocr_instance
    if _ocr_instance is None:
        from paddleocr import PaddleOCR
        _ocr_instance = PaddleOCR(
            use_angle_cls=True,
            lang="en",
            show_log=False,
            use_gpu=False,
        )
    return _ocr_instance


def _rasterize_pdf(pdf_path: str, dpi: int = 200) -> str:
    """Convert first page of PDF to PNG for PaddleOCR."""
    import fitz
    doc = fitz.open(pdf_path)
    page = doc[0]
    scale = dpi / 72.0
    mat = fitz.Matrix(scale, scale)
    pix = page.get_pixmap(matrix=mat)
    out_path = Path(pdf_path).with_suffix(".paddle_ocr.png")
    pix.save(str(out_path))
    doc.close()
    return str(out_path)


def _bbox_center(box: list) -> Tuple[float, float]:
    """Get center of a PaddleOCR bounding box [[x1,y1],[x2,y2],[x3,y3],[x4,y4]]."""
    xs = [p[0] for p in box]
    ys = [p[1] for p in box]
    return (sum(xs) / len(xs), sum(ys) / len(ys))


def _bbox_to_pct(box: list, img_w: int, img_h: int) -> Dict:
    """Convert bounding box to percentage coordinates."""
    xs = [p[0] for p in box]
    ys = [p[1] for p in box]
    cx, cy = sum(xs) / len(xs), sum(ys) / len(ys)
    return {
        "x_pct": round(cx / img_w * 100, 2),
        "y_pct": round(cy / img_h * 100, 2),
        "x1_pct": round(min(xs) / img_w * 100, 2),
        "y1_pct": round(min(ys) / img_h * 100, 2),
        "x2_pct": round(max(xs) / img_w * 100, 2),
        "y2_pct": round(max(ys) / img_h * 100, 2),
        "width_pct": round((max(xs) - min(xs)) / img_w * 100, 2),
        "height_pct": round((max(ys) - min(ys)) / img_h * 100, 2),
    }


def _classify_text(text: str) -> str:
    """Heuristic classification of OCR text for engineering drawings."""
    import re
    text_stripped = text.strip()

    # Dimension patterns
    if re.match(r'^[+-]?\d+\.?\d*\s*(mm|in|cm|m)?$', text_stripped):
        return "dimension"
    # Tolerance like +0.05 / -0.02
    if re.match(r'^[+-]\d+\.?\d*$', text_stripped):
        return "tolerance"
    # Diameter symbol — ⌀, Ø, ø, φ, Φ prefix or "dia" keyword
    if text_stripped.startswith(('Ø', 'ø', 'φ', 'Φ', '⌀')) or re.match(r'^[Dd]ia\.?\s*\d', text_stripped):
        return "diameter"
    # Radius — R prefix followed by number
    if re.match(r'^[Rr]\d+\.?\d*$', text_stripped):
        return "radius"
    # Angular — number followed by degree symbol, or just degrees
    if re.search(r'\d+\.?\d*\s*[°˚º]', text_stripped) or text_stripped.endswith(('°', '˚', 'º')):
        return "angular"
    # Chamfer — C prefix or NxN° pattern
    if re.match(r'^[Cc]\d+\.?\d*$', text_stripped) or re.match(r'^\d+\.?\d*\s*[xX×]\s*45', text_stripped):
        return "chamfer"
    # Thread spec — M10, M12x1.5, UNC, UNF patterns
    if re.match(r'^M\d+', text_stripped, re.IGNORECASE) or re.search(r'UN[CF]', text_stripped):
        return "thread"
    # Depth — depth symbol ↧ or "DEPTH" or "DP" keyword
    if '↧' in text_stripped or re.match(r'^(DEPTH|DP)\b', text_stripped, re.IGNORECASE):
        return "depth"
    # Thickness — THK, t= patterns
    if re.match(r'^(THK|t\s*=)', text_stripped, re.IGNORECASE) or 'thickness' in text_stripped.lower():
        return "thickness"
    # Tolerance class like H7, g6, js15
    if re.match(r'^[A-Za-z]{1,2}\d{1,2}$', text_stripped):
        return "tolerance_class"
    # GD&T symbols
    if any(s in text_stripped for s in ['⌀', '⏥', '⊥', '∥', '⊙', '◎', '⌖']):
        return "gdt"
    # Section labels like A-A, B-B
    if re.match(r'^[A-Z]-[A-Z]$', text_stripped):
        return "section_label"
    # Surface finish
    if text_stripped.startswith('Ra') or text_stripped.startswith('Rz'):
        return "surface_finish"
    # Material spec
    if any(kw in text_stripped.lower() for kw in ['steel', 'aluminum', 'brass', 'aisi', 'astm', 'en-']):
        return "material"

    return "text"


def _group_nearby_texts(
    regions: List[Dict], proximity_pct: float = 3.0
) -> List[Dict]:
    """Group OCR text regions that are close together (likely part of same callout).

    For example "25.0" next to "+0.05" next to "-0.02" = a single dimensioned tolerance.
    """
    if not regions:
        return regions

    used = [False] * len(regions)
    groups = []

    for i, r in enumerate(regions):
        if used[i]:
            continue
        group = [r]
        used[i] = True

        for j in range(i + 1, len(regions)):
            if used[j]:
                continue
            # Check if centers are close
            dx = abs(r["position"]["x_pct"] - regions[j]["position"]["x_pct"])
            dy = abs(r["position"]["y_pct"] - regions[j]["position"]["y_pct"])
            if dx < proximity_pct and dy < proximity_pct:
                group.append(regions[j])
                used[j] = True

        if len(group) == 1:
            groups.append(group[0])
        else:
            # Merge group into one region
            texts = [g["text"] for g in group]
            types = [g["type"] for g in group]
            confs = [g["confidence"] for g in group]
            # Use the dimension text's position as the group center
            dim_items = [g for g in group if g["type"] in ("dimension", "diameter")]
            anchor = dim_items[0] if dim_items else group[0]
            groups.append({
                "text": " ".join(texts),
                "parts": texts,
                "types": types,
                "type": "dimension_group" if any(t in ("dimension", "diameter") for t in types) else "text_group",
                "confidence": round(sum(confs) / len(confs), 3),
                "position": anchor["position"],
                "bbox": anchor["bbox"],
            })

    return groups


def run_paddle_ocr(file_path: str) -> Dict:
    """Run PaddleOCR on a drawing and return structured text regions.

    Returns:
        {
            "image_size": {"width": W, "height": H},
            "text_regions": [
                {
                    "text": "25.0",
                    "type": "dimension",
                    "confidence": 0.97,
                    "position": {"x_pct": 34.2, "y_pct": 55.1, ...},
                    "bbox": [[x1,y1], [x2,y2], [x3,y3], [x4,y4]]
                },
                ...
            ],
            "grouped_regions": [...],  # nearby texts merged
            "summary": {
                "total_texts": N,
                "dimensions": N,
                "tolerances": N,
                "gdt": N,
            }
        }
    """
    path = Path(file_path)

    # Rasterize PDF if needed
    if path.suffix.lower() == ".pdf":
        img_path = _rasterize_pdf(file_path)
    else:
        img_path = file_path

    img = Image.open(img_path)
    img_w, img_h = img.size

    logger.info("PaddleOCR: processing %s (%dx%d)", path.name, img_w, img_h)

    ocr = _get_ocr()
    results = ocr.ocr(img_path, cls=True)

    if not results or not results[0]:
        logger.warning("PaddleOCR: no text detected")
        return {
            "image_size": {"width": img_w, "height": img_h},
            "text_regions": [],
            "grouped_regions": [],
            "summary": {"total_texts": 0, "dimensions": 0, "tolerances": 0, "gdt": 0},
        }

    text_regions = []
    type_counts = {}

    for line in results[0]:
        bbox = line[0]  # [[x1,y1],[x2,y2],[x3,y3],[x4,y4]]
        text = line[1][0]
        confidence = line[1][1]

        text_type = _classify_text(text)
        type_counts[text_type] = type_counts.get(text_type, 0) + 1

        region = {
            "text": text,
            "type": text_type,
            "confidence": round(confidence, 3),
            "position": _bbox_to_pct(bbox, img_w, img_h),
            "bbox": bbox,
        }
        text_regions.append(region)

    # Sort by position (top to bottom, left to right)
    text_regions.sort(key=lambda r: (r["position"]["y_pct"], r["position"]["x_pct"]))

    grouped = _group_nearby_texts(text_regions)

    logger.info(
        "PaddleOCR: found %d text regions (%d dimensions, %d tolerances, %d GDT)",
        len(text_regions),
        type_counts.get("dimension", 0) + type_counts.get("diameter", 0),
        type_counts.get("tolerance", 0) + type_counts.get("tolerance_class", 0),
        type_counts.get("gdt", 0),
    )

    return {
        "image_size": {"width": img_w, "height": img_h},
        "text_regions": text_regions,
        "grouped_regions": grouped,
        "summary": {
            "total_texts": len(text_regions),
            "dimensions": type_counts.get("dimension", 0) + type_counts.get("diameter", 0),
            "tolerances": type_counts.get("tolerance", 0) + type_counts.get("tolerance_class", 0),
            "gdt": type_counts.get("gdt", 0),
        },
    }
