"""Versioned LM(PC) 2011 ruleset: every rule's PASS / FAIL / REVIEW / NOT_APPLICABLE paths."""
from dataclasses import replace
from datetime import date, datetime, timezone
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.core.database import get_db
from backend.app.main import app
from backend.app.models import Base, ComplianceResult, ComplianceRule, ExtractedField, Inspection, InspectionImage, User
from backend.app.services.compliance.registry import sync_compliance_rules
from backend.app.services.compliance.rule_engine import CHECKS, CONDITIONS, ComplianceEngine, FieldEvidence, overall_status, verdict
from backend.app.services.compliance.ruleset import RulesetError, default_ruleset, parse_ruleset
from backend.app.services.risk import RiskService
from backend.scripts.eval_compliance import evaluate as evaluate_fixtures

TODAY = date(2026, 10, 15)
LEGACY_CODES = {"PRODUCT_IDENTITY", "MANUFACTURER_INFORMATION", "PACKER_INFORMATION", "IMPORTER_INFORMATION", "MRP_FORMAT", "NET_QUANTITY_FORMAT", "DATE_CONSISTENCY", "CONSUMER_CARE_FORMAT"}

# A domestic, fully declared food label: every applicable rule passes.
COMPLETE = {
    "product_name": "Golden Crunch Cookies", "manufacturer": "Golden Crunch Foods Pvt Ltd", "manufacturer_address": "Plot 21, MIDC Ambad, Nashik, MH - 422010",
    "net_quantity": "200 g", "mrp": "₹50.00", "mrp_declaration": "present_with_value", "tax_inclusive": "true", "unit_sale_price": "₹0.25 per g",
    "manufacturing_date": "06/2026", "use_by": "03/2027", "consumer_care_phone": "1800 123 4567", "consumer_care_email": "care@goldencrunch.in",
    "consumer_care_declaration": "present_with_details", "product_category": "food", "fssai_license": "10020022000456",
}


def check(code: str, **changes: str | None) -> tuple[str, str]:
    """Status and message of one rule for COMPLETE with ``changes`` applied (None removes a field)."""
    values = {**COMPLETE, **changes}
    fields = {name: FieldEvidence(value, 0.9, False, f"label: {value}") for name, value in values.items() if value is not None}
    finding = next(item for item in ComplianceEngine().evaluate(fields, today=TODAY) if item.rule_id == code)
    return finding.status, finding.message


# ------------------------------------------------------------------ ruleset

def test_ruleset_keeps_legacy_codes_and_every_rule_has_a_check_and_reference() -> None:
    ruleset = default_ruleset()
    codes = {rule.code for rule in ruleset.rules}
    assert LEGACY_CODES <= codes and codes == set(CHECKS)
    assert (ruleset.ruleset_id, ruleset.version, ruleset.label) == ("LM_PC_2011", "1", "LM(PC) 2011 ruleset v1")
    for rule in ruleset.rules:
        assert rule.legal_reference and rule.title and rule.description and rule.applies_when in CONDITIONS
        assert rule.severity in {"HIGH", "WARNING", "INFO"} and "." not in rule.title
    assert "verify against the official notification" in ruleset.get("UNIT_SALE_PRICE").description
    assert "not a Legal Metrology requirement" in ruleset.get("FSSAI_LICENSE_PRESENT").description


@pytest.mark.parametrize(("change", "error"), [
    ({"severity": "CRITICAL"}, "severity"),
    ({"applies_when": "sometimes"}, "applies_when"),
    ({"code": "NO_SUCH_RULE"}, "no check function"),
    ({"legal_reference": ""}, "legal_reference"),
    ({"target_fields": ["mrp"], "required_fields": ["net_quantity"]}, "required_fields"),
])
def test_malformed_ruleset_fails_at_load_time(change, error) -> None:
    rule = {"code": "MRP_FORMAT", "title": "MRP", "description": "d", "legal_reference": "Rule 6(1)(e)", "severity": "HIGH", "applies_when": "always", **change}
    with pytest.raises(RulesetError, match=error):
        parse_ruleset({"ruleset_id": "X", "version": 1, "rules": [rule]}, known_conditions=frozenset(CONDITIONS), known_codes=frozenset(CHECKS))


