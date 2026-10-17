"""Chronological audit trail built from persisted rows; only events with no other record are stored."""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.models import ComplianceResult, ComplianceRule, FieldCorrection, Inspection, InspectionEvent, InspectionImage, OcrResult
from backend.app.schemas.inspection import AuditEvent
from backend.app.services.compliance.registry import results_ruleset
from backend.app.services.compliance.rule_engine import FAIL, NOT_APPLICABLE, PASS, REVIEW, verdict
from backend.app.services.workflow import user_names

# Breaks ties between events recorded in the same instant, in lifecycle order.
_RANK = {"CREATED": 0, "PHOTO_UPLOADED": 1, "OCR_COMPLETED": 2, "COMPLIANCE_EVALUATED": 3}


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def record_event(db: Session, inspection_id: UUID, event_type: str, actor_id: UUID | None, details: dict | None = None) -> None:
    db.add(InspectionEvent(inspection_id=inspection_id, event_type=event_type, actor_id=actor_id, details=details or {}))
    db.commit()


def build_audit(db: Session, inspection: Inspection) -> list[AuditEvent]:
    images = db.scalars(select(InspectionImage).where(InspectionImage.inspection_id == inspection.id)).all()
    ocrs = db.scalars(select(OcrResult).where(OcrResult.inspection_id == inspection.id)).all()
    results = db.scalars(select(ComplianceResult).where(ComplianceResult.inspection_id == inspection.id).join(ComplianceRule)).all()
    corrections = db.scalars(select(FieldCorrection).where(FieldCorrection.inspection_id == inspection.id)).all()
    stored = db.scalars(select(InspectionEvent).where(InspectionEvent.inspection_id == inspection.id)).all()
    names = user_names(db, [inspection.user_id] + [row.corrected_by for row in corrections] + [row.actor_id for row in stored])

    events: list[tuple[datetime, int, AuditEvent]] = []

    def add(kind: str, at: datetime, actor: str | None, details: dict, order: int | None = None) -> None:
        at = _aware(at)
        events.append((at, _RANK.get(kind, 4) if order is None else order, AuditEvent(type=kind, at=at, actor=actor, details=details)))

    add("CREATED", inspection.created_at, names.get(inspection.user_id), {"inspection_number": inspection.inspection_number})
    for image in images:
        add("PHOTO_UPLOADED", image.created_at, names.get(inspection.user_id), {"filename": image.original_filename, "checksum": image.checksum, "byte_size": image.byte_size, "width": image.width, "height": image.height})
    for ocr in ocrs:
        add("OCR_COMPLETED", ocr.created_at, None, {"engine": ocr.engine, "languages": ocr.languages, "duration_ms": ocr.duration_ms, "lines": len(ocr.detections or [])})
    if results:
        statuses = [row.result for row in results]
        _, version, label = results_ruleset(list(results))
        at = inspection.completed_at or min(row.created_at for row in results)
        add("COMPLIANCE_EVALUATED", at, None, {"ruleset": label, "ruleset_version": version, "verdict": verdict(statuses), "passed": statuses.count(PASS), "failed": statuses.count(FAIL), "review": statuses.count(REVIEW), "not_applicable": statuses.count(NOT_APPLICABLE)})
    for row in corrections:
        add("CORRECTION", row.created_at, names.get(row.corrected_by), {"field": row.field_name, "original": row.original_value, "corrected": row.corrected_value, "reason": row.reason})
    for row in stored:
        add(row.event_type, row.created_at, names.get(row.actor_id), dict(row.details or {}))
    events.sort(key=lambda item: (item[0], item[1]))
    return [event for _, _, event in events]
