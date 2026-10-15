"""Explainable repeat-pattern detection over a user's completed inspections."""
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.models import ComplianceResult, ComplianceRule, ExtractedField, Inspection
from backend.app.schemas.inspection import PatternResponse
from backend.app.services.compliance.registry import rule_title
from backend.app.services.compliance.rule_engine import FAIL, ISSUE_STATUSES
from backend.app.services.risk import photo_keys

SEVERITY_RANK = {"HIGH": 3, "WARNING": 2, "MEDIUM": 2, "INFO": 1, "LOW": 1}


@dataclass
class _Issue:
    key: str; kind: str; title: str; description: str; severity: str
    inspection_id: UUID; seen_at: datetime; field: str | None = None; rule_code: str | None = None


class PatternService:
    """Groups stable rule codes (and absent extraction fields) without loading OCR blobs."""
    def __init__(self, db: Session, minimum_occurrences: int = 2):
        self.db = db
        self.minimum_occurrences = minimum_occurrences

    def for_user(self, user_id: UUID, pattern_type: str | None = None, severity: str | None = None, search: str | None = None) -> list[PatternResponse]:
        completed = Inspection.status == "COMPLETED"
        issues: list[_Issue] = []
        rows = self.db.execute(
            select(Inspection.id, Inspection.completed_at, Inspection.created_at, ComplianceResult, ComplianceRule)
            .join(ComplianceResult, ComplianceResult.inspection_id == Inspection.id)
            .join(ComplianceRule, ComplianceRule.id == ComplianceResult.rule_id)
            .where(Inspection.user_id == user_id, completed, ComplianceResult.result.in_(ISSUE_STATUSES))
        ).all()
        for inspection_id, completed_at, created_at, result, rule in rows:
            # NOT_APPLICABLE results are never issues; only FAIL / REVIEW form patterns.
            kind = "COMPLIANCE_VIOLATION" if result.result == FAIL else "COMPLIANCE_WARNING"
            issues.append(_Issue(rule.rule_code, kind, rule_title(rule), result.explanation, rule.severity, inspection_id, completed_at or created_at, result.field_name, rule.rule_code))
        field_rows = self.db.execute(
            select(Inspection.id, Inspection.completed_at, Inspection.created_at, ExtractedField)
            .join(ExtractedField, ExtractedField.inspection_id == Inspection.id)
            .where(Inspection.user_id == user_id, completed, ExtractedField.value.is_(None))
        ).all()
        for inspection_id, completed_at, created_at, field in field_rows:
            key = f"MISSING_FIELD:{field.field_name}"
            title = f"Missing {field.field_name.replace('_', ' ')}"
            issues.append(_Issue(key, "MISSING_FIELD", title, f"{field.field_name.replace('_', ' ').capitalize()} was repeatedly not extracted.", "WARNING", inspection_id, completed_at or created_at, field.field_name))
        grouped: dict[tuple[str, str], list[_Issue]] = defaultdict(list)
        for issue in issues: grouped[(issue.kind, issue.key)].append(issue)
        keys = photo_keys(self.db, list({issue.inspection_id for issue in issues}))
        patterns = []
        for (_, _), entries in grouped.items():
            # One persisted result per rule/inspection; de-duplicate field reruns defensively.
            by_inspection = {entry.inspection_id: entry for entry in entries}
            entries = list(by_inspection.values())
            # The same photo uploaded for several inspections is one occurrence.
            occurrences = len({keys[entry.inspection_id] for entry in entries})
            if occurrences < self.minimum_occurrences: continue
            first, last = min(entries, key=lambda x: x.seen_at), max(entries, key=lambda x: x.seen_at)
            strongest = max(entries, key=lambda x: SEVERITY_RANK.get(x.severity, 0)).severity
            sample = entries[0]
            patterns.append(PatternResponse(pattern_key=sample.key, pattern_type=sample.kind, title=sample.title, description=sample.description, occurrence_count=occurrences, first_seen=first.seen_at, last_seen=last.seen_at, severity=strongest, inspection_ids=sorted(by_inspection), affected_field=sample.field, rule_code=sample.rule_code))
        if pattern_type: patterns = [p for p in patterns if p.pattern_type == pattern_type]
        if severity: patterns = [p for p in patterns if p.severity == severity]
        if search and (term := search.strip().lower()): patterns = [p for p in patterns if term in p.title.lower() or term in p.description.lower() or term in p.pattern_key.lower()]
        return sorted(patterns, key=lambda p: (-p.occurrence_count, -SEVERITY_RANK.get(p.severity, 0), -p.last_seen.timestamp(), p.pattern_key))