def test_disabled_rule_is_not_evaluated() -> None:
    ruleset = default_ruleset()
    disabled = replace(ruleset, rules=tuple(replace(rule, enabled=rule.code != "FSSAI_LICENSE_PRESENT") for rule in ruleset.rules))
    assert "FSSAI_LICENSE_PRESENT" not in {item.rule_id for item in ComplianceEngine(disabled).evaluate({}, today=TODAY)}


def test_complete_label_is_compliant_and_findings_cite_their_rule() -> None:
    findings = ComplianceEngine().evaluate({name: FieldEvidence(value, .9) for name, value in COMPLETE.items()}, today=TODAY)
    assert {item.status for item in findings} == {"PASS", "NOT_APPLICABLE"}
    assert verdict([item.status for item in findings]) == "COMPLIANT" and overall_status([item.status for item in findings]) == "PASS"
    assert all(item.legal_reference for item in findings)
    assert next(item for item in findings if item.rule_id == "MRP_FORMAT").legal_reference == "LM(PC) Rules 2011, Rule 6(1)(e)"


@pytest.mark.parametrize(("statuses", "overall", "expected"), [
    (["PASS", "FAIL", "REVIEW"], "FAIL", "NON_COMPLIANT"),
    (["PASS", "REVIEW", "NOT_APPLICABLE"], "REVIEW", "NEEDS_REVIEW"),
    (["PASS", "NOT_APPLICABLE"], "PASS", "COMPLIANT"),
    (["NOT_APPLICABLE"], "PASS", "COMPLIANT"),
])
def test_verdict_ignores_not_applicable(statuses, overall, expected) -> None:
    assert (overall_status(statuses), verdict(statuses)) == (overall, expected)


# -------------------------------------------------------------------- rules

@pytest.mark.parametrize(("changes", "status", "phrase"), [
    ({}, "PASS", "Manufacturer \"Golden Crunch Foods Pvt Ltd\" is declared"),
    ({"manufacturer": None, "manufacturer_address": None, "brand_owner": "Parle Products Pvt Ltd", "brand_owner_address": "Vile Parle East, Mumbai - 400057"}, "PASS", "Brand owner"),
    ({"manufacturer": None, "manufacturer_address": None, "packer": "Coastal Pack", "packer_address": "Verna, Goa - 403722"}, "PASS", "Packer"),
    ({"manufacturer": None, "manufacturer_address": None, "importer": "Euro Imports", "importer_address": "Bandra, Mumbai - 400050"}, "PASS", "Importer"),
    ({"manufacturer_address": None}, "REVIEW", "no address"),
    ({"manufacturer": None, "manufacturer_address": None}, "FAIL", "No manufacturer"),
])
def test_manufacturer_information(changes, status, phrase) -> None:
    result = check("MANUFACTURER_INFORMATION", **changes)
    assert result[0] == status and phrase in result[1]


@pytest.mark.parametrize(("changes", "status"), [
    ({}, "NOT_APPLICABLE"),
    ({"country_of_origin": "India"}, "NOT_APPLICABLE"),
    ({"importer": "Euro Imports", "importer_address": "Bandra, Mumbai - 400050"}, "PASS"),
    ({"importer": "Euro Imports"}, "FAIL"),
    ({"import_declaration": "present"}, "FAIL"),
    ({"country_of_origin": "Thailand"}, "FAIL"),
])
def test_importer_information(changes, status) -> None:
    assert check("IMPORTER_INFORMATION", **changes)[0] == status


