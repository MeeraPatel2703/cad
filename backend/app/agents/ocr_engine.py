"""Hybrid OCR engine combining traditional (Tesseract) and CNN-based (EasyOCR) approaches."""

import logging
import re
from typing import List, Dict, Tuple, Optional
import cv2
import numpy as np
from PIL import Image

logger = logging.getLogger(__name__)

# Lazy load EasyOCR to avoid startup overhead
_easyocr_reader = None


def _get_easyocr_reader():
    """Lazy-load EasyOCR reader (CNN-based) for character recognition."""
    global _easyocr_reader
    if _easyocr_reader is None:
        import easyocr
        logger.info("Initializing EasyOCR CNN model (one-time setup)...")
        # Use English only, GPU=False for CPU efficiency
        _easyocr_reader = easyocr.Reader(['en'], gpu=False, verbose=False)
        logger.info("EasyOCR CNN model loaded")
    return _easyocr_reader


def extract_dimensions_with_cnn(
    image_path: str,
    region: Optional[Dict] = None
) -> List[Dict]:
    """Extract dimension text using CNN-based OCR (EasyOCR).

    EasyOCR uses a CNN architecture:
    - Feature extraction: ResNet-based backbone
    - Text detection: CRAFT (Character Region Awareness)
    - Text recognition: LSTM + CTC decoder

    Better than Tesseract for:
    - Small fonts (6-10pt)
    - Rotated text
    - Low-contrast images
    - Similar characters (0/O, 1/I/l)
    """
    reader = _get_easyocr_reader()

    # Load image
    img = cv2.imread(image_path)
    if img is None:
        logger.error(f"Could not load image: {image_path}")
        return []

    # Apply region crop if specified
    if region:
        x1, y1 = region.get("x1", 0), region.get("y1", 0)
        x2, y2 = region.get("x2", img.shape[1]), region.get("y2", img.shape[0])
        img = img[y1:y2, x1:x2]

    # EasyOCR works best on preprocessed images
    # Convert to grayscale
    if len(img.shape) == 3:
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    else:
        gray = img

    # Run CNN-based OCR
    # detail=1 returns bounding boxes, detail=0 returns just text
    results = reader.readtext(
        gray,
        detail=1,
        paragraph=False,  # Get individual text regions
        min_size=10,      # Minimum text height in pixels
        text_threshold=0.7,  # Confidence threshold
        low_text=0.4,     # Detection threshold
    )

    # Parse results
    dimensions = []
    for (bbox, text, confidence) in results:
        # Extract bounding box coordinates
        # bbox is [[x1,y1], [x2,y2], [x3,y3], [x4,y4]]
        x_coords = [point[0] for point in bbox]
        y_coords = [point[1] for point in bbox]

        x_min, x_max = int(min(x_coords)), int(max(x_coords))
        y_min, y_max = int(min(y_coords)), int(max(y_coords))

        # Center of bounding box
        center_x = (x_min + x_max) // 2
        center_y = (y_min + y_max) // 2

        # Try to parse as dimension value
        # Look for numbers with optional decimal points
        numbers = re.findall(r'\d+\.?\d*', text)

        for num_str in numbers:
            try:
                value = float(num_str)
                dimensions.append({
                    "value": value,
                    "text": text,
                    "confidence": confidence,
                    "coordinates": {"x": center_x, "y": center_y},
                    "bbox": {
                        "x": x_min,
                        "y": y_min,
                        "width": x_max - x_min,
                        "height": y_max - y_min
                    },
                    "method": "cnn_easyocr"
                })
            except ValueError:
                continue

    avg_conf = np.mean([d['confidence'] for d in dimensions]) if dimensions else 0.0
    logger.info(
        f"EasyOCR (CNN) extracted {len(dimensions)} dimension values "
        f"with avg confidence {avg_conf:.2f}"
    )

    return dimensions


