"""A manufacturer's products: self-checks grouped by product, latest check first.

A product is every completed self-check of one user that shares a product key. A key is carried forward by "upload corrected
label" (``previous_inspection_id`` / ``product_key``); otherwise it is the normalised product name, so repeated uploads of the
same artwork land on one card. Everything here reads persisted results; nothing is re-evaluated, and it is always scoped to
the calling user's own inspections.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
import re
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.app.models import ComplianceResult, ComplianceRule, ExtractedField, Inspection
from backend.app.schemas.inspection import FixGroup, FixLabel, FixesResponse, ProductCard, ProductCheck, ProductsSummary
from backend.app.services.compliance.registry import rule_legal_reference, rule_title
from backend.app.services.compliance.remediation import remediation_for, rule_chip
from backend.app.services.compliance.rule_engine import FAIL, NOT_APPLICABLE, PASS, REVIEW
from backend.app.services.corrections import display_name
from backend.app.services.workflow import primary_image

READY, FIX, CHECK = "READY", "FIX", "CHECK"
SEVERITY_RANK = {"CRITICAL": 5, "HIGH": 4, "WARNING": 3, "MEDIUM": 3, "INFO": 2, "LOW": 1}


def normalise_product_key(name: str | None) -> str:
    return re.sub(r"[^0-9a-zऀ-ॿ]+", " ", (name or "").casefold()).strip()


def effective_key(inspection: Inspection) -> str:
    return inspection.product_key or normalise_product_key(inspection.product_name) or f"untitled-{inspection.id}"


def readiness(fixes: int, review: int) -> str:
    return FIX if fixes else CHECK if review else READY


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _completed(db: Session, user_id: UUID) -> list[Inspection]:
    rows = db.scalars(select(Inspection).where(Inspection.user_id == user_id, Inspection.status == "COMPLETED")).all()
    return sorted(rows, key=lambda row: (_aware(row.completed_at or row.created_at), str(row.id)))


def _counts(db: Session, ids: list[UUID]) -> dict[UUID, dict[str, int]]:
    counts: dict[UUID, dict[str, int]] = {}
    if ids:
        for inspection_id, result, total in db.execute(select(ComplianceResult.inspection_id, ComplianceResult.result, func.count()).where(ComplianceResult.inspection_id.in_(ids)).group_by(ComplianceResult.inspection_id, ComplianceResult.result)).all():
            counts.setdefault(inspection_id, {PASS: 0, FAIL: 0, REVIEW: 0, NOT_APPLICABLE: 0})[result] = total
    return counts


def product_summary(db: Session, user_id: UUID) -> ProductsSummary:
    counts = _counts(db, [row.id for row in _completed(db, user_id)])
    # Only labels the rules were actually run on are products.
    inspections = [row for row in _completed(db, user_id) if row.id in counts]
    groups: dict[str, list[Inspection]] = defaultdict(list)
    for row in inspections:
        groups[effective_key(row)].append(row)
    latest_ids = [history[-1].id for history in groups.values()]
    quantities = {inspection_id: value for inspection_id, value in db.execute(select(ExtractedField.inspection_id, ExtractedField.value).where(ExtractedField.field_name == "net_quantity", ExtractedField.inspection_id.in_(latest_ids))).all()} if latest_ids else {}
    cards: list[ProductCard] = []
    for key, history in groups.items():
        latest = history[-1]
        c = counts[latest.id]
        checks = [ProductCheck(inspection_id=row.id, checked_at=row.completed_at or row.created_at, fixes=counts[row.id][FAIL], review=counts[row.id][REVIEW], status=readiness(counts[row.id][FAIL], counts[row.id][REVIEW])) for row in history]
        previous = history[-2] if len(history) > 1 else None
        previous_fixes = counts[previous.id][FAIL] if previous else None
        image = primary_image(db, latest.id)
        cards.append(ProductCard(
            product_key=key, inspection_id=latest.id, product_name=display_name(latest), net_quantity=quantities.get(latest.id), checked_at=latest.completed_at or latest.created_at,
            checks=len(history), status=readiness(c[FAIL], c[REVIEW]), fixes=c[FAIL], review=c[REVIEW], passed=c[PASS], rules_total=c[PASS] + c[FAIL] + c[REVIEW],
            has_photo=image is not None, image_id=image.id if image else None, previous_fixes=previous_fixes,
            improved=previous_fixes is not None and c[FAIL] < previous_fixes, history=list(reversed(checks)),
        ))
    cards.sort(key=lambda card: (_aware(card.checked_at), card.product_name), reverse=True)
    ready = sum(card.status == READY for card in cards)
    fix = sum(card.status == FIX for card in cards)
    check = sum(card.status == CHECK for card in cards)
    return ProductsSummary(total=len(cards), ready=ready, fix=fix, check=check, readiness_percent=round(100 * ready / len(cards)) if cards else 0,
                           open_fixes=sum(card.fixes for card in cards), labels_to_fix=fix, products=cards)


def open_fixes(db: Session, user_id: UUID) -> FixesResponse:
    """FAIL (then REVIEW) results of every product's latest self-check, grouped by rule."""
    latest: dict[str, Inspection] = {}
    for row in _completed(db, user_id):
        latest[effective_key(row)] = row
    by_id = {row.id: row for row in latest.values()}
    groups: dict[tuple[str, str], list[tuple[Inspection, ComplianceRule]]] = defaultdict(list)
    if by_id:
        for result in db.scalars(select(ComplianceResult).where(ComplianceResult.inspection_id.in_(list(by_id)), ComplianceResult.result.in_((FAIL, REVIEW))).join(ComplianceRule)).all():
            groups[(result.rule.rule_code, result.result)].append((by_id[result.inspection_id], result.rule))
    out: list[FixGroup] = []
    for (code, status), entries in groups.items():
        rule = entries[0][1]
        entry = remediation_for(code)
        reference = rule_legal_reference(rule)
        names = sorted({(row.id, display_name(row)) for row, _ in entries}, key=lambda pair: (pair[1], str(pair[0])))
        out.append(FixGroup(rule_id=code, title=entry.problem_title if entry else rule_title(rule), rule_name=rule_title(rule), legal_reference=reference, rule_chip=rule_chip(reference), severity=rule.severity, status=status,
                            product_count=len(names), fix_steps=list(entry.fix_steps) if entry else [], example=entry.example if entry else None,
                            products=[FixLabel(inspection_id=inspection_id, product_name=name, status=status) for inspection_id, name in names]))
    out.sort(key=lambda group: (group.status != FAIL, -SEVERITY_RANK.get(group.severity, 0), -group.product_count, group.rule_id))
    return FixesResponse(total_items=sum(group.product_count for group in out if group.status == FAIL), groups=out)