@pytest.mark.parametrize(("changes", "status"), [
    ({}, "NOT_APPLICABLE"),
    ({"country_of_origin": "Thailand"}, "PASS"),
    ({"importer": "Euro Imports", "country_of_origin": "Switzerland"}, "PASS"),
    ({"importer": "Euro Imports"}, "FAIL"),
    ({"import_declaration": "present"}, "FAIL"),
])
def test_country_of_origin(changes, status) -> None:
    assert check("COUNTRY_OF_ORIGIN", **changes)[0] == status


@pytest.mark.parametrize(("changes", "status"), [
    ({}, "NOT_APPLICABLE"),
    ({"packer": "Coastal Pack", "packer_address": "Verna, Goa - 403722"}, "PASS"),
    ({"packer": "Coastal Pack"}, "REVIEW"),
])
def test_packer_information_only_applies_to_a_separately_named_packer(changes, status) -> None:
    assert check("PACKER_INFORMATION", **changes)[0] == status
    assert default_ruleset().get("PACKER_INFORMATION").severity == "INFO"


def test_product_identity() -> None:
    assert check("PRODUCT_IDENTITY")[0] == "PASS"
    assert check("PRODUCT_IDENTITY", product_name=None)[0] == "REVIEW"


@pytest.mark.parametrize(("quantity", "status"), [("200 g", "PASS"), ("0.5 kg", "PASS"), ("12 N", "PASS"), (None, "FAIL"), ("200 boxes", "FAIL"), ("0 g", "FAIL")])
def test_net_quantity_format(quantity, status) -> None:
    assert check("NET_QUANTITY_FORMAT", net_quantity=quantity)[0] == status


@pytest.mark.parametrize(("name", "quantity", "status"), [
    ("Golden Crunch Cookies", "200 g", "PASS"),
    ("Ganga Toned Milk", "1 L", "PASS"),
    ("Ganga Toned Milk", "6 N", "REVIEW"),
    ("Whole Wheat Atta", "1 L", "REVIEW"),
    ("Rice Bran Oil", "1 L", "PASS"),  # liquid words win over solid words
    ("Paneer", "200 g", "NOT_APPLICABLE"),
    ("Golden Crunch Cookies", None, "NOT_APPLICABLE"),
])
def test_net_quantity_unit_suitability_is_conservative(name, quantity, status) -> None:
    assert check("NET_QUANTITY_UNIT_SUITABILITY", product_name=name, net_quantity=quantity)[0] == status


@pytest.mark.parametrize(("changes", "status"), [
    ({}, "PASS"),
    ({"mrp": None, "mrp_declaration": "present_without_value"}, "REVIEW"),
    ({"mrp": None, "mrp_declaration": "absent"}, "FAIL"),
    ({"mrp": None, "mrp_declaration": None}, "FAIL"),
    ({"mrp": "₹0"}, "FAIL"),
    ({"mrp": "$5"}, "FAIL"),
])
def test_mrp_format(changes, status) -> None:
    assert check("MRP_FORMAT", **changes)[0] == status


@pytest.mark.parametrize(("changes", "status"), [
    ({}, "PASS"),
    ({"tax_inclusive": None}, "FAIL"),
    ({"mrp": None, "mrp_declaration": "present_without_value"}, "PASS"),
    ({"mrp": None, "mrp_declaration": "absent", "tax_inclusive": None}, "NOT_APPLICABLE"),
])
def test_mrp_tax_inclusive(changes, status) -> None:
    assert check("MRP_TAX_INCLUSIVE", **changes)[0] == status


