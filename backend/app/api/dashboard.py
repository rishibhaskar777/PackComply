"""Overview dashboard: a compact summary over the caller's own inspections.

Scoping matches the inspection history endpoint: every user, official or
manufacturer, sees only inspections they own (``require_owner`` would 404 on
anything else).  Nothing here re-evaluates labels; it reads persisted
compliance results, the risk service and the active ruleset.
"""
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from statistics import median
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.app.api.auth import get_current_user
from backend.app.api.inspections import _overall_compliance_status
from backend.app.core.config import get_settings
from backend.app.core.database import get_db
from backend.app.models import ComplianceResult, ComplianceRule, Inspection, InspectionImage, User
from backend.app.schemas.inspection import DashboardRepeatIssue, DashboardRiskItem, DashboardSummary
from backend.app.services.compliance.registry import rule_legal_reference, rule_title
from backend.app.services.compliance.rule_engine import FAIL, ISSUE_STATUSES, verdict
from backend.app.services.compliance.ruleset import default_ruleset
from backend.app.services.risk import RiskService, photo_keys
from backend.app.services.workflow import latest_extracted_fields

router = APIRouter(prefix="/dashboard", tags=["dashboard"], dependencies=[Depends(get_current_user)])

PERIODS = {"7d": timedelta(days=7), "30d": timedelta(days=30), "all": None}
TOP_RISK_LIMIT = 6
REPEAT_ISSUE_LIMIT = 3
VERDICT_SEVERITY = {"NON_COMPLIANT": 0, "NEEDS_REVIEW": 1, "COMPLIANT": 2}


def _aware(value: datetime) -> datetime:
    # SQLite returns naive datetimes; every stored timestamp is UTC.
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def _labels(db: Session, inspection_id: UUID) -> tuple[str | None, str | None]:
    """Product name and maker (brand owner, else manufacturer) as extracted from the label."""
    fields = {field.field_name: field.value for field in latest_extracted_fields(db, inspection_id)}
    return fields.get("product_name"), fields.get("brand_owner") or fields.get("manufacturer")


def _risk_queue(db: Session, user: User, since: datetime | None) -> list[DashboardRiskItem]:
    if user.role == "OFFICIAL":
        rows = RiskService(db, get_settings().pattern_min_occurrences).for_user(user.id)
        ranked = [(inspection, product, assessment.risk_score, assessment.risk_level, verdict(["REVIEW" if status == "WARNING" else status]))
                  for inspection, product, assessment, status in rows if since is None or _aware(inspection.created_at) >= since]
    else:
        # Manufacturers do not receive numerical targeting evidence; worst verdict first, newest first.
        status = _overall_compliance_status()
        query = select(Inspection, status).where(Inspection.user_id == user.id, Inspection.status == "COMPLETED", status.is_not(None))
        if since is not None:
            query = query.where(Inspection.created_at >= since)
        ranked = [(inspection, None, None, None, verdict([value])) for inspection, value in db.execute(query).all()]
        ranked.sort(key=lambda row: (VERDICT_SEVERITY[row[4]], -_aware(row[0].completed_at or row[0].created_at).timestamp(), str(row[0].id)))
    items = []
    for inspection, product, score, level, verdict_value in ranked[:TOP_RISK_LIMIT]:
        extracted_product, maker = _labels(db, inspection.id)
        items.append(DashboardRiskItem(id=inspection.id, inspection_number=inspection.inspection_number, product_name=product or extracted_product, maker=maker, verdict=verdict_value, risk_score=score, risk_level=level))
    return items


def _repeat_issues(db: Session, scope: list) -> list[DashboardRepeatIssue]:
    """FAIL / REVIEW rules seen on at least the pattern threshold of distinct label photos."""
    rows = db.execute(
        select(Inspection.id, ComplianceRule, ComplianceResult.result)
        .join(ComplianceResult, ComplianceResult.inspection_id == Inspection.id)
        .join(ComplianceRule, ComplianceRule.id == ComplianceResult.rule_id)
        .where(*scope, Inspection.status == "COMPLETED", ComplianceResult.result.in_(ISSUE_STATUSES))
    ).all()
    keys = photo_keys(db, list({inspection_id for inspection_id, _, _ in rows}))
    photos: dict[str, set[str]] = defaultdict(set)
    rules: dict[str, ComplianceRule] = {}
    failed: set[str] = set()
    for inspection_id, rule, result in rows:
        photos[rule.rule_code].add(keys[inspection_id])
        rules[rule.rule_code] = rule
        if result == FAIL:
            failed.add(rule.rule_code)
    minimum = get_settings().pattern_min_occurrences
    ranked = sorted((code for code, seen in photos.items() if len(seen) >= minimum), key=lambda code: (-len(photos[code]), code not in failed, code))
    return [DashboardRepeatIssue(rule_id=code, title=rule_title(rules[code]), legal_reference=rule_legal_reference(rules[code]), label_count=len(photos[code])) for code in ranked[:REPEAT_ISSUE_LIMIT]]


def _median_processing_seconds(db: Session, scope: list) -> float | None:
    """Median time from the latest photo upload to completion; unknown without both timestamps."""
    rows = db.execute(
        select(Inspection.completed_at, func.max(InspectionImage.created_at))
        .join(InspectionImage, InspectionImage.inspection_id == Inspection.id)
        .where(*scope, Inspection.status == "COMPLETED", Inspection.completed_at.is_not(None))
        .group_by(Inspection.id, Inspection.completed_at)
    ).all()
    durations = [seconds for completed, uploaded in rows if uploaded is not None and (seconds := (_aware(completed) - _aware(uploaded)).total_seconds()) >= 0]
    return round(median(durations), 1) if durations else None


@router.get("/summary", response_model=DashboardSummary, summary="Overview dashboard summary for the authenticated user")
def dashboard_summary(
    period: Literal["7d", "30d", "all"] = Query("all"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> DashboardSummary:
    since = datetime.now(timezone.utc) - PERIODS[period] if PERIODS[period] else None
    scope = [Inspection.user_id == user.id] + ([Inspection.created_at >= since] if since is not None else [])
    counts = {"NON_COMPLIANT": 0, "NEEDS_REVIEW": 0, "COMPLIANT": 0, "PENDING": 0}
    for (status,) in db.execute(select(_overall_compliance_status()).select_from(Inspection).where(*scope)).all():
        counts[verdict([status]) if status else "PENDING"] += 1
    return DashboardSummary(
        period=period,
        total_inspections=sum(counts.values()),
        verdict_counts=counts,
        top_risk=_risk_queue(db, user, since),
        repeat_issues=_repeat_issues(db, scope),
        median_processing_seconds=_median_processing_seconds(db, scope),
        ruleset_version=default_ruleset().version,
    )
