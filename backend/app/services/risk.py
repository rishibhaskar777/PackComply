"""Deterministic, evidence-only risk scoring for completed inspections."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.app.models import ComplianceResult, ComplianceRule, ExtractedField, Inspection, InspectionImage, Product
from backend.app.services.compliance.rule_engine import FAIL, ISSUE_STATUSES, REVIEW

# Central policy constants.  These are prioritisation weights, not legal rules.
RISK_LEVELS = ((75, "CRITICAL"), (50, "HIGH"), (25, "MEDIUM"), (0, "LOW"))
FAILURE_POINTS = {"HIGH": 35, "WARNING": 25, "MEDIUM": 25, "INFO": 15, "LOW": 10}
REVIEW_POINTS = {"HIGH": 20, "WARNING": 12, "MEDIUM": 12, "INFO": 6, "LOW": 4}
MISSING_FIELD_POINTS = 6
MISSING_FIELD_CAP = 18
REPEATED_RULE_POINTS = 12
REPEATED_FIELD_POINTS = 8
MULTIPLE_FINDINGS_POINTS = 8
MULTIPLE_FINDINGS_MINIMUM = 3


@dataclass(frozen=True)
class RiskReason:
    type: str
    points: int
    description: str
    rule_code: str | None = None
    severity: str | None = None
    field: str | None = None
    occurrences: int | None = None


@dataclass(frozen=True)
class RiskAssessment:
    risk_score: int
    risk_level: str
    reasons: list[RiskReason]


def risk_level(score: int) -> str:
    return next(level for threshold, level in RISK_LEVELS if score >= threshold)


def required_fields(rule: ComplianceRule) -> set[str]:
    """Fields whose absence the rule treats as a missing declaration; rules stored before rulesets use target_field."""
    configuration = rule.configuration or {}
    return set(configuration["required_fields"]) if "required_fields" in configuration else {rule.target_field}


def risk_missing_fields(results: list[tuple[ComplianceResult, ComplianceRule]], missing: set[str]) -> set[str]:
    """Missing fields required by a rule that FAILed.

    A REVIEW finding's points already cover the uncertainty, so its missing
    fields are not counted again.  Optional or informational fields (website,
    batch number, best before, a packer beside a declared manufacturer) and
    fields of NOT_APPLICABLE rules never add points on their own.
    """
    return missing & set().union(*(required_fields(rule) for result, rule in results if result.result == FAIL))


def photo_keys(db: Session, inspection_ids: list[UUID]) -> dict[UUID, str]:
    """One key per distinct label: the checksum of the active BACK photo, else of the earliest active photo.

    The same label photo uploaded again for another inspection is one
    occurrence for repeat patterns and risk, not a new repetition. Active means
    the newest photo per panel (every OTHER photo counts); replaced photos are ignored.
    """
    keys = {inspection_id: f"inspection:{inspection_id}" for inspection_id in inspection_ids}
    if not inspection_ids:
        return keys
    images = db.execute(
        select(InspectionImage.inspection_id, InspectionImage.checksum, InspectionImage.created_at, InspectionImage.upload_sequence, InspectionImage.id, InspectionImage.panel)
        .where(InspectionImage.inspection_id.in_(inspection_ids))
    ).all()
    by_inspection: dict[UUID, list[tuple]] = {}
    for inspection_id, checksum, created_at, sequence, image_id, panel in images:
        by_inspection.setdefault(inspection_id, []).append(((sequence, created_at, str(image_id)), checksum, panel))
    for inspection_id, rows in by_inspection.items():
        rows.sort(key=lambda row: row[0])
        newest: dict[str, tuple] = {}
        active = []
        for row in rows:
            if row[2] == "OTHER":
                active.append(row)
            else:
                newest[row[2]] = row
        active = sorted([*newest.values(), *active], key=lambda row: row[0])
        chosen = newest.get("BACK") or (active[0] if active else None)
        if chosen and chosen[1]:
            keys[inspection_id] = f"image:{chosen[1]}"
    return keys


class RiskService:
    """Scores only persisted compliance/extraction evidence; it never reads OCR."""
    def __init__(self, db: Session, minimum_occurrences: int = 2):
        self.db, self.minimum_occurrences = db, minimum_occurrences

    def _evidence(self, inspections: list[Inspection]):
        ids = [inspection.id for inspection in inspections]
        if not ids:
            return {}, {}, {}
        results = self.db.execute(
            select(ComplianceResult.inspection_id, ComplianceResult, ComplianceRule)
            .join(ComplianceRule, ComplianceRule.id == ComplianceResult.rule_id)
            .where(ComplianceResult.inspection_id.in_(ids))
        ).all()
        fields = self.db.execute(
            select(ExtractedField.inspection_id, ExtractedField)
            .where(ExtractedField.inspection_id.in_(ids), ExtractedField.value.is_(None))
        ).all()
        by_inspection: dict[UUID, list[tuple[ComplianceResult, ComplianceRule]]] = defaultdict(list)
        for inspection_id, result, rule in results:
            by_inspection[inspection_id].append((result, rule))
        missing: dict[UUID, set[str]] = defaultdict(set)
        for inspection_id, field in fields:
            missing[inspection_id].add(field.field_name)
        # Repeats count violations only (FAIL), once per distinct photo.
        keys = photo_keys(self.db, ids)
        rule_photos: dict[str, set[str]] = defaultdict(set)
        field_photos: dict[str, set[str]] = defaultdict(set)
        for inspection_id in ids:
            inspection_results = by_inspection.get(inspection_id, [])
            for result, rule in inspection_results:
                if result.result == FAIL:
                    rule_photos[rule.rule_code].add(keys[inspection_id])
            for field in risk_missing_fields(inspection_results, missing.get(inspection_id, set())):
                field_photos[field].add(keys[inspection_id])
        counts = {"rules": {code: len(photos) for code, photos in rule_photos.items()}, "fields": {field: len(photos) for field, photos in field_photos.items()}}
        return by_inspection, missing, counts

    def assess(self, inspection: Inspection, results: list[tuple[ComplianceResult, ComplianceRule]], missing_fields: set[str], counts: dict) -> RiskAssessment:
        reasons: list[RiskReason] = []
        non_pass = []
        # Only fields required by a failing rule count; REVIEW and NOT_APPLICABLE add nothing here.
        missing_fields = risk_missing_fields(results, missing_fields)
        for result, rule in sorted(results, key=lambda item: item[1].rule_code):
            if result.result not in ISSUE_STATUSES:
                continue
            non_pass.append((result, rule))
            points = (FAILURE_POINTS if result.result == FAIL else REVIEW_POINTS).get(rule.severity, REVIEW_POINTS["INFO"])
            kind = "COMPLIANCE_FAILURE" if result.result == FAIL else "COMPLIANCE_WARNING"
            reasons.append(RiskReason(kind, points, result.explanation, rule.rule_code, rule.severity, result.field_name))
        # Missing extraction values are explanatory only; no legal requirement is inferred.
        for field in sorted(missing_fields)[: MISSING_FIELD_CAP // MISSING_FIELD_POINTS]:
            reasons.append(RiskReason("MISSING_FIELD", MISSING_FIELD_POINTS, f"{field.replace('_', ' ').capitalize()} was not extracted and requires review.", field=field))
        # Repeat points are for repeated violations only: a REVIEW ("cannot be confirmed
        # from this photo") repeated across inspections adds nothing.
        seen_rules = set()
        for result, rule in non_pass:
            occurrences = counts["rules"].get(rule.rule_code, 0)
            if result.result == FAIL and rule.rule_code not in seen_rules and occurrences >= self.minimum_occurrences:
                seen_rules.add(rule.rule_code)
                reasons.append(RiskReason("REPEATED_PATTERN", REPEATED_RULE_POINTS, "Same violation has appeared repeatedly in completed inspections (distinct photos).", rule.rule_code, rule.severity, occurrences=occurrences))
        for field in sorted(missing_fields):
            occurrences = counts["fields"].get(field, 0)
            if occurrences >= self.minimum_occurrences:
                reasons.append(RiskReason("REPEATED_PATTERN", REPEATED_FIELD_POINTS, "Missing required field has appeared repeatedly with a violation in completed inspections (distinct photos).", field=field, occurrences=occurrences))
        if len({rule.rule_code for result, rule in non_pass if result.result == FAIL}) >= MULTIPLE_FINDINGS_MINIMUM:
            reasons.append(RiskReason("MULTIPLE_FINDINGS", MULTIPLE_FINDINGS_POINTS, "Multiple distinct violations require combined attention."))
        score = min(100, max(0, sum(reason.points for reason in reasons)))
        return RiskAssessment(score, risk_level(score), reasons)

    def for_user(self, user_id: UUID) -> list[tuple[Inspection, str | None, RiskAssessment, str]]:
        rows = self.db.execute(
            select(Inspection, func.coalesce(Inspection.product_name, Product.name)).outerjoin(Product, Product.id == Inspection.product_id)
            .where(Inspection.user_id == user_id, Inspection.status == "COMPLETED")
        ).all()
        inspections = [inspection for inspection, _ in rows]
        results, missing, counts = self._evidence(inspections)
        output = []
        for inspection, product_name in rows:
            assessment = self.assess(inspection, results.get(inspection.id, []), missing.get(inspection.id, set()), counts)
            statuses = [result.result for result, _ in results.get(inspection.id, [])]
            compliance_status = "FAIL" if FAIL in statuses else "WARNING" if REVIEW in statuses else "PASS"
            output.append((inspection, product_name, assessment, compliance_status))
        return sorted(output, key=lambda row: (-row[2].risk_score, -(3 if row[2].risk_level == "CRITICAL" else 2 if row[2].risk_level == "HIGH" else 1 if row[2].risk_level == "MEDIUM" else 0), -(row[0].completed_at or row[0].created_at).timestamp(), str(row[0].id)))

    def for_inspection(self, inspection: Inspection) -> RiskAssessment:
        # Repeat context is intentionally limited to the inspection owner's completed history.
        items = self.for_user(inspection.user_id) if inspection.user_id else []
        for candidate, _, assessment, _ in items:
            if candidate.id == inspection.id:
                return assessment
        return RiskAssessment(0, "LOW", [])