@pytest.mark.parametrize(("changes", "status", "phrase"), [
    ({}, "PASS", "consistent"),
    ({"unit_sale_price": "Rs 0.26/g"}, "REVIEW", "differs by 4.0%"),  # 4% > 2% and > the ₹0.005 rounding slack
    ({"unit_sale_price": None}, "FAIL", "₹0.25 per g"),
    ({"unit_sale_price": "₹0.30 per g"}, "REVIEW", "differs by 20.0%"),
    ({"unit_sale_price": "₹250 per kg"}, "REVIEW", "not per g"),
    ({"net_quantity": "5 kg", "mrp": "₹1250", "unit_sale_price": "₹250 per kg"}, "PASS", "per kg"),
    ({"net_quantity": "1 kg", "mrp": "₹240", "unit_sale_price": "₹0.24 per g"}, "PASS", "per g"),  # exactly 1 kg is per gram
    ({"net_quantity": "12 N", "mrp": "₹120", "unit_sale_price": "₹10 per unit"}, "PASS", "per unit"),
    ({"net_quantity": "1 N", "mrp": "₹120", "unit_sale_price": None}, "NOT_APPLICABLE", "equals the MRP"),
    ({"unit_sale_price": "about a quarter rupee"}, "REVIEW", "could not be read"),
    ({"mrp": None, "mrp_declaration": "present_without_value"}, "NOT_APPLICABLE", "stamped separately"),
    ({"net_quantity": None}, "NOT_APPLICABLE", "no net quantity"),
])
def test_unit_sale_price(changes, status, phrase) -> None:
    result = check("UNIT_SALE_PRICE", **changes)
    assert result[0] == status and phrase in result[1], result


def test_unit_sale_price_tolerance_comes_from_configuration() -> None:
    assert check("UNIT_SALE_PRICE", net_quantity="52 g", mrp="₹20", unit_sale_price="₹0.38 per g")[0] == "PASS"  # 0.3846 rounded
    config = default_ruleset().get("UNIT_SALE_PRICE").configuration
    assert config["tolerance"] == 0.02 and config["base_units"]["g"][0] == {"up_to": 1000, "unit": "g", "factor": 1}


@pytest.mark.parametrize(("changes", "status"), [
    ({}, "PASS"),
    ({"manufacturing_date": None, "packing_date": "02/2026"}, "PASS"),
    ({"manufacturing_date": None}, "REVIEW"),
])
def test_manufacture_date_is_never_a_violation_from_the_photo_alone(changes, status) -> None:
    result = check("MANUFACTURE_DATE", **changes)
    assert result[0] == status
    if status == "REVIEW":
        assert "stamped elsewhere" in result[1]


@pytest.mark.parametrize(("changes", "status"), [
    ({}, "PASS"),
    ({"manufacturing_date": "04/2027"}, "FAIL"),  # after use-by
    ({"manufacturing_date": "10/09/2026", "use_by": "05/09/2026"}, "FAIL"),
    ({"manufacturing_date": "15/03/2026", "use_by": "03/2026"}, "PASS"),  # month precision: same month
    ({"manufacturing_date": "01/2027", "use_by": None}, "FAIL"),  # in the future beyond one month
    ({"manufacturing_date": "11/2026", "use_by": None}, "NOT_APPLICABLE"),  # within the one-month tolerance
    ({"manufacturing_date": "sometime soon"}, "REVIEW"),
    ({"use_by": None}, "NOT_APPLICABLE"),
    ({"use_by": None, "best_before": "9 months from manufacture"}, "NOT_APPLICABLE"),
    ({"use_by": None, "best_before": "05/2026"}, "FAIL"),
])
def test_date_consistency(changes, status) -> None:
    assert check("DATE_CONSISTENCY", **changes)[0] == status


@pytest.mark.parametrize(("changes", "status"), [
    ({}, "PASS"),
    ({"consumer_care_email": None}, "PASS"),
    ({"consumer_care_phone": "12345"}, "REVIEW"),
    ({"consumer_care_email": "not-an-email"}, "REVIEW"),
    ({"consumer_care_phone": None, "consumer_care_email": None, "consumer_care_website": "www.goldencrunch.in"}, "REVIEW"),
    ({"consumer_care_phone": None, "consumer_care_email": None, "consumer_care_declaration": "present_without_details"}, "REVIEW"),
    ({"consumer_care_phone": None, "consumer_care_email": None, "consumer_care_declaration": "absent"}, "FAIL"),
])
def test_consumer_care_format(changes, status) -> None:
    assert check("CONSUMER_CARE_FORMAT", **changes)[0] == status
    assert default_ruleset().get("CONSUMER_CARE_FORMAT").legal_reference == "LM(PC) Rules 2011, Rule 6(2)"


