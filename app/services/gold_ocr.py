"""Free, offline OCR of a Cambodian gold-shop price board -> structured rows.

RapidOCR (ONNX runtime; no PyTorch) reads the numbers; the table is rebuilt by
clustering detections into rows (by Y) and columns (by X). The board is a fixed
template, so row *labels* come from order — only the prices are OCR'd, which is
where OCR is reliable. Khmer text is never OCR'd. Mirrors the standalone tool in
the repo's `gold-ocr/` folder.

Heavy deps (rapidocr-onnxruntime, opencv) are imported lazily so the backend
still boots (and every other feature works) if they're not installed — the
Telegram gold-photo handler just reports the feature is unavailable.
"""
import logging
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

# Fixed template for the current shop (Hak Kunthea), rows top -> bottom.
ROW_LABELS = [
    {"type_km": "មាសគីឡូ (ដុំ)",    "type_en": "Gold bar (dom)"},
    {"type_km": "មាសគីឡូ (គ្រឿង)", "type_en": "Gold jewelry (kreung)"},
    {"type_km": "ជ្ផាទីនទឹក ៧",     "type_en": "Cha Tin 7"},
    {"type_km": "ជ្ផាទីនទឹក ៧ VN",  "type_en": "Cha Tin 7 VN"},
]

# Plausible USD-per-chi window — filters stray digits (phone numbers, the year,
# address numbers) so only real prices survive.
PRICE_MIN, PRICE_MAX = 100, 1500

_engine = None


def _get_engine():
    global _engine
    if _engine is None:
        from rapidocr_onnxruntime import RapidOCR  # lazy: heavy import
        _engine = RapidOCR()
    return _engine


def parse_gold_board(image_bytes: bytes, top: float = 0.42, bottom: float = 0.80) -> List[Dict[str, Any]]:
    """OCR a board image (raw bytes) into rows:
    `[{type_km, type_en, sell_out, buy_in}, ...]`. `sell_out` = លក់ចេញ (ask side),
    `buy_in` = ទិញចូល (bid side). Returns [] if nothing readable."""
    import cv2  # lazy
    import numpy as np

    arr = np.frombuffer(image_bytes, np.uint8)
    bgr = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if bgr is None:
        return []
    h, w = bgr.shape[:2]

    # Yellow-on-charcoal -> dark-on-light (also wipes the watermark).
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    _, th = cv2.threshold(cv2.bitwise_not(gray), 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    proc = cv2.cvtColor(th, cv2.COLOR_GRAY2BGR)

    result, _ = _get_engine()(proc)
    result = result or []

    y_lo, y_hi = top * h, bottom * h
    prices = []
    for box, text, _conf in result:
        if "/" in text or ":" in text:  # dates / times
            continue
        num = "".join(ch for ch in text if ch.isdigit())
        if not num:
            continue
        val = int(num)
        if not (PRICE_MIN <= val <= PRICE_MAX):
            continue
        ys = [p[1] for p in box]
        cy = sum(ys) / 4.0
        if not (y_lo <= cy <= y_hi):
            continue
        xs = [p[0] for p in box]
        prices.append({"val": val, "cx": sum(xs) / 4.0, "cy": cy})

    # Cluster into rows by Y proximity.
    prices.sort(key=lambda d: d["cy"])
    rows: List[Dict[str, Any]] = []
    for d in prices:
        if rows and abs(d["cy"] - rows[-1]["cy"]) < 0.045 * h:
            rows[-1]["items"].append(d)
            rows[-1]["cy"] = sum(i["cy"] for i in rows[-1]["items"]) / len(rows[-1]["items"])
        else:
            rows.append({"cy": d["cy"], "items": [d]})

    out = []
    for i, row in enumerate(rows):
        cols = sorted(row["items"], key=lambda d: d["cx"])
        vals = [c["val"] for c in cols][-2:]  # rightmost two columns = sell, buy
        label = ROW_LABELS[i] if i < len(ROW_LABELS) else {
            "type_km": f"row{i + 1}", "type_en": f"row{i + 1}"}
        out.append({
            **label,
            "sell_out": vals[0] if len(vals) >= 1 else None,
            "buy_in": vals[1] if len(vals) >= 2 else None,
        })
    return out
