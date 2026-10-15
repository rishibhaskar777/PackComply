"""Persisted registration of the versioned compliance ruleset."""
from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.models import ComplianceResult, ComplianceRule
from backend.app.services.compliance.ruleset import RuleDefinition, Ruleset, default_ruleset

logger = logging.getLogger(__name__)


def rule_configuration(rule: RuleDefinition, ruleset: Ruleset) -> dict:
    return {
        "title": rule.title,
        "legal_reference": rule.legal_reference,
        "applies_when": rule.applies_when,
        "target_fields": list(rule.target_fields),
        "required_fields": list(rule.required_fields),
        "expected": rule.expected,
        "actions": rule.actions,
        "ruleset_id": ruleset.ruleset_id,
        "ruleset_version": ruleset.version,
        **({"settings": rule.configuration} if rule.configuration else {}),
    }


def sync_compliance_rules(db: Session, ruleset: Ruleset | None = None) -> dict[str, ComplianceRule]:
    """Make the compliance_rules table match the ruleset; returns every rule by code.

    Rules are updated in place and never deleted: results from earlier
    inspections keep pointing at them, including codes the ruleset dropped.
    """
    ruleset = ruleset or default_ruleset()
    existing = {rule.rule_code: rule for rule in db.scalars(select(ComplianceRule)).all()}
    changed = 0
    for definition in ruleset.rules:
        values = {
            "description": definition.description,
            "target_field": definition.target_field,
            "severity": definition.severity,
            "enabled": definition.enabled,
            "configuration": rule_configuration(definition, ruleset),
        }
        rule = existing.get(definition.code)
        if rule is None:
            rule = ComplianceRule(rule_code=definition.code, **values)
            db.add(rule)
            existing[definition.code] = rule
            changed += 1
        elif any(getattr(rule, key) != value for key, value in values.items()):
            for key, value in values.items():
                setattr(rule, key, value)
            changed += 1
    db.flush()
    if changed:
        logger.info("Compliance rules synced ruleset=%s version=%s changed=%d", ruleset.ruleset_id, ruleset.version, changed)
    return existing


# Evaluation paths call this before persisting results; it is the same sync.
ensure_compliance_rules = sync_compliance_rules


def rule_title(rule: ComplianceRule) -> str:
    """Display title; rules stored before the ruleset had it as the description's first sentence."""
    return (rule.configuration or {}).get("title") or rule.description.split(".", 1)[0]


def rule_legal_reference(rule: ComplianceRule) -> str | None:
    return (rule.configuration or {}).get("legal_reference")


def rule_action(rule: ComplianceRule, status: str) -> str | None:
    return ((rule.configuration or {}).get("actions") or {}).get(status)


def rule_expected(rule: ComplianceRule) -> str:
    return (rule.configuration or {}).get("expected") or rule.description


def results_ruleset(results: list[ComplianceResult]) -> tuple[str | None, str | None, str | None]:
    """(ruleset_id, version, label) shared by one inspection's results, e.g. "LM(PC) 2011 ruleset v1".

    All None for results stored before versioned rulesets (or, defensively, a mix).
    """
    versions = {(result.ruleset_id, result.ruleset_version) for result in results}
    ruleset_id, version = versions.pop() if len(versions) == 1 else (None, None)
    if not ruleset_id:
        return None, None, None
    current = default_ruleset()
    label = current.label if (ruleset_id, version) == (current.ruleset_id, current.version) else f"{ruleset_id} v{version}"
    return ruleset_id, version, label