@pytest.mark.parametrize(("changes", "status"), [
    ({}, "PASS"),
    ({"fssai_license": None}, "REVIEW"),
    ({"fssai_license": "1001302200"}, "REVIEW"),
    ({"product_category": None}, "NOT_APPLICABLE"),
])
def test_fssai_cross_check(changes, status) -> None:
    assert check("FSSAI_LICENSE_PRESENT", **changes)[0] == status


def test_every_rule_has_tested_paths_for_each_status_it_can_return() -> None:
    """Guard: a new rule code must come with tests in this module."""
    tested = {"MANUFACTURER_INFORMATION", "IMPORTER_INFORMATION", "COUNTRY_OF_ORIGIN", "PACKER_INFORMATION", "PRODUCT_IDENTITY", "NET_QUANTITY_FORMAT", "NET_QUANTITY_UNIT_SUITABILITY", "MRP_FORMAT", "MRP_TAX_INCLUSIVE", "UNIT_SALE_PRICE", "MANUFACTURE_DATE", "DATE_CONSISTENCY", "CONSUMER_CARE_FORMAT", "FSSAI_LICENSE_PRESENT"}
    assert tested == set(CHECKS)


# ------------------------------------------------------------ fixtures

def test_fixture_rule_outcomes_and_verdicts_match_ground_truth() -> None:
    rows, verdicts = evaluate_fixtures(today=TODAY)
    assert [f"{row.fixture}.{row.rule}: expected {row.expected}, got {row.got} ({row.message})" for row in rows if not row.ok] == []
    assert {name: got for name, (want, got) in verdicts.items() if want != got} == {}
    assert verdicts["parle_g_gluco_70g"] == ("NEEDS_REVIEW", "NEEDS_REVIEW")
    assert verdicts["synthetic_fully_compliant"][1] == "COMPLIANT"


# ------------------------------------------------------ persistence & API

@pytest.fixture
def database():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    yield sessions
    engine.dispose()


def test_sync_updates_metadata_and_never_deletes_rules(database) -> None:
    with database() as db:
        retired = ComplianceRule(rule_code="RETIRED_RULE", description="Old.", target_field="x", severity="INFO", enabled=True, configuration={})
        stale = ComplianceRule(rule_code="MRP_FORMAT", description="MRP format.", target_field="mrp", severity="WARNING", enabled=True, configuration={})
        db.add_all([retired, stale]); db.commit()
        rules = sync_compliance_rules(db); db.commit()
        assert rules["MRP_FORMAT"].id == stale.id and rules["MRP_FORMAT"].severity == "HIGH"
        assert rules["MRP_FORMAT"].configuration["legal_reference"] == "LM(PC) Rules 2011, Rule 6(1)(e)"
        assert rules["MRP_FORMAT"].configuration["ruleset_version"] == "1"
        assert db.scalar(select(ComplianceRule).where(ComplianceRule.rule_code == "RETIRED_RULE")) is not None
        assert len(rules) == len(default_ruleset().rules) + 1


def test_rules_endpoint_lists_active_rules_with_legal_reference(database) -> None:
    def override():
        with database() as session:
            yield session
    app.dependency_overrides[get_db] = override
    client = TestClient(app)
    body = client.get("/api/compliance/rules").json()
    assert body["label"] == "LM(PC) 2011 ruleset v1" and body["version"] == "1"
    rules = {rule["code"]: rule for rule in body["rules"]}
    assert set(rules) == {rule.code for rule in default_ruleset().enabled_rules}
    assert rules["COUNTRY_OF_ORIGIN"] == {**rules["COUNTRY_OF_ORIGIN"], "legal_reference": "LM(PC) Rules 2011, Rule 6(1)(aa)", "severity": "HIGH", "title": "Country of origin (imported goods)"}
    app.dependency_overrides.clear()
    assert TestClient(app).get("/api/compliance/rules").status_code == 401


