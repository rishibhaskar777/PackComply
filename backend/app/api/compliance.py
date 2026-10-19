"""Read-only view of the active, versioned compliance ruleset."""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from backend.app.api.auth import get_current_user
from backend.app.core.database import get_db
from backend.app.schemas.inspection import ComplianceRuleResponse, ComplianceRulesetResponse
from backend.app.services.compliance.registry import rule_expected, rule_legal_reference, rule_title, sync_compliance_rules
from backend.app.services.compliance.remediation import remediation_for, rule_chip
from backend.app.services.compliance.ruleset import default_ruleset

router = APIRouter(prefix="/compliance", tags=["compliance"], dependencies=[Depends(get_current_user)])


@router.get("/rules", response_model=ComplianceRulesetResponse, summary="List the active compliance rules")
def list_compliance_rules(db: Session = Depends(get_db)) -> ComplianceRulesetResponse:
    """Enabled rules of the current ruleset, in ruleset order, as stored in compliance_rules."""
    ruleset = default_ruleset()
    stored = sync_compliance_rules(db, ruleset)
    db.commit()
    rules = [stored[definition.code] for definition in ruleset.rules if stored[definition.code].enabled]
    return ComplianceRulesetResponse(
        ruleset_id=ruleset.ruleset_id, version=ruleset.version, label=ruleset.label, title=ruleset.title, disclaimer=ruleset.disclaimer,
        rules=[_rule_response(rule) for rule in rules],
    )


def _rule_response(rule) -> ComplianceRuleResponse:
    entry = remediation_for(rule.rule_code)
    reference = rule_legal_reference(rule)
    return ComplianceRuleResponse(
        code=rule.rule_code, title=rule_title(rule), description=rule.description, legal_reference=reference, severity=rule.severity, applies_when=rule.configuration.get("applies_when"),
        expected=rule_expected(rule), rule_chip=rule_chip(reference),
        fix_title=entry.problem_title if entry else None, fix_steps=list(entry.fix_steps) if entry else [], example=entry.example if entry else None, pass_text=entry.pass_text if entry else None,
    )
