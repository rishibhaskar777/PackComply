"""Persisted registration of the deterministic compliance rules."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.models import ComplianceRule
from backend.app.services.compliance.rule_engine import RULES


def ensure_compliance_rules(db: Session) -> dict[str, ComplianceRule]:
    """Return every rule by code, inserting any built-in rule not yet stored."""
    existing = {rule.rule_code: rule for rule in db.scalars(select(ComplianceRule)).all()}
    for code, name, target_field, severity in RULES:
        if code not in existing:
            rule = ComplianceRule(rule_code=code, description=f"{name}. Deterministic extraction-evidence check; it is not a legal determination.", target_field=target_field, severity=severity, enabled=True, configuration={})
            db.add(rule)
            existing[code] = rule
    db.flush()
    return existing
