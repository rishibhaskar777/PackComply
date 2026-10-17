"""Inspector corrections: validation, re-application on fresh extraction, and the audit trail.

A correction overrides one extracted field. Values are validated and normalised
with the same parsers the extractor and rule engine use, so a corrected field
reaches the rules exactly as an OCR-read one would. Corrected fields carry
confidence 1.0 and the matched pattern ``inspector_correction`` (surfaced to
clients as source ``INSPECTOR``). Corrections are re-applied on top of every
fresh extraction, which is also how an undo restores the OCR value.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Callable
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.models import ExtractedField, FieldCorrection, Inspection, OcrResult
from backend.app.services.compliance.rule_engine import _CARE_NORMALIZE, _CARE_VALID, ComplianceEngine
from backend.app.services.extraction import values as value_first
from backend.app.services.extraction.extraction_service import FIELD_NAMES

INSPECTOR_PATTERN = "inspector_correction"
SOURCE_OCR, SOURCE_INSPECTOR = "OCR", "INSPECTOR"
REASONS = ("Verified on physical pack", "OCR misread", "Other")
# "brand_name" is what an inspector sees; it is stored on the brand_owner field.
FIELD_ALIASES = {"brand_name": "brand_owner"}
# Evidence and declaration-state fields are produced by the extractor, not read from the label.
NOT_CORRECTABLE = frozenset({"declaration_status", "ocr_coverage", "mrp_declaration", "consumer_care_declaration", "import_declaration", "product_category"})
CORRECTABLE_FIELDS = tuple(name for name in FIELD_NAMES if name not in NOT_CORRECTABLE)
DATE_FIELDS = frozenset({"manufacturing_date", "packing_date", "best_before", "use_by", "expiry_date"})
_TEXT_MAX = 300


class CorrectionError(ValueError):
    """The submitted value is not valid for the field; the message is shown to the inspector."""


def _clean(raw: str) -> str:
    return re.sub(r"\s+", " ", raw.replace(" ", " ")).strip()


def _mrp(raw: str) -> tuple[str, str | None]:
    match = re.fullmatch(r"(?i)(?:mrp\s*[:.]?\s*)?(?:₹|rs\.?|inr)?\s*([0-9][0-9,]*(?:\.[0-9]{1,2})?)\s*(?:/-|/)?", raw)
    if not match:
        raise CorrectionError("Enter the MRP as an amount in rupees, for example 10, 10.00 or ₹10.00.")
    try:
        amount = Decimal(match.group(1).replace(",", ""))
    except InvalidOperation as error:
        raise CorrectionError("Enter the MRP as an amount in rupees, for example 10 or ₹10.00.") from error
    if amount <= 0:
        raise CorrectionError("The MRP must be greater than zero.")
    value = f"₹{amount:.2f}"
    return value, ComplianceEngine.normalize_mrp(value)


def _quantity(raw: str) -> tuple[str, str | None]:
    match = value_first._QUANTITY.fullmatch(raw)
    parsed = value_first._quantity_value(match.group(1), match.group(2)) if match else None
    if not parsed:
        raise CorrectionError("Enter the net quantity with a standard unit, for example 70 g, 500 ml or 1 kg.")
    return parsed


def _date(raw: str) -> tuple[str, str | None]:
    normalized = value_first.normalize_date(raw)
    if normalized is None:
        raise CorrectionError("Enter a valid date such as 08/2026, 15/08/2026 or AUG 2026 (month 1-12).")
    return raw, normalized


def _unit_sale_price(raw: str) -> tuple[str, str | None]:
    match = value_first._USP.fullmatch(raw)
    parsed = value_first._usp_value(match) if match else None
    if not parsed:
        raise CorrectionError("Enter the unit sale price as an amount per unit, for example ₹14.29 per 100 g.")
    return parsed


def _care(field: str) -> Callable[[str], tuple[str, str | None]]:
    kind = field.rsplit("_", 1)[1]

    def parse(raw: str) -> tuple[str, str | None]:
        if not _CARE_VALID[field](_CARE_NORMALIZE[field](raw)):
            raise CorrectionError(f"Enter a valid consumer-care {kind}.")
        return raw, None
    return parse


def _fssai(raw: str) -> tuple[str, str | None]:
    digits = re.sub(r"[\s-]", "", raw)
    if not re.fullmatch(r"\d{14}", digits):
        raise CorrectionError("An FSSAI licence number has exactly 14 digits.")
    return digits, None


def _tax_inclusive(raw: str) -> tuple[str, str | None]:
    answer = raw.casefold()
    if answer in ("true", "yes", "y", "inclusive"):
        return "true", "true"
    if answer in ("false", "no", "n"):
        return "false", "false"
    raise CorrectionError("Answer yes or no.")


def _text(raw: str) -> tuple[str, str | None]:
    if len(raw) < 2:
        raise CorrectionError("Enter at least two characters.")
    if len(raw) > _TEXT_MAX or re.search(r"[\x00-\x1f\x7f]", raw):
        raise CorrectionError(f"Enter plain text of at most {_TEXT_MAX} characters.")
    return raw, None


def normalise_value(field_name: str, raw: str | None) -> tuple[str, str | None]:
    """(stored value, normalized_value) for a correction, or CorrectionError."""
    if field_name not in CORRECTABLE_FIELDS:
        raise CorrectionError(f"'{field_name}' is not a field that can be corrected.")
    text = _clean(raw or "")
    if not text:
        raise CorrectionError("Enter a value.")
    if field_name == "mrp":
        return _mrp(text)
    if field_name == "net_quantity":
        return _quantity(text)
    if field_name in DATE_FIELDS:
        return _date(text)
    if field_name == "unit_sale_price":
        return _unit_sale_price(text)
    if field_name in _CARE_VALID:
        return _care(field_name)(text)
    if field_name == "fssai_license":
        return _fssai(text)
    if field_name == "tax_inclusive":
        return _tax_inclusive(text)
    return _text(text)


def active_corrections(db: Session, inspection_id: UUID) -> dict[str, FieldCorrection]:
    """The latest correction per field."""
    rows = db.scalars(select(FieldCorrection).where(FieldCorrection.inspection_id == inspection_id).order_by(FieldCorrection.created_at, FieldCorrection.id)).all()
    return {row.field_name: row for row in rows}


def apply_corrections(db: Session, inspection_id: UUID, ocr_result: OcrResult | None, extracted: list[ExtractedField]) -> list[ExtractedField]:
    """Overlay active corrections on freshly extracted rows (creating rows for fields the OCR missed)."""
    corrections = active_corrections(db, inspection_id)
    if not corrections:
        return extracted
    by_name = {field.field_name: field for field in extracted}
    for name, correction in corrections.items():
        try:
            value, normalized = normalise_value(name, correction.corrected_value)
        except CorrectionError:
            value, normalized = correction.corrected_value, None
        row = by_name.get(name)
        if row is None:
            row = ExtractedField(inspection_id=inspection_id, ocr_result_id=ocr_result.id if ocr_result else None, field_name=name)
            db.add(row)
            by_name[name] = row
        row.value, row.normalized_value = value, normalized
        row.confidence, row.review_status, row.matched_pattern = 1, "EXTRACTED", INSPECTOR_PATTERN
        row.alternatives = None  # the inspector settled any disagreement between photos
        if row.source_text is None:
            row.source_bboxes = None
    db.commit()
    return list(by_name.values())


def sync_label_identity(db: Session, inspection: Inspection, extracted: list[ExtractedField]) -> None:
    """Store product / brand / maker on the inspection so history and search need no extraction join."""
    def shown(field: ExtractedField) -> str:
        # OCR-read names show their tidied form (the raw value stays on the field); an inspector's text is used as typed.
        tidy = field.normalized_value if field.field_name == "product_name" and field.matched_pattern != INSPECTOR_PATTERN else None
        return _clean(tidy or field.value)
    values = {field.field_name: shown(field) for field in extracted if field.value and field.field_name in ("product_name", "brand_owner", "manufacturer")}
    inspection.product_name = values.get("product_name") or None
    inspection.brand_name = values.get("brand_owner") or None
    inspection.maker_name = values.get("manufacturer") or None
    db.commit()


def display_name(inspection: Inspection) -> str:
    return inspection.product_name or "Untitled label"


def reset_review(inspection: Inspection) -> None:
    inspection.review_status = "UNREVIEWED"
    inspection.reviewed_by = inspection.reviewed_at = None


def mark_verified(inspection: Inspection, user_id: UUID, remarks: str | None) -> None:
    inspection.review_status, inspection.reviewed_by, inspection.reviewed_at = "VERIFIED", user_id, datetime.now(timezone.utc)
    inspection.remarks = remarks or None
