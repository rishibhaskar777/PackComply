"""Reading-order lines built from OCR boxes, with the geometry needed to find a
key's value beside or below it.

Without boxes (legacy or text-only OCR) the layout degrades to the plain
newline order: "below" is simply the next line and nothing is "to the right".
"""
from __future__ import annotations

from dataclasses import dataclass, replace
import re
from statistics import median
from typing import Any, Iterable

from backend.app.services.ocr.ocr_service import LANGUAGE_SCRIPTS, OcrDetection, bbox_containment, bbox_extent, bbox_iou

# Recogniser output below this confidence is mostly artwork noise ("1h", "51").
MIN_DETECTION_CONFIDENCE = 0.5
# Boxes on one row join into a line when the horizontal gap is at most this many box heights.
JOIN_GAP_FACTOR = 2.0
# Share of the smaller height two boxes must share vertically to sit on one row.
ROW_OVERLAP = 0.6
# A value below a key is at most this many key-line heights further down.
BELOW_GAP_FACTOR = 2.5
# A value to the right of a key is at most this many key-line heights away.
RIGHT_GAP_FACTOR = 8.0
# A box of at least three characters this many times taller than wide is
# rotated side text (e.g. "scan QR code" printed up a panel edge).
VERTICAL_ASPECT = 1.5
_DUPLICATE_IOU = 0.3
_DUPLICATE_CONTAINMENT = 0.6
_SCRIPTS = {name: re.compile(f"[{block}]") for name, block in {**LANGUAGE_SCRIPTS, "latin": "A-Za-z"}.items()}


def clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", value.replace(" ", " ").replace("​", "")).strip()


@dataclass(frozen=True)
class LayoutBox:
    text: str
    confidence: float | None
    bbox: list[list[float]] | None
    language: str | None = None
    # Degrees the photo was turned to read this text (OcrDetection.rotation).
    rotation: int = 0
    # Extent in the upright reading frame when the photo was read rotated;
    # ``bbox`` stays in original photo pixels for evidence.
    frame: tuple[float, float, float, float] | None = None

    @property
    def extent(self) -> tuple[float, float, float, float]:
        return self.frame or bbox_extent(self.bbox) or (0.0, 0.0, 0.0, 0.0)

    @property
    def height(self) -> float:
        return max(self.extent[3] - self.extent[1], 1.0)

    @property
    def center_y(self) -> float:
        return (self.extent[1] + self.extent[3]) / 2

    @property
    def vertical(self) -> bool:
        x0, y0, x1, y1 = self.extent
        return len(self.text.replace(" ", "")) >= 3 and (y1 - y0) >= VERTICAL_ASPECT * max(x1 - x0, 1.0)


@dataclass(frozen=True)
class LayoutLine:
    index: int
    boxes: tuple[LayoutBox, ...]
    text: str
    extent: tuple[float, float, float, float] | None = None

    @property
    def bboxes(self) -> list[list[list[float]]]:
        return [box.bbox for box in self.boxes if box.bbox]

    @property
    def height(self) -> float:
        return max(self.extent[3] - self.extent[1], 1.0) if self.extent else 1.0

    @property
    def box_height(self) -> float:
        """Typical glyph height: the tallest box, since rows of a line can be slightly offset."""
        return max((box.height for box in self.boxes if box.bbox), default=self.height)

    @property
    def center_y(self) -> float:
        return (self.extent[1] + self.extent[3]) / 2 if self.extent else float(self.index)

    @property
    def vertical(self) -> bool:
        return bool(self.boxes) and all(box.vertical for box in self.boxes)


