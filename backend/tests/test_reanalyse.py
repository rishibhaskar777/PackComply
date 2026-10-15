"""Re-analysis without re-OCR, the bulk script, and compliance pass-through of new fields."""
import json
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.api.auth import get_current_user
from backend.app.core.database import get_db
from backend.app.main import app
from backend.app.models import Base, ComplianceResult, ExtractedField, Inspection, OcrResult, User
from backend.app.services.compliance.rule_engine import ComplianceEngine, FieldEvidence
from backend.app.services.extraction.evidence import field_evidence
from backend.scripts import reanalyse_all
from backend.scripts.eval_extraction import FIXTURE_DIR
from backend.tests.conftest import TEST_USER_ID


@pytest.fixture
def api():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool); Base.metadata.create_all(engine); sessions = sessionmaker(bind=engine)
    def override_db():
        with sessions() as session: yield session
    app.dependency_overrides[get_db] = override_db
    try:
        yield TestClient(app), sessions
    finally:
        app.dependency_overrides.pop(get_db, None); engine.dispose()


def parle_ocr(inspection_id: UUID) -> OcrResult:
    fixture = json.loads((FIXTURE_DIR / "parle_g_gluco_70g.json").read_text(encoding="utf-8"))
    return OcrResult(inspection_id=inspection_id, engine="PaddleOCR", languages=fixture["languages"], raw_text=fixture["raw_text"], detections=fixture["detections"])


def stale_rows(session, inspection_id: UUID, ocr: OcrResult) -> None:
    session.add(ExtractedField(inspection_id=inspection_id, ocr_result_id=ocr.id, field_name="manufacturer", value="FOR", confidence=0.94, review_status="EXTRACTED"))
    session.commit()


def test_reanalyse_replaces_extraction_and_compliance_from_stored_ocr(api) -> None:
    client, sessions = api
    inspection = client.post("/api/inspections", json={}).json()
    inspection_id = UUID(inspection["id"])
    with sessions() as session:
        ocr = parle_ocr(inspection_id); session.add(ocr); session.commit()
        stale_rows(session, inspection_id, ocr)
        detections_before = list(ocr.detections)

    response = client.post(f"/api/inspections/{inspection_id}/reanalyse")

    assert response.status_code == 200
    payload = response.json()
    fields = payload["extraction"]["fields"]
    assert payload["status"] == "COMPLETED"
    assert fields["manufacturer"]["value"] == "CF FOODS PVT LTD" and fields["brand_owner"]["value"] == "PARLE PRODUCTS PVT LTD"
    assert fields["net_quantity"]["value"] == "70 g" and fields["mrp_declaration"]["value"] == "present_without_value"
    assert fields["net_quantity"]["source_bboxes"]
    findings = {finding["rule_id"]: finding for finding in payload["compliance"]["findings"]}
    assert findings["NET_QUANTITY_FORMAT"]["status"] == "PASS" and findings["MANUFACTURER_INFORMATION"]["status"] == "PASS"
    with sessions() as session:
        names = [row.field_name for row in session.scalars(select(ExtractedField).where(ExtractedField.inspection_id == inspection_id))]
        assert names.count("manufacturer") == 1  # the stale "FOR" row was replaced
        assert session.scalar(select(OcrResult).where(OcrResult.inspection_id == inspection_id)).detections == detections_before
        first_results = session.query(ComplianceResult).filter_by(inspection_id=inspection_id).count()
    # Idempotent: a second run replaces rather than duplicates.
    assert client.post(f"/api/inspections/{inspection_id}/reanalyse").status_code == 200
    with sessions() as session:
        assert session.query(ComplianceResult).filter_by(inspection_id=inspection_id).count() == first_results


def test_reanalyse_requires_ocr_and_ownership(api) -> None:
    client, sessions = api
    inspection = client.post("/api/inspections", json={}).json()
    response = client.post(f"/api/inspections/{inspection['id']}/reanalyse")
    assert response.status_code == 409 and "OCR" in response.json()["detail"]
    assert client.post(f"/api/inspections/{uuid4()}/reanalyse").status_code == 404
    with sessions() as session:
        session.add(parle_ocr(UUID(inspection["id"]))); session.commit()
    app.dependency_overrides[get_current_user] = lambda: User(id=uuid4(), username="other", email="other@example.invalid", full_name="Other", password_hash="unused", is_active=True)
    assert client.post(f"/api/inspections/{inspection['id']}/reanalyse").status_code == 404


