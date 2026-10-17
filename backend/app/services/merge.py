"""Merge per-photo extraction into one set of label fields.

Each photo is extracted on its own. For every field the best candidate wins:
highest confidence, and on a tie the photo whose panel is the natural home of
that field (names on the FRONT, dates and batch on the DATE_STAMP, everything
else on the BACK). Every merged field keeps the photo it came from and the
boxes in that photo's pixel space. When photos disagree on a field a rule
depends on, the losing values are kept as ``alternatives`` so compliance can
raise a REVIEW note instead of silently picking one.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from backend.app.models import ExtractedField, OcrResult
from backend.app.services.extraction.evidence import source_bboxes
from backend.app.services.extraction.extraction_service import DECLARED_WITH_VALUE, KEY_FOUND_VALUE_UNREAD, NOT_DETECTED, SUPPLEMENTARY_FIELD_NAMES, ExtractedValue
from backend.app.services.names import normalise_display_name

PANELS = ("BACK", "FRONT", "DATE_STAMP", "OTHER")
DEFAULT_PANEL = "BACK"

NAME_FIELDS = frozenset({"product_name", "brand_owner"})
# Only the product name is recased for display; company names are legal entities and stay as printed.
DISPLAY_NORMALISED = frozenset({"product_name"})
DATE_FIELDS = frozenset({"manufacturing_date", "packing_date", "best_before", "use_by", "expiry_date", "batch_number"})
_PREFERENCE = {
    "FRONT": ("FRONT", "BACK", "OTHER", "DATE_STAMP"),
    "DATE_STAMP": ("DATE_STAMP", "BACK", "OTHER", "FRONT"),
    "BACK": ("BACK", "OTHER", "FRONT", "DATE_STAMP"),
}
# Fields a rule judges: two different readings are surfaced for review instead of one being dropped.
CONFLICT_FIELDS = frozenset({
    "mrp", "net_quantity", "manufacturing_date", "packing_date", "best_before", "use_by", "expiry_date", "unit_sale_price",
    "fssai_license", "batch_number", "country_of_origin", "consumer_care_phone", "consumer_care_email",
})
# Computed from the whole photo, merged separately rather than picked.
_DERIVED = frozenset({"declaration_status", "ocr_coverage"})
# "absent" only says that photo has no such heading, so any positive reading elsewhere beats it.
_PLACEHOLDER_DECLARATIONS = {"mrp_declaration": "absent", "consumer_care_declaration": "absent"}
_STATE_RANK = {DECLARED_WITH_VALUE: 2, KEY_FOUND_VALUE_UNREAD: 1, NOT_DETECTED: 0}
_DECLARATION_FIELDS = (
    ("net_quantity", ("net_quantity",)), ("mrp", ("mrp",)), ("manufacturing_date", ("manufacturing_date", "packing_date")),
    ("unit_sale_price", ("unit_sale_price",)), ("consumer_care", ("consumer_care_phone", "consumer_care_email", "consumer_care_website")),
)


def panel_order(field_name: str) -> tuple[str, ...]:
    return _PREFERENCE["FRONT" if field_name in NAME_FIELDS else "DATE_STAMP" if field_name in DATE_FIELDS else "BACK"]


@dataclass
class PhotoExtraction:
    """One photo's extraction, with the OCR result it came from."""
    image_id: UUID | None
    panel: str
    ocr_result: OcrResult
    values: dict[str, ExtractedValue]


@dataclass
class MergedField:
    value: ExtractedValue
    image_id: UUID | None = None
    panel: str | None = None
    ocr_result: OcrResult | None = None
    alternatives: list[dict[str, Any]] = field(default_factory=list)


def _comparable(value: ExtractedValue) -> str:
    return re.sub(r"[\s,]+", "", (value.normalized_value or value.value or "")).casefold()


def _rank(candidate: tuple[PhotoExtraction, ExtractedValue], order: tuple[str, ...]) -> tuple[float, int]:
    photo, value = candidate
    # Confidence to two decimals: closer than that is a tie, which the panel decides.
    return (round(value.confidence or 0.0, 2), -(order.index(photo.panel) if photo.panel in order else len(order)))


def _pick(name: str, candidates: list[tuple[PhotoExtraction, ExtractedValue]]) -> MergedField:
    order = panel_order(name)
    placeholder = _PLACEHOLDER_DECLARATIONS.get(name)
    positive = [c for c in candidates if c[1].value != placeholder] if placeholder else candidates
    photo, value = max(positive or candidates, key=lambda candidate: _rank(candidate, order))
    alternatives = []
    if name in CONFLICT_FIELDS:
        chosen = _comparable(value)
        for other, other_value in candidates:
            if other is not photo and _comparable(other_value) != chosen:
                alternatives.append({"image_id": str(other.image_id) if other.image_id else None, "panel": other.panel, "value": other_value.value, "confidence": round(other_value.confidence or 0.0, 4)})
    return MergedField(value, photo.image_id, photo.panel, photo.ocr_result, alternatives)