def _completed_inspection(db, user_id: UUID, statuses: dict[str, str], missing: tuple[str, ...] = (), checksum: str | None = None) -> Inspection:
    rules = sync_compliance_rules(db)
    inspection = Inspection(id=uuid4(), inspection_number=f"PC-{uuid4().hex[:8]}", user_id=user_id, status="COMPLETED", completed_at=datetime.now(timezone.utc), inspected_at=datetime.now(timezone.utc))
    db.add(inspection); db.flush()
    if checksum:
        db.add(InspectionImage(inspection_id=inspection.id, upload_sequence=1, storage_key=f"{uuid4().hex}.png", original_filename="label.png", content_type="image/png", byte_size=10, checksum=checksum))
    db.add_all(ComplianceResult(inspection_id=inspection.id, rule_id=rules[code].id, result=status, explanation=f"{code} {status}", ruleset_id="LM_PC_2011", ruleset_version="1") for code, status in statuses.items())
    db.add_all(ExtractedField(inspection_id=inspection.id, field_name=name, value=None, review_status="REVIEW") for name in missing)
    db.commit()
    return inspection


def _user(db) -> User:
    user = User(id=uuid4(), username=uuid4().hex[:10], email=f"{uuid4().hex[:8]}@example.invalid", full_name="U", password_hash="x", is_active=True)
    db.add(user); db.commit()
    return user


COMPLIANT_STATUSES = {code: "PASS" for code in CHECKS} | {"IMPORTER_INFORMATION": "NOT_APPLICABLE", "COUNTRY_OF_ORIGIN": "NOT_APPLICABLE", "PACKER_INFORMATION": "NOT_APPLICABLE"}
PARLE_STATUSES = {code: "PASS" for code in CHECKS} | {"MRP_FORMAT": "REVIEW", "MANUFACTURE_DATE": "REVIEW", "CONSUMER_CARE_FORMAT": "REVIEW", "IMPORTER_INFORMATION": "NOT_APPLICABLE", "COUNTRY_OF_ORIGIN": "NOT_APPLICABLE", "PACKER_INFORMATION": "NOT_APPLICABLE", "UNIT_SALE_PRICE": "NOT_APPLICABLE", "DATE_CONSISTENCY": "NOT_APPLICABLE"}
# Core fields extraction stores even when empty, as on the Parle-G back label.
PARLE_MISSING = ("mrp", "manufacturing_date", "packing_date", "consumer_care_phone", "consumer_care_email", "consumer_care_website", "importer", "importer_address", "packer", "packer_address", "best_before", "use_by", "expiry_date")


def test_compliant_label_scores_low_whatever_optional_fields_are_missing(database) -> None:
    with database() as db:
        # Optional / informational or not-applicable fields never add points on their own.
        compliant = _completed_inspection(db, _user(db).id, COMPLIANT_STATUSES, missing=("importer", "importer_address", "packer", "packer_address", "best_before", "use_by", "consumer_care_website", "batch_number"))
        assessment = RiskService(db).for_inspection(compliant)
    assert (assessment.risk_score, assessment.risk_level, assessment.reasons) == (0, "LOW", [])


def test_parle_g_style_review_score_is_explained_and_review_repeats_add_nothing(database) -> None:
    with database() as db:
        user = _user(db)
        # Five distinct photos with the same REVIEW findings: still no repeat points.
        inspections = [_completed_inspection(db, user.id, PARLE_STATUSES, PARLE_MISSING, checksum=f"photo-{index}") for index in range(5)]
        assessment = RiskService(db).for_inspection(inspections[0])
    points = {(reason.type, reason.rule_code or reason.field): reason.points for reason in assessment.reasons}
    # Only the REVIEW points: HIGH 20 + WARNING 12 + WARNING 12. Fields missing under a REVIEW
    # are not counted again, and REVIEWs are not "multiple violations" or repeats.
    assert points == {("COMPLIANCE_WARNING", "CONSUMER_CARE_FORMAT"): 12, ("COMPLIANCE_WARNING", "MANUFACTURE_DATE"): 12, ("COMPLIANCE_WARNING", "MRP_FORMAT"): 20}
    assert (assessment.risk_score, assessment.risk_level) == (44, "MEDIUM")


