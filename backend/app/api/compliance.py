"""Read-only view of the active, versioned compliance ruleset."""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from backend.app.api.auth import get_current_user
from backend.app.core.database import get_db
from backend.app.schemas.inspection import ComplianceRuleResponse, ComplianceRulesetResponse
from backend.app.services.compliance.registry import rule_legal_reference, rule_title, sync_compliance_rules
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
        rules=[ComplianceRuleResponse(code=rule.rule_code, title=rule_title(rule), description=rule.description, legal_reference=rule_legal_reference(rule), severity=rule.severity, applies_when=rule.configuration.get("applies_when")) for rule in rules],
    )
