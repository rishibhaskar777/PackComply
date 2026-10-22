"""Second OCR pass for date stamps the first pass could not read.

Dot-matrix and inkjet stamps ("Mfg. Date: 05..2026") often come back from the whole-photo pass as digits
without separators ("05026", "042028") or not at all.  When a manufacture, packing or expiry KEY was read but
its value was not, this pass re-reads one small crop around the keys (at most two crops per photo, at most
two image variants per crop).  Crops are *reduced* to about 40 px of key height and never enlarged: on a
reduced crop the dots of a stamp merge into strokes, while an enlarged crop separates them and reads worse.

What a crop may add, strongest first:

1. A clean date with separators ("05/2026", "04-2028", confidence >= 0.8): replaces the garbled reading as an
   ordinary detection.
2. An unseparated six-digit value ("042028"): only when it sits level with a manufacture/packing/expiry key (and
   no Batch/Lot key is nearer), month 01-12, year 2020-2040, confidence >= 0.9.  It is tagged ``recovery`` so
   extraction records it as a suggestion to confirm, never as a value the rules can pass on.
3. Anything else ("05026", a lost digit) is dropped: nothing is guessed.

Only local libraries are used (Pillow, OpenCV, the already-loaded PaddleOCR engine).
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
import logging
from pathlib import Path
import re
import tempfile
from time import perf_counter
from typing import Callable

logger = logging.getLogger("uvicorn.error")

DATE_FIELDS = ("manufacturing_date", "packing_date", "expiry_date")
# A key's value is "missing" when none of these fields holds a value.
_COVERING = {"manufacturing_date": ("manufacturing_date", "packing_date"), "packing_date": ("manufacturing_date", "packing_date"), "expiry_date": ("expiry_date",)}
MAX_CROPS = 2
TARGET_KEY_HEIGHT = 40.0       # crops are reduced so the key text is about this tall, never enlarged
MIN_CLEAN_CONFIDENCE = 0.8
MIN_GUESS_CONFIDENCE = 0.9
GUESS_YEARS = (2020, 2040)
CLEAN_YEARS = (2000, 2099)
CROP_WIDTH_FACTOR = 20.0       # crop width in key heights: the key column plus the stamp column
CROP_ABOVE_FACTOR = 1.5        # stamps sit up to about a row above their key
CROP_BELOW_FACTOR = 0.5
CLUSTER_GAP_FACTOR = 6.0       # keys further apart than this many key heights get their own crop
_SIX_DIGITS = re.compile(r"[0-9]{6}")
_SEPARATED = re.compile(r"[0-9]{1,2}\s*[/.\-]\s*[0-9]{2,4}|[0-9]{1,2}\s*[/.\-]\s*[0-9]{1,2}\s*[/.\-]\s*[0-9]{2,4}")
_BATCH_FIELDS = ("batch_number",)

Reader = Callable[[Path], list]


@dataclass
class RecoveryResult:
    detections: list
    crops: int = 0
    reads: int = 0
    seconds: float = 0.0
    accepted: list[str] = field(default_factory=list)


def _extent(bbox) -> tuple[float, float, float, float]:
    xs, ys = [point[0] for point in bbox], [point[1] for point in bbox]
    return min(xs), min(ys), max(xs), max(ys)


def _box(x0: float, y0: float, x1: float, y1: float) -> list[list[float]]:
    return [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]


def _overlap(a, b) -> float:
    """Share of the smaller box covered by the other."""
    width, height = min(a[2], b[2]) - max(a[0], b[0]), min(a[3], b[3]) - max(a[1], b[1])
    if width <= 0 or height <= 0:
        return 0.0
    return width * height / max(min((a[2] - a[0]) * (a[3] - a[1]), (b[2] - b[0]) * (b[3] - b[1])), 1.0)


def missing_date_keys(raw_text: str, detections: list) -> list:
    """Date keys whose value the first pass did not read, upright keys only (a rotated photo is left alone)."""
    from backend.app.services.extraction import ExtractionService
    from backend.app.services.extraction import values as value_first
    from backend.app.services.extraction.layout import Layout

    if any(getattr(item, "rotation", 0) for item in detections):
        return []
    layout = Layout.build(raw_text, detections)
    if not layout.geometric:
        return []
    fields = ExtractionService().extract(raw_text, detections)
    return [key for key in value_first.find_keys(layout)
            if key.field in DATE_FIELDS and key.box is not None and not any(fields[name].value for name in _COVERING[key.field])]


def _all_keys(raw_text: str, detections: list) -> list:
    from backend.app.services.extraction import values as value_first
    from backend.app.services.extraction.layout import Layout

    layout = Layout.build(raw_text, detections)
    return [key for key in value_first.find_keys(layout) if key.box is not None] + [
        # "Batch No." / "Lot No.": the keys a six-digit stamp may belong to instead of a date.
        key for key in _batch_keys(layout)]


def _batch_keys(layout) -> list:
    from backend.app.services.extraction import values as value_first

    keys = []
    for line in layout.lines:
        for match in re.finditer(r"(?i)(?<![A-Za-z])(?:batch|lot|b\.?\s*no)\b\.?\s*(?:no|number)?\.?", line.text):
            span = value_first.Span(line, match.start(), match.end())
            keys.append(value_first.Key("batch_number", span, line.span_extent(match.start(), match.end()), 0, "batch no"))
    return keys


def _clusters(keys: list) -> list[list]:
    keys = sorted(keys, key=lambda key: key.box[1])
    clusters: list[list] = []
    for key in keys:
        height = max(key.box[3] - key.box[1], 1.0)
        if clusters and key.box[1] - max(item.box[3] for item in clusters[-1]) <= CLUSTER_GAP_FACTOR * height:
            clusters[-1].append(key)
        else:
            clusters.append([key])
    return clusters[:MAX_CROPS]


def _region(cluster: list, width: int, height: int) -> tuple[int, int, int, int]:
    key_height = max(max(key.box[3] - key.box[1] for key in cluster), 1.0)
    x0 = min(key.box[0] for key in cluster) - 0.3 * key_height
    y0 = min(key.box[1] for key in cluster) - CROP_ABOVE_FACTOR * key_height
    y1 = max(key.box[3] for key in cluster) + CROP_BELOW_FACTOR * key_height
    return int(max(x0, 0)), int(max(y0, 0)), int(min(x0 + CROP_WIDTH_FACTOR * key_height, width)), int(min(y1, height))


def _variants(crop, scale: float):
    """Plain reduced greyscale, then the same lightly smoothed so stamp dots join into strokes."""
    import cv2
    import numpy as np
    from PIL import Image

    grey = crop.convert("L")
    if scale < 1.0:
        grey = grey.resize((max(int(grey.width * scale), 1), max(int(grey.height * scale), 1)), Image.LANCZOS)
    yield "plain", grey
    yield "smoothed", Image.fromarray(cv2.GaussianBlur(np.array(grey), (0, 0), 0.8))


def _clean_date(text: str, confidence: float | None) -> str | None:
    from backend.app.services.extraction.values import normalize_date

    text = text.strip()
    if confidence is None or confidence < MIN_CLEAN_CONFIDENCE or not _SEPARATED.fullmatch(text):
        return None
    normalized = normalize_date(re.sub(r"\s+", "", text))
    year = int(normalized[:4]) if normalized and normalized[:1].isdigit() else None
    return text if year and CLEAN_YEARS[0] <= year <= CLEAN_YEARS[1] else None


def six_digit_suggestion(text: str, confidence: float | None) -> str | None:
    """"042028" -> "04/2028" when it is exactly six digits, month 01-12, year 2020-2040 and read at >= 0.9."""
    text = text.strip()
    if confidence is None or confidence < MIN_GUESS_CONFIDENCE or not _SIX_DIGITS.fullmatch(text):
        return None
    month, year = int(text[:2]), int(text[2:])
    return f"{text[:2]}/{text[2:]}" if 1 <= month <= 12 and GUESS_YEARS[0] <= year <= GUESS_YEARS[1] else None


def _owner_key(extent, keys: list):
    """The key a stamp is level with: the nearest row, to the right of the key.  None when no key is that close."""
    best = None
    for key in keys:
        height = max(key.box[3] - key.box[1], 1.0)
        dy = abs((extent[1] + extent[3]) / 2 - (key.box[1] + key.box[3]) / 2)
        if dy <= 0.8 * height and extent[0] >= key.box[2] - 0.5 * height and (best is None or dy < best[0]):
            best = (dy, key)
    return best[1] if best else None


def recover_dates(image_path: Path, raw_text: str, detections: list, read: Reader) -> RecoveryResult:
    """Detections of the photo plus whatever the second pass may add; ``detections`` is returned unchanged if nothing qualifies.

    ``read`` OCRs one image file and returns detections in that file's pixels.
    """
    from PIL import Image, ImageOps

    started = perf_counter()
    result = RecoveryResult(list(detections))
    missing = missing_date_keys(raw_text, detections)
    if not missing:
        return result
    all_keys = _all_keys(raw_text, detections)
    with Image.open(image_path) as source:
        image = ImageOps.exif_transpose(source).convert("RGB")
    clean: list = []
    guesses: list = []
    with tempfile.TemporaryDirectory(prefix="packcomply-dates-") as workdir:
        for index, cluster in enumerate(_clusters(missing)):
            x0, y0, x1, y1 = _region(cluster, image.width, image.height)
            if x1 - x0 < 20 or y1 - y0 < 10:
                continue
            result.crops += 1
            key_height = max(key.box[3] - key.box[1] for key in cluster)
            scale = min(1.0, TARGET_KEY_HEIGHT / max(key_height, 1.0))
            crop = image.crop((x0, y0, x1, y1))
            for name, variant in _variants(crop, scale):
                path = Path(workdir) / f"crop{index}_{name}.png"
                variant.save(path)
                result.reads += 1
                for item in read(path):
                    box = _box(*[(value / scale) + offset for value, offset in zip(_extent(item.bbox), (x0, y0, x0, y0))]) if item.bbox else None
                    if box is None:
                        continue
                    moved = replace(item, bbox=box, language=item.language or "en")
                    if _clean_date(item.text, item.confidence) and _owner_key(_extent(box), [key for key in all_keys if key.field in DATE_FIELDS]) is not None:
                        clean.append(moved)
                    elif (suggestion := six_digit_suggestion(item.text, item.confidence)):
                        owner = _owner_key(_extent(box), all_keys)
                        if owner is not None and owner.field in DATE_FIELDS:
                            guesses.append(replace(moved, recovery={"kind": "date_guess", "field": owner.field, "suggestion": suggestion}))
                # Stop when every missing key has a clean date beside it.
                if len({id(_owner_key(_extent(item.bbox), missing)) for item in clean} - {id(None)}) >= len(missing):
                    break
    kept = list(detections)
    for item in sorted(clean, key=lambda item: -(item.confidence or 0)):
        extent = _extent(item.bbox)
        if any(_overlap(extent, _extent(other.bbox)) >= 0.5 for other in kept if other.bbox and getattr(other, "recovery", None)):
            continue  # a better reading of the same stamp is already in
        overlapping = [other for other in kept if other.bbox and _overlap(extent, _extent(other.bbox)) >= 0.3]
        if any(_clean_date(other.text, other.confidence) for other in overlapping):
            continue  # the first pass already read this date: re-reading it adds nothing
        kept = [other for other in kept if other not in overlapping]
        kept.append(replace(item, recovery={"kind": "date_read"}))
        result.accepted.append(item.text)
    for item in guesses:
        extent = _extent(item.bbox)
        if any(_overlap(extent, _extent(other.bbox)) >= 0.5 for other in kept if other.bbox and getattr(other, "recovery", None)):
            continue
        original = next((other for other in kept if other.bbox and other.text.strip() == item.text.strip() and _overlap(extent, _extent(other.bbox)) >= 0.3), None)
        if original is not None:
            kept[kept.index(original)] = replace(original, recovery=item.recovery)
        else:
            kept = [other for other in kept if not (other.bbox and _overlap(extent, _extent(other.bbox)) >= 0.3)]
            kept.append(item)
        result.accepted.append(f"{item.text} (guess {item.recovery['suggestion']})")
    result.detections = kept
    result.seconds = perf_counter() - started
    logger.info("OCR date recovery crops=%d reads=%d accepted=%s seconds=%.2f", result.crops, result.reads, result.accepted, result.seconds)
    return result