def test_reanalyse_all_script_processes_completed_inspections(api, monkeypatch, capsys) -> None:
    _, sessions = api
    with sessions() as session:
        done = Inspection(inspection_number="PC-1", status="COMPLETED", user_id=TEST_USER_ID)
        no_ocr = Inspection(inspection_number="PC-2", status="COMPLETED", user_id=TEST_USER_ID)
        pending = Inspection(inspection_number="PC-3", status="CREATED", user_id=TEST_USER_ID)
        session.add_all([done, no_ocr, pending]); session.commit()
        session.add(parle_ocr(done.id)); session.commit()
        done_id = done.id
    monkeypatch.setattr(reanalyse_all, "SessionLocal", sessions)
    monkeypatch.setattr("sys.argv", ["reanalyse_all"])

    reanalyse_all.main()

    output = capsys.readouterr().out
    assert "inspections=2 reanalysed=1 skipped_no_ocr=1 failed=0" in output
    with sessions() as session:
        brand = session.scalar(select(ExtractedField).where(ExtractedField.inspection_id == done_id, ExtractedField.field_name == "brand_owner"))
        assert brand.value == "PARLE PRODUCTS PVT LTD" and brand.source_bboxes


def test_manufacturer_rule_accepts_the_brand_owner_without_new_rule_codes() -> None:
    owner = {"brand_owner": FieldEvidence("Parle Products Pvt Ltd", 0.9, False, "MANUFACTURED FOR: | PARLE PRODUCTS PVT LTD"), "brand_owner_address": FieldEvidence("NORTH LEVEL CROSSING, MUMBAI, MH - 400057", 0.82)}
    findings = {item.rule_id: item for item in ComplianceEngine().evaluate(owner)}
    manufacturer = findings["MANUFACTURER_INFORMATION"]
    assert (manufacturer.status, manufacturer.field, manufacturer.value) == ("PASS", "brand_owner", "Parle Products Pvt Ltd")
    by_rule = lambda fields: {item.rule_id: item.status for item in ComplianceEngine().evaluate(fields)}
    assert by_rule({"brand_owner": owner["brand_owner"]})["MANUFACTURER_INFORMATION"] == "REVIEW"  # named, no address
    assert by_rule({})["MANUFACTURER_INFORMATION"] == "FAIL"  # no party at all
    assert {item.rule_id for item in findings.values()} == {item.rule_id for item in ComplianceEngine().evaluate({})}


def test_absent_supplementary_fields_are_not_persisted_so_risk_inputs_are_unchanged() -> None:
    from backend.app.services.extraction import ExtractionService
    from backend.app.services.extraction.evidence import build_extracted_fields
    from backend.app.services.extraction.extraction_service import CORE_FIELD_NAMES

    ocr = OcrResult(id=uuid4(), inspection_id=uuid4(), raw_text="Biscuits\nNet Wt. 50 g", detections=[], languages=["en"])
    rows = build_extracted_fields(ocr.inspection_id, ocr, ExtractionService().extract(ocr.raw_text))
    names = {row.field_name for row in rows}
    assert set(CORE_FIELD_NAMES) <= names  # core fields keep their empty rows
    # The only supplementary fields with a value: the "absent" declarations.
    assert names - set(CORE_FIELD_NAMES) == {"mrp_declaration", "consumer_care_declaration"}
    assert not any(row.value is None and row.field_name not in CORE_FIELD_NAMES for row in rows)


def test_iso_dates_reach_the_date_rule_in_its_comparable_form() -> None:
    month = ExtractedField(field_name="manufacturing_date", value="MAR 2026", normalized_value="2026-03", source_text="MFD: MAR 2026", review_status="EXTRACTED")
    day = ExtractedField(field_name="expiry_date", value="08/27", normalized_value="2027-08", source_text="EXP 08/27", review_status="EXTRACTED")
    assert field_evidence(month).value == "03/2026" and field_evidence(month).evidence == "MFD: MAR 2026"
    findings = ComplianceEngine().evaluate({"manufacturing_date": field_evidence(month), "expiry_date": field_evidence(day)})
    assert next(item for item in findings if item.rule_id == "DATE_CONSISTENCY").status == "PASS"
    legacy = ExtractedField(field_name="manufacturing_date", value="08/2026", review_status="EXTRACTED")
    assert field_evidence(legacy).value == "08/2026"