def extract_dimensions_hybrid(
    image_path: str,
    use_tesseract: bool = True,
    use_cnn: bool = True
) -> Tuple[List[Dict], List[Dict]]:
    """Hybrid approach: combine Tesseract (fast, traditional) and EasyOCR (accurate, CNN).

    Strategy:
    - Tesseract: Fast, good for clean text, standard fonts
    - EasyOCR (CNN): Slower, better for small/rotated/low-quality text

    Returns both results for ensemble validation.
    """
    tesseract_dims = []
    cnn_dims = []

    if use_tesseract:
        # Use existing Tesseract extraction
        import pytesseract
        from pytesseract import Output

        img = cv2.imread(image_path, cv2.IMREAD_GRAYSCALE)
        if img is not None:
            try:
                ocr_data = pytesseract.image_to_data(img, output_type=Output.DICT)

                for i in range(len(ocr_data['text'])):
                    text = ocr_data['text'][i].strip()
                    if not text or ocr_data['conf'][i] < 30:
                        continue

                    numbers = re.findall(r'\d+\.?\d*', text)
                    for num_str in numbers:
                        try:
                            value = float(num_str)
                            tesseract_dims.append({
                                "value": value,
                                "text": text,
                                "confidence": ocr_data['conf'][i] / 100.0,
                                "coordinates": {
                                    "x": ocr_data['left'][i] + ocr_data['width'][i] // 2,
                                    "y": ocr_data['top'][i] + ocr_data['height'][i] // 2
                                },
                                "method": "tesseract"
                            })
                        except ValueError:
                            continue
            except Exception as e:
                logger.warning(f"Tesseract extraction failed: {e}")
        else:
            logger.warning(f"Could not load image for Tesseract: {image_path}")

    if use_cnn:
        cnn_dims = extract_dimensions_with_cnn(image_path)

    logger.info(
        f"Hybrid OCR: Tesseract found {len(tesseract_dims)}, "
        f"CNN found {len(cnn_dims)} dimensions"
    )

    return tesseract_dims, cnn_dims


def ensemble_validate(
    gemini_dims: List[Dict],
    tesseract_dims: List[Dict],
    cnn_dims: List[Dict],
    consensus_threshold: int = 2
) -> List[Dict]:
    """Ensemble validation: cross-check Gemini, Tesseract, and CNN results.

    A dimension is validated if at least `consensus_threshold` methods agree.
    """
    validated = []

    for g_dim in gemini_dims:
        g_value = g_dim.get("value")
        if g_value is None:
            validated.append(g_dim)
            continue

        try:
            g_value = float(g_value)
        except (ValueError, TypeError):
            validated.append(g_dim)
            continue

        g_coords = g_dim.get("coordinates", {})

        # Find matching values from other methods (within 50px radius)
        matches = {"gemini": True, "tesseract": False, "cnn": False}

        # Check Tesseract
        for t_dim in tesseract_dims:
            if g_value == 0 and t_dim["value"] == 0:
                matches["tesseract"] = True
                break
            if g_value != 0 and abs(g_value - t_dim["value"]) / max(abs(g_value), abs(t_dim["value"])) < 0.01:
                t_coords = t_dim.get("coordinates", {})
                dist = ((g_coords.get("x", 0) - t_coords.get("x", 0)) ** 2 +
                       (g_coords.get("y", 0) - t_coords.get("y", 0)) ** 2) ** 0.5
                if dist < 50:
                    matches["tesseract"] = True
                    break

        # Check CNN
        for c_dim in cnn_dims:
            if g_value == 0 and c_dim["value"] == 0:
                matches["cnn"] = True
                break
            if g_value != 0 and abs(g_value - c_dim["value"]) / max(abs(g_value), abs(c_dim["value"])) < 0.01:
                c_coords = c_dim.get("coordinates", {})
                dist = ((g_coords.get("x", 0) - c_coords.get("x", 0)) ** 2 +
                       (g_coords.get("y", 0) - c_coords.get("y", 0)) ** 2) ** 0.5
                if dist < 50:
                    matches["cnn"] = True
                    break

        # Count consensus
        consensus_count = sum(matches.values())

        g_dim["validation_methods"] = matches
        g_dim["consensus_count"] = consensus_count
        g_dim["validated"] = consensus_count >= consensus_threshold

        if consensus_count >= consensus_threshold:
            g_dim["confidence"] = min(g_dim.get("confidence", 1.0) * 1.2, 1.0)
            logger.info(f"Value {g_value} validated by {consensus_count}/3 methods")
        else:
            g_dim["confidence"] = g_dim.get("confidence", 1.0) * 0.6
            logger.warning(
                f"Value {g_value} only validated by {consensus_count}/3 methods: {matches}"
            )

        validated.append(g_dim)

    return validated