@pytest.mark.parametrize(("fixture", "score", "level"), [("synthetic_fully_compliant", 0, "LOW"), ("parle_g_gluco_70g", 44, "MEDIUM"), ("synthetic_near_empty", 100, "CRITICAL")])
def test_fixture_labels_score_through_the_real_pipeline(database, fixture, score, level) -> None:
    import json
    from backend.app.models import OcrResult
    from backend.app.services.extraction import ExtractionService
    from backend.app.services.extraction.evidence import build_extracted_fields, field_evidence
    from backend.app.services.workflow import persist_compliance
    from backend.scripts.eval_extraction import FIXTURE_DIR
    label = json.loads((FIXTURE_DIR / f"{fixture}.json").read_text(encoding="utf-8"))
    with database() as db:
        user = _user(db)
        inspection = Inspection(id=uuid4(), inspection_number="PC-FIXTURE", user_id=user.id, status="COMPLETED", completed_at=datetime.now(timezone.utc), inspected_at=datetime.now(timezone.utc))
        ocr = OcrResult(id=uuid4(), inspection_id=inspection.id, raw_text=label["raw_text"], detections=label["detections"] or [], languages=["en"])
        db.add_all([inspection, ocr]); db.flush()
        stored = build_extracted_fields(inspection.id, ocr, ExtractionService().extract(label["raw_text"], label["detections"]))
        db.add_all(stored); db.commit()
        persist_compliance(db, ComplianceEngine(), inspection.id, {field.field_name: field_evidence(field) for field in stored})
        assessment = RiskService(db).for_inspection(inspection)
    assert (assessment.risk_score, assessment.risk_level) == (score, level), assessment.reasons


def test_missing_fields_and_multiple_findings_count_violations_only(database) -> None:
    with database() as db:
        user = _user(db)
        # One FAIL and two REVIEWs: only the failing rule's required field scores; three issues are not three violations.
        mixed = _completed_inspection(db, user.id, COMPLIANT_STATUSES | {"NET_QUANTITY_FORMAT": "FAIL", "MRP_FORMAT": "REVIEW", "MANUFACTURE_DATE": "REVIEW"}, ("net_quantity", "mrp", "manufacturing_date"))
        points = {(reason.type, reason.rule_code or reason.field): reason.points for reason in RiskService(db).for_inspection(mixed).reasons}
        assert points == {("COMPLIANCE_FAILURE", "NET_QUANTITY_FORMAT"): 35, ("MISSING_FIELD", "net_quantity"): 6, ("COMPLIANCE_WARNING", "MRP_FORMAT"): 20, ("COMPLIANCE_WARNING", "MANUFACTURE_DATE"): 12}
        # Three distinct FAILs are multiple violations.
        failing = _completed_inspection(db, user.id, COMPLIANT_STATUSES | {"NET_QUANTITY_FORMAT": "FAIL", "MRP_FORMAT": "FAIL", "CONSUMER_CARE_FORMAT": "FAIL"})
        assert any(reason.type == "MULTIPLE_FINDINGS" and reason.points == 8 for reason in RiskService(db).for_inspection(failing).reasons)