def _vertical_overlap(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> float:
    return max(min(a[3], b[3]) - max(a[1], b[1]), 0.0)


def _horizontal_overlap(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> float:
    return max(min(a[2], b[2]) - max(a[0], b[0]), 0.0)


def _union(extents: Iterable[tuple[float, float, float, float]]) -> tuple[float, float, float, float]:
    items = list(extents)
    return min(e[0] for e in items), min(e[1] for e in items), max(e[2] for e in items), max(e[3] for e in items)


def _scripts(text: str) -> set[str]:
    return {name for name, pattern in _SCRIPTS.items() if pattern.search(text)}


def _as_box(item: Any) -> LayoutBox | None:
    if isinstance(item, OcrDetection):
        text, confidence, bbox, language, rotation = item.text, item.confidence, item.bbox, item.language, item.rotation
    elif isinstance(item, dict):
        text, confidence, bbox, language, rotation = item.get("text"), item.get("confidence"), item.get("bbox"), item.get("language"), item.get("rotation") or 0
    else:
        return None
    text = clean_text(str(text or ""))
    if not text or (confidence is not None and confidence < MIN_DETECTION_CONFIDENCE):
        return None
    return LayoutBox(text, confidence, bbox or None, language, int(rotation))


def upright(boxes: list[LayoutBox]) -> list[LayoutBox]:
    """Give boxes read on a rotated photo their extent in the upright reading frame.

    Original pixels (x, y) of a photo turned 90 degrees clockwise read at
    (-y, x); turned 270 degrees, at (y, -x).  Only relative geometry matters,
    so the frame is shifted to start at zero.
    """
    if not any(box.rotation for box in boxes):
        return boxes
    turn = {90: lambda x, y: (-y, x), 270: lambda x, y: (y, -x), 180: lambda x, y: (-x, -y)}
    frames = []
    for box in boxes:
        points = [turn.get(box.rotation, lambda x, y: (x, y))(x, y) for x, y in box.bbox or []]
        frames.append((min(p[0] for p in points), min(p[1] for p in points), max(p[0] for p in points), max(p[1] for p in points)) if points else None)
    left = min(frame[0] for frame in frames if frame)
    top = min(frame[1] for frame in frames if frame)
    return [replace(box, frame=(frame[0] - left, frame[1] - top, frame[2] - left, frame[3] - top) if frame else None) for box, frame in zip(boxes, frames)]


def suppress_duplicates(boxes: list[LayoutBox]) -> list[LayoutBox]:
    """Drop later boxes that re-read an earlier box's region in the same script.

    OCR results stored before cross-language de-duplication contain a second
    language's re-reading of the English text; stored order is primary first,
    so the earlier box is kept.  A box adding a script its overlapping boxes
    lack (Devanagari beside English) is kept.
    """
    kept: list[LayoutBox] = []
    for box in boxes:
        overlapping = [
            other for other in kept
            if bbox_iou(box.bbox, other.bbox) >= _DUPLICATE_IOU or bbox_containment(box.bbox, other.bbox) >= _DUPLICATE_CONTAINMENT
        ]
        covered = set().union(*(_scripts(other.text) for other in overlapping)) if overlapping else set()
        if overlapping and _scripts(box.text) <= covered:
            continue
        kept.append(box)
    return kept


def merge_lines(boxes: list[LayoutBox]) -> list[list[LayoutBox]]:
    """Group boxes into lines: same row (vertical overlap) and horizontally adjacent.

    Distant boxes on one row stay separate lines so two label columns never
    merge; the key/value search reaches across that gap explicitly instead.
    Rotated side text is a line of its own: its tall box would otherwise
    swallow every row it crosses.
    """
    groups: list[list[LayoutBox]] = []
    for box in sorted(boxes, key=lambda item: (item.center_y, item.extent[0])):
        x0, y0, x1, y1 = box.extent
        best, best_overlap = None, 0.0
        if box.vertical:
            groups.append([box])
            continue
        for group in groups:
            if group[0].vertical:
                continue
            extent = _union(item.extent for item in group)
            reference = min(box.height, median(item.height for item in group))
            overlap = _vertical_overlap(box.extent, extent) / reference
            gap = max(x0 - extent[2], extent[0] - x1)
            side_by_side = _horizontal_overlap(box.extent, extent) <= 0.5 * min(x1 - x0, extent[2] - extent[0])
            if overlap >= ROW_OVERLAP and side_by_side and gap <= JOIN_GAP_FACTOR * reference and overlap > best_overlap:
                best, best_overlap = group, overlap
        if best is None:
            groups.append([box])
        else:
            best.append(box)
    return [sorted(group, key=lambda item: item.extent[0]) for group in groups]


class Layout:
    def __init__(self, lines: list[LayoutLine], geometric: bool) -> None:
        self.lines = lines
        self.geometric = geometric

    @classmethod
    def from_text(cls, text: str) -> "Layout":
        texts = [clean_text(line) for line in text.splitlines()]
        return cls([LayoutLine(index, (), line) for index, line in enumerate(item for item in texts if item)], False)

    @classmethod
    def build(cls, text: str, detections: list[Any] | None = None) -> "Layout":
        boxes = [box for box in (_as_box(item) for item in detections or []) if box is not None]
        boxes = [box for box in boxes if box.bbox]
        if not boxes:
            return cls.from_text(text)
        groups = merge_lines(upright(suppress_duplicates(boxes)))
        extents = [_union(box.extent for box in group) for group in groups]
        order = sorted(range(len(groups)), key=lambda i: (extents[i][1], extents[i][0]))
        lines = [LayoutLine(position, tuple(groups[i]), clean_text(" ".join(box.text for box in groups[i])), extents[i]) for position, i in enumerate(order)]
        return cls(lines, True)

    @property
    def page_height(self) -> float:
        return max((line.extent[3] for line in self.lines if line.extent), default=float(len(self.lines) or 1))

    @property
    def median_height(self) -> float:
        heights = [line.box_height for line in self.lines if line.extent]
        return median(heights) if heights else 1.0

    def right_of(self, line: LayoutLine) -> LayoutLine | None:
        """Nearest line on the same row to the right."""
        if not self.geometric or line.extent is None:
            return None
        candidates = []
        for other in self.lines:
            if other is line or other.extent is None:
                continue
            reference = min(line.box_height, other.box_height)
            gap = other.extent[0] - line.extent[2]
            if _vertical_overlap(line.extent, other.extent) / reference >= ROW_OVERLAP and -0.5 * reference <= gap <= RIGHT_GAP_FACTOR * line.box_height:
                candidates.append((gap, other))
        return min(candidates, key=lambda item: item[0])[1] if candidates else None

    def below(self, line: LayoutLine, gap_factor: float = BELOW_GAP_FACTOR) -> list[LayoutLine]:
        """Lines directly below whose horizontal span overlaps this line, nearest first."""
        if not self.geometric or line.extent is None:
            return [self.lines[line.index + 1]] if line.index + 1 < len(self.lines) else []
        candidates = []
        for other in self.lines:
            if other is line or other.extent is None:
                continue
            gap = other.extent[1] - line.extent[3]
            if other.center_y > line.center_y + 0.5 * line.box_height and _horizontal_overlap(line.extent, other.extent) > 0 and gap <= gap_factor * line.box_height:
                candidates.append(other)
        return sorted(candidates, key=lambda other: (other.extent[1], abs(other.extent[0] - line.extent[0])))

    def next_in_column(self, line: LayoutLine) -> LayoutLine | None:
        """The following line of the same text block, skipping OCR specks."""
        for candidate in self.below(line):
            if is_substantial(candidate.text):
                return candidate
        return None


def is_substantial(text: str) -> bool:
    """At least two letters or digits: a lone "b" or "|" is a speck, not content."""
    return len(re.findall(r"[0-9A-Za-zऀ-ॿ]", text)) >= 2