def _json_values(photos: list[PhotoExtraction], name: str) -> list[dict]:
    found = []
    for photo in photos:
        raw = photo.values.get(name)
        try:
            data = json.loads(raw.value) if raw and raw.value else None
        except ValueError:
            data = None
        if isinstance(data, dict):
            found.append(data)
    return found


def _merge_declaration_status(photos: list[PhotoExtraction], merged: dict[str, MergedField]) -> ExtractedValue:
    parsed = _json_values(photos, "declaration_status")
    if not parsed:
        return ExtractedValue()
    groups: dict[str, Any] = {}
    for data in parsed:
        for group, entry in data.items():
            best = groups.get(group)
            if best is None or _STATE_RANK.get((entry or {}).get("state"), 0) > _STATE_RANK.get(best.get("state"), 0):
                groups[group] = entry
    # A merged field with a value always means that declaration was read with its value.
    for group, names in _DECLARATION_FIELDS:
        winner = next((merged[n] for n in names if n in merged and merged[n].value.value), None)
        if winner is not None and (groups.get(group) or {}).get("state") != DECLARED_WITH_VALUE:
            groups[group] = {"state": DECLARED_WITH_VALUE, "evidence": winner.value.source_text}
    serialized = json.dumps(groups, ensure_ascii=False, sort_keys=True)
    return ExtractedValue(serialized, 0.9, False, None, "declaration_status:computed", serialized)


def _merge_coverage(photos: list[PhotoExtraction]) -> ExtractedValue:
    """Several photos together read the pack: lines add up, confidence is the line-weighted mean."""
    parts = _json_values(photos, "ocr_coverage")
    if not parts:
        return ExtractedValue()
    if len(parts) == 1:
        coverage = parts[0]
    else:
        def weighted(key: str) -> float | None:
            known = [(part[key], part.get("text_lines") or 0) for part in parts if part.get(key) is not None]
            weight = sum(w for _, w in known)
            return round(sum(v * w for v, w in known) / weight, 3) if known and weight else (known[0][0] if known else None)
        coverage = {"text_lines": sum(part.get("text_lines") or 0 for part in parts), "mean_confidence": weighted("mean_confidence"), "low_confidence_line_share": weighted("low_confidence_line_share")}
    serialized = json.dumps(coverage, sort_keys=True)
    return ExtractedValue(serialized, 0.9, False, None, "ocr_coverage:computed", serialized)


def merge_photos(photos: list[PhotoExtraction]) -> dict[str, MergedField]:
    """Best candidate per field across photos. A single photo merges to exactly its own extraction."""
    if not photos:
        return {}
    names = list(dict.fromkeys(name for photo in photos for name in photo.values))
    merged: dict[str, MergedField] = {}
    for name in names:
        if name in _DERIVED:
            continue
        candidates = [(photo, photo.values[name]) for photo in photos if photo.values.get(name) is not None and photo.values[name].value is not None]
        merged[name] = _pick(name, candidates) if candidates else MergedField(photos[0].values.get(name) or ExtractedValue())
    if any("declaration_status" in photo.values for photo in photos):
        merged["declaration_status"] = MergedField(_merge_declaration_status(photos, merged))
    if any("ocr_coverage" in photo.values for photo in photos):
        merged["ocr_coverage"] = MergedField(_merge_coverage(photos))
    return merged


def build_merged_fields(inspection_id: UUID, merged: dict[str, MergedField]) -> list[ExtractedField]:
    rows = []
    for name, item in merged.items():
        value = item.value
        if value.value is None and name in SUPPLEMENTARY_FIELD_NAMES:
            continue
        # The raw OCR value stays in ``value``; names also get their tidied display form.
        normalized = normalise_display_name(value.value) if name in DISPLAY_NORMALISED and value.value else value.normalized_value
        ocr = item.ocr_result
        rows.append(ExtractedField(
            inspection_id=inspection_id,
            ocr_result_id=ocr.id if ocr is not None else None,
            image_id=item.image_id,
            field_name=name,
            value=value.value,
            confidence=value.confidence,
            review_status="REVIEW" if value.needs_review else "EXTRACTED",
            source_text=value.source_text,
            matched_pattern=value.matched_pattern[:100] if value.matched_pattern else None,
            normalized_value=normalized,
            source_bboxes=(value.source_bboxes or (source_bboxes(value.source_text, ocr.detections) if ocr is not None else None)) if value.value else None,
            alternatives=item.alternatives or None,
        ))
    return rows