def test_repeated_violations_count_once_per_distinct_photo(database) -> None:
    violation = COMPLIANT_STATUSES | {"NET_QUANTITY_FORMAT": "FAIL"}
    with database() as db:
        user = _user(db)
        first = _completed_inspection(db, user.id, violation, ("net_quantity",), checksum="same-photo")
        _completed_inspection(db, user.id, violation, ("net_quantity",), checksum="same-photo")
        assert not any(reason.type == "REPEATED_PATTERN" for reason in RiskService(db).for_inspection(first).reasons)
        assert RiskService(db).for_inspection(first).risk_score == 35 + 6
        _completed_inspection(db, user.id, violation, ("net_quantity",), checksum="another-photo")
        repeats = {(reason.rule_code or reason.field): (reason.points, reason.occurrences) for reason in RiskService(db).for_inspection(first).reasons if reason.type == "REPEATED_PATTERN"}
    # Three inspections, two distinct photos: the violation and its missing required field repeat twice.
    assert repeats == {"NET_QUANTITY_FORMAT": (12, 2), "net_quantity": (8, 2)}


def test_compliance_response_reports_verdict_ruleset_and_not_applicable(database) -> None:
    from backend.app.services.workflow import compliance_response
    with database() as db:
        user = User(id=uuid4(), username="resp", email="resp@example.invalid", full_name="R", password_hash="x", is_active=True); db.add(user); db.commit()
        inspection = _completed_inspection(db, user.id, {"MRP_FORMAT": "REVIEW", "IMPORTER_INFORMATION": "NOT_APPLICABLE", "PRODUCT_IDENTITY": "PASS"})
        results = db.scalars(select(ComplianceResult).where(ComplianceResult.inspection_id == inspection.id)).all()
        response = compliance_response(inspection.id, list(results), [])
    assert (response.overall_status, response.verdict, response.ruleset_label) == ("REVIEW", "NEEDS_REVIEW", "LM(PC) 2011 ruleset v1")
    assert (response.summary.passed, response.summary.review, response.summary.failed, response.summary.not_applicable) == (1, 1, 0, 1)
    mrp = next(item for item in response.findings if item.rule_id == "MRP_FORMAT")
    assert mrp.legal_reference == "LM(PC) Rules 2011, Rule 6(1)(e)" and "physical pack" in mrp.recommended_action and mrp.rule_name == "Retail sale price (MRP)"


def test_report_groups_findings_and_states_verdict_and_ruleset(database, monkeypatch, tmp_path) -> None:
    from backend.app.services.report import InspectionReportService
    with database() as db:
        user = User(id=uuid4(), username="pdf", email="pdf@example.invalid", full_name="P", password_hash="x", role="OFFICIAL", is_active=True); db.add(user); db.commit()
        inspection = _completed_inspection(db, user.id, {"COUNTRY_OF_ORIGIN": "FAIL", "MRP_FORMAT": "REVIEW", "PRODUCT_IDENTITY": "PASS", "PACKER_INFORMATION": "NOT_APPLICABLE"})
        captured = {}
        monkeypatch.setattr("backend.app.services.report.SimpleDocTemplate.build", lambda self, story, **kwargs: captured.setdefault("story", story))
        InspectionReportService(db, tmp_path).generate(inspection)
    story = captured["story"]
    headings = [flowable.getPlainText() for flowable in story if hasattr(flowable, "getPlainText")]
    for heading in ("Violations", "Needs review - cannot be confirmed from this photo", "Passed", "Not applicable"):
        assert heading in headings
    cells = " ".join(cell.getPlainText() for flowable in story if hasattr(flowable, "_cellvalues") for row in flowable._cellvalues for cell in row if hasattr(cell, "getPlainText"))
    assert "NON-COMPLIANT" in cells and "LM(PC) 2011 ruleset v1" in cells
    cards = [flowable for flowable in story if flowable.__class__.__name__ == "KeepTogether"]
    rows = {row[0].getPlainText(): row[1].getPlainText() for row in cards[0]._content[0]._cellvalues}
    assert rows["Legal reference"] == "LM(PC) Rules 2011, Rule 6(1)(aa)" and rows["Expected requirement"].startswith('"Country of origin')
    assert "country of origin" in rows["Recommended action"]
