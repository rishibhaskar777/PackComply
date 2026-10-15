from datetime import date
from decimal import Decimal
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.core.database import get_db
from backend.app.main import app
from backend.app.models import Base, ComplianceResult, ExtractedField, OcrResult
from backend.app.services.compliance.rule_engine import ComplianceEngine, FieldEvidence
import pytest


def evidence(**values: FieldEvidence) -> dict[str, FieldEvidence]:
    return values


COMPLETE_LABEL = {
    "product_name": "Biscuits", "manufacturer": "ABC Foods", "manufacturer_address": "Plot 1, MIDC, Pune - 411026",
    "packer": "XYZ Pack", "packer_address": "Plot 2, MIDC, Pune - 411026",
    "importer": "Example Imports", "importer_address": "4 Andheri East, Mumbai - 400069", "country_of_origin": "Thailand",
    "mrp": "₹100", "mrp_declaration": "present_with_value", "tax_inclusive": "true", "net_quantity": "500 g",
    "unit_sale_price": "₹0.20 per g", "manufacturing_date": "08/2026", "expiry_date": "12/2026",
    "consumer_care_email": "care@example.com", "product_category": "food", "fssai_license": "10012022000789",
}


def test_valid_label_rules_pass() -> None:
    findings = ComplianceEngine().evaluate({name: FieldEvidence(value, .95) for name, value in COMPLETE_LABEL.items()}, today=date(2026, 10, 15))
    assert {finding.status for finding in findings} == {"PASS"}


@pytest.mark.parametrize(("raw", "normalized"), [
    ("500 g", "500 g"), ("500g", "500 g"), ("0.5 kg", "500 g"),
    ("  500   G  ", "500 g"), ("500 GMS", "500 g"), ("500 mL", "500 ml"),
    ("0.5 L", "500 ml"), ("5OO g", "500 g"), ("500 grams", "500 g"),
    ("500\u00a0g", "500 g"), ("500 m l", "500 ml"), ("500 millilitres", "500 ml"),
])
def test_quantity_formatting_variants_are_semantically_valid(raw: str, normalized: str) -> None:
    engine = ComplianceEngine()
    finding = next(item for item in engine.evaluate(evidence(net_quantity=FieldEvidence(raw, .96))) if item.rule_id == "NET_QUANTITY_FORMAT")
    assert engine.normalize_quantity(raw) == normalized
    assert finding.status == "PASS"


@pytest.mark.parametrize("raw", ["", "0 g", "five hundred g", "500 boxes", "500 gg"])
def test_invalid_or_missing_quantities_remain_issues(raw: str) -> None:
    finding = next(item for item in ComplianceEngine().evaluate(evidence(net_quantity=FieldEvidence(raw, .96))) if item.rule_id == "NET_QUANTITY_FORMAT")
    assert finding.status in {"FAIL", "REVIEW"}


def test_invalid_values_dates_and_uncertainty_are_explainable() -> None:
    findings = ComplianceEngine().evaluate(evidence(
        product_name=FieldEvidence("Product", .4, True), mrp=FieldEvidence("₹0", .9),
        net_quantity=FieldEvidence("seven boxes", .9), manufacturing_date=FieldEvidence("12/2026", .9),
        expiry_date=FieldEvidence("08/2026", .9), consumer_care_email=FieldEvidence("not-an-email", .9),
    ))
    by_rule = {finding.rule_id: finding for finding in findings}
    assert by_rule["MRP_FORMAT"].status == "FAIL"
    assert by_rule["NET_QUANTITY_FORMAT"].status == "FAIL"
    assert by_rule["DATE_CONSISTENCY"].status == "FAIL"
    assert by_rule["CONSUMER_CARE_FORMAT"].status == "REVIEW"
    # Low confidence is reported per extracted field (needs_review), never as an unregistered rule.
    assert not any(rule.startswith("LOW_CONFIDENCE_") for rule in by_rule)


def test_ambiguous_date_requires_review() -> None:
    finding = next(finding for finding in ComplianceEngine().evaluate(evidence(manufacturing_date=FieldEvidence("sometime soon", .9))) if finding.rule_id == "DATE_CONSISTENCY")
    assert finding.status == "REVIEW"


def test_compliance_api_persists_and_replaces_deterministically() -> None:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine)

    def override_db():
        with sessions() as session:
            yield session

    app.dependency_overrides[get_db] = override_db
    try:
        client = TestClient(app)
        inspection = client.post("/api/inspections", json={}).json()
        with sessions() as session:
            ocr = OcrResult(inspection_id=UUID(inspection["id"]), raw_text="label", languages=["en"], detections=[])
            session.add(ocr); session.flush()
            for name, value in COMPLETE_LABEL.items():
                session.add(ExtractedField(inspection_id=ocr.inspection_id, ocr_result_id=ocr.id, field_name=name, value=value, confidence=Decimal("0.95"), review_status="EXTRACTED"))
            session.commit()
        response = client.post(f"/api/inspections/{inspection['id']}/compliance")
        assert response.status_code == 200
        assert response.json()["overall_status"] == "PASS" and response.json()["verdict"] == "COMPLIANT"
        assert response.json()["summary"]["failed"] == 0
        assert response.json()["ruleset_label"] == "LM(PC) 2011 ruleset v2"
        rerun = client.post(f"/api/inspections/{inspection['id']}/compliance")
        assert rerun.status_code == 200 and rerun.json() == response.json()
        assert client.get(f"/api/inspections/{inspection['id']}/compliance").json() == response.json()
        with sessions() as session:
            assert session.scalar(select(ComplianceResult).where(ComplianceResult.inspection_id == UUID(inspection["id"]))) is not None
        assert client.post(f"/api/inspections/{uuid4()}/compliance").status_code == 404
        waiting = client.post("/api/inspections", json={}).json()
        assert client.post(f"/api/inspections/{waiting['id']}/compliance").status_code == 409
        with sessions() as session:
            session.add(OcrResult(inspection_id=UUID(waiting["id"]), raw_text="label", languages=["en"], detections=[]))
            session.commit()
        assert client.post(f"/api/inspections/{waiting['id']}/compliance").status_code == 409
    finally:
        app.dependency_overrides.clear()
        engine.dispose()
