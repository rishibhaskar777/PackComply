"""Persist extraction results together with the OCR evidence they came from."""
from __future__ import annotations

import re
from typing import Any
from uuid import UUID

from backend.app.models import ExtractedField, OcrResult
from backend.app.services.compliance.rule_engine import FieldEvidence
from backend.app.services.extraction.extraction_service import SUPPLEMENTARY_FIELD_NAMES, ExtractedValue

# Single characters ("g", "1") occur in nearly every line and would mark
# unrelated regions of the photo, so they never count as a match.
_MIN_DETECTION_LENGTH = 2


def _comparable(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().casefold()


def source_bboxes(source_text: str | None, detections: list[dict[str, Any]] | None) -> list[list[list[float]]] | None:
    """Boxes of every OCR detection whose text occurs within the source line."""
    if not source_text or not detections:
        return None
    line = _comparable(source_text)
    boxes = [
        detection["bbox"] for detection in detections
        if detection.get("bbox") and len(text := _comparable(str(detection.get("text") or ""))) >= _MIN_DETECTION_LENGTH and text in line
    ]
    return boxes or None


def build_extracted_fields(inspection_id: UUID, ocr_result: OcrResult, values: dict[str, ExtractedValue]) -> list[ExtractedField]:
    return [
        ExtractedField(
            inspection_id=inspection_id,
            ocr_result_id=ocr_result.id,
            image_id=ocr_result.image_id,
            field_name=name,
            value=value.value,
            confidence=value.confidence,
            review_status="REVIEW" if value.needs_review else "EXTRACTED",
            source_text=value.source_text,
            matched_pattern=value.matched_pattern[:100] if value.matched_pattern else None,
            normalized_value=value.normalized_value,
            # Layout-aware extraction knows its exact boxes; text-only results are matched back.
            source_bboxes=(value.source_bboxes or source_bboxes(value.source_text, ocr_result.detections)) if value.value else None,
        )
        for name, value in values.items()
        if value.value is not None or name not in SUPPLEMENTARY_FIELD_NAMES
    ]


_DATE_FIELDS = frozenset({"manufacturing_date", "packing_date", "best_before", "use_by", "expiry_date"})


def _compliance_value(field: ExtractedField) -> str | None:
    """Dates reach the rule engine in the numeric form it compares ("MAR 2026" -> "03/2026")."""
    normalized = field.normalized_value or ""
    if field.field_name in _DATE_FIELDS and (match := re.fullmatch(r"(\d{4})-(\d{2})(?:-(\d{2}))?", normalized)):
        year, month, day = match.groups()
        return f"{day}/{month}/{year}" if day else f"{month}/{year}"
    return field.value


def field_evidence(field: ExtractedField) -> FieldEvidence:
    """Compliance input; evidence is the OCR line, falling back to the value for legacy rows."""
    return FieldEvidence(
        value=_compliance_value(field),
        confidence=float(field.confidence) if field.confidence is not None else None,
        needs_review=field.review_status == "REVIEW",
        evidence=field.source_text if field.source_text is not None else field.value,
    )
