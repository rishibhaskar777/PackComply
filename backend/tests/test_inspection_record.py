"""Inspection record: label identity, inspector corrections, verification, audit trail and the PDF review section."""
import json
from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.api.auth import get_current_user
from backend.app.core.database import get_db
from backend.app.main import app
from backend.app.models import Base, ExtractedField, Inspection, InspectionImage, OcrResult, User
from backend.app.services.corrections import CorrectionError, normalise_value
from backend.app.services.report import InspectionReportService
from backend.scripts import backfill_product_names
from backend.scripts.eval_extraction import FIXTURE_DIR

OFFICIAL = User(id=uuid4(), username="official", email="official@test.invalid", full_name="Asha Official", password_hash="x", role="OFFICIAL", is_active=True)
MANUFACTURER = User(id=uuid4(), username="maker", email="maker@test.invalid", full_name="Maker", password_hash="x", role="MANUFACTURER", is_active=True)


@pytest.fixture
def api():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)

    def override_db():
        with sessions() as session:
            yield session
    app.dependency_overrides[get_db] = override_db
    with sessions() as session:
        session.merge(OFFICIAL)  # module-level instances are detached after the first test
        session.merge(MANUFACTURER)
        session.commit()
    app.dependency_overrides[get_current_user] = lambda: OFFICIAL
    yield TestClient(app), sessions
    app.dependency_overrides.pop(get_db, None)
    engine.dispose()


def as_user(user: User) -> None:
    app.dependency_overrides[get_current_user] = lambda: user


def parle_inspection(client, sessions) -> str:
    """A completed inspection from the stored Parle-G OCR (MRP printed as a heading without a readable price)."""
    inspection = client.post("/api/inspections", json={}).json()
    fixture = json.loads((FIXTURE_DIR / "parle_g_gluco_70g.json").read_text(encoding="utf-8"))
    with sessions() as session:
        session.add(OcrResult(inspection_id=UUID(inspection["id"]), engine="PaddleOCR", languages=fixture["languages"], raw_text=fixture["raw_text"], detections=fixture["detections"], duration_ms=24500))
        session.add(InspectionImage(inspection_id=UUID(inspection["id"]), storage_key="x.png", original_filename="label.png", content_type="image/png", byte_size=10, checksum="ab" * 32, width=1000, height=1400))
        session.commit()
    assert client.post(f"/api/inspections/{inspection['id']}/reanalyse").status_code == 200
    return inspection["id"]


def finding(summary: dict, rule: str) -> dict:
    return next(item for item in summary["compliance"]["findings"] if item["rule_id"] == rule)


def correct(client, inspection_id: str, field: str, value: str, reason: str | None = "Verified on physical pack"):
    return client.post(f"/api/inspections/{inspection_id}/corrections", json={"field_name": field, "corrected_value": value, "reason": reason})


# ------------------------------------------------------------ product name

def test_reanalyse_stores_label_identity_and_history_search_uses_it(api) -> None:
    client, sessions = api
    inspection_id = parle_inspection(client, sessions)
    summary = client.get(f"/api/inspections/{inspection_id}").json()
    assert summary["product_name"] != "Untitled label" and summary["maker_name"] == "CF FOODS PVT LTD" and summary["brand_name"] == "PARLE PRODUCTS PVT LTD"
    item = client.get("/api/inspections").json()["items"][0]
    assert item["product_name"] == summary["product_name"] and item["image_filename"] == "label.png"
    number = item["inspection_number"]
    for term in (summary["product_name"][:5].lower(), "parle products", "cf foods", number.lower()):
        assert client.get("/api/inspections", params={"search": term}).json()["total"] == 1, term
    assert client.get("/api/inspections", params={"search": "nonexistent-xyz"}).json()["total"] == 0


def test_untitled_label_replaces_the_filename_when_nothing_was_extracted(api) -> None:
    client, sessions = api
    inspection = client.post("/api/inspections", json={}).json()
    with sessions() as session:
        session.add(InspectionImage(inspection_id=UUID(inspection["id"]), storage_key="y.png", original_filename="IMG_0001.png", content_type="image/png", byte_size=1))
        session.commit()
    assert client.get("/api/inspections").json()["items"][0]["product_name"] == "Untitled label"
    assert client.get(f"/api/inspections/{inspection['id']}").json()["product_name"] == "Untitled label"


def test_backfill_fills_identity_from_extracted_fields_and_is_idempotent(api) -> None:
    client, sessions = api
    inspection_id = parle_inspection(client, sessions)
    with sessions() as session:
        row = session.get(Inspection, UUID(inspection_id))
        expected = (row.product_name, row.brand_name, row.maker_name)
        row.product_name = row.brand_name = row.maker_name = None
        session.commit()
        first = backfill_product_names.backfill(session)
        assert first["updated"] == 1
        row = session.get(Inspection, UUID(inspection_id))
        assert (row.product_name, row.brand_name, row.maker_name) == expected
        second = backfill_product_names.backfill(session)
        assert second["updated"] == 0 and second["unchanged"] == 1


# ------------------------------------------------------------- validation

@pytest.mark.parametrize("field,raw,value", [
    ("mrp", "10", "₹10.00"), ("mrp", "₹10.00", "₹10.00"), ("mrp", "Rs. 10/-", "₹10.00"), ("mrp", "1,250", "₹1250.00"),
    ("net_quantity", "70 g", "70 g"), ("net_quantity", "70g", "70 g"), ("net_quantity", "1 kg", "1 kg"),
    ("manufacturing_date", "08/2026", "08/2026"), ("expiry_date", "15/08/2027", "15/08/2027"),
    ("fssai_license", "1000 0000 000000", "10000000000000"), ("consumer_care_email", "care@example.com", "care@example.com"),
])
def test_valid_values_are_normalised_with_the_extractor_parsers(field, raw, value) -> None:
    assert normalise_value(field, raw)[0] == value


@pytest.mark.parametrize("field,raw", [
    ("mrp", "free"), ("mrp", "0"), ("mrp", "-5"), ("net_quantity", "seventy"), ("net_quantity", "70"), ("manufacturing_date", "13/2026"),
    ("expiry_date", "tomorrow"), ("fssai_license", "123"), ("consumer_care_email", "nope"), ("mrp", ""), ("declaration_status", "{}"), ("not_a_field", "x"),
])
def test_invalid_values_are_rejected(field, raw) -> None:
    with pytest.raises(CorrectionError):
        normalise_value(field, raw)


def test_api_rejects_invalid_values_with_a_clear_422(api) -> None:
    client, sessions = api
    inspection_id = parle_inspection(client, sessions)
    response = correct(client, inspection_id, "mrp", "free")
    assert response.status_code == 422 and "rupees" in response.json()["detail"]
    assert correct(client, inspection_id, "bogus", "1").status_code == 422
    assert client.get(f"/api/inspections/{inspection_id}").json()["corrections"] == []


# ------------------------------------------------------- roles + re-evaluation

def test_only_officials_can_correct_undo_or_verify(api) -> None:
    client, sessions = api
    inspection_id = parle_inspection(client, sessions)
    correction = correct(client, inspection_id, "mrp", "10").json()["corrections"][0]
    as_user(MANUFACTURER)
    # Ownership is checked after the role, so a manufacturer is told 403 even on a record that is not theirs.
    assert correct(client, inspection_id, "mrp", "12").status_code == 403
    assert client.delete(f"/api/inspections/{inspection_id}/corrections/{correction['id']}").status_code == 403
    assert client.post(f"/api/inspections/{inspection_id}/verify", json={"remarks": "ok"}).status_code == 403
    as_user(OFFICIAL)
    assert client.get(f"/api/inspections/{inspection_id}").json()["corrections"][0]["id"] == correction["id"]


def test_entering_the_mrp_turns_a_review_into_a_pass_and_makes_the_unit_sale_price_assessable(api) -> None:
    client, sessions = api
    inspection_id = parle_inspection(client, sessions)
    before = client.get(f"/api/inspections/{inspection_id}").json()
    assert finding(before, "MRP_FORMAT")["status"] == "REVIEW" and before["extraction"]["fields"]["mrp"]["value"] is None

    response = correct(client, inspection_id, "mrp", "10")

    assert response.status_code == 200
    after = response.json()
    mrp = finding(after, "MRP_FORMAT")
    assert mrp["status"] == "PASS" and mrp["corrected"] is True and mrp["fields"] == ["mrp"]
    field = after["extraction"]["fields"]["mrp"]
    assert field["value"] == "₹10.00" and field["confidence"] == 1.0 and field["needs_review"] is False and field["source"] == "INSPECTOR"
    assert after["compliance"]["summary"]["review"] == before["compliance"]["summary"]["review"] - 1
    # With a price the unit-sale-price rule can finally be assessed, and nothing on the pack declares one.
    assert finding(before, "UNIT_SALE_PRICE")["status"] == "NOT_APPLICABLE" and finding(after, "UNIT_SALE_PRICE")["status"] == "FAIL"
    assert after["compliance"]["verdict"] == "NON_COMPLIANT" and after["risk"]["risk_score"] > before["risk"]["risk_score"]
    correction = after["corrections"][0]
    assert (correction["field_name"], correction["original_value"], correction["corrected_value"], correction["reason"], correction["corrected_by_name"]) == ("mrp", None, "₹10.00", "Verified on physical pack", "Asha Official")


def test_correcting_every_open_item_reaches_a_compliant_verdict_and_zero_risk(api) -> None:
    client, sessions = api
    inspection_id = parle_inspection(client, sessions)
    assert client.get(f"/api/inspections/{inspection_id}").json()["compliance"]["verdict"] == "NEEDS_REVIEW"
    for field, value in (("mrp", "10"), ("unit_sale_price", "₹0.14 per g"), ("manufacturing_date", "08/2026"), ("consumer_care_phone", "1800 123 4567")):
        summary = correct(client, inspection_id, field, value).json()
    assert summary["compliance"]["verdict"] == "COMPLIANT" and summary["risk"]["risk_score"] == 0
    assert [item["field_name"] for item in summary["corrections"]] == ["mrp", "unit_sale_price", "manufacturing_date", "consumer_care_phone"]
    assert client.get("/api/inspections").json()["items"][0]["verdict"] == "COMPLIANT"


def test_undo_restores_the_ocr_value_and_the_original_findings(api) -> None:
    client, sessions = api
    inspection_id = parle_inspection(client, sessions)
    before = client.get(f"/api/inspections/{inspection_id}").json()
    correction = correct(client, inspection_id, "mrp", "10").json()["corrections"][0]

    undone = client.delete(f"/api/inspections/{inspection_id}/corrections/{correction['id']}")

    assert undone.status_code == 200
    summary = undone.json()
    assert summary["corrections"] == [] and summary["extraction"]["fields"]["mrp"]["source"] == "OCR"
    assert finding(summary, "MRP_FORMAT")["status"] == "REVIEW"
    assert summary["compliance"]["summary"] == before["compliance"]["summary"] and summary["risk"]["risk_score"] == before["risk"]["risk_score"]
    assert client.delete(f"/api/inspections/{inspection_id}/corrections/{correction['id']}").status_code == 404


def test_corrections_survive_a_later_reanalyse_and_the_second_wins(api) -> None:
    client, sessions = api
    inspection_id = parle_inspection(client, sessions)
    correct(client, inspection_id, "mrp", "10")
    correct(client, inspection_id, "mrp", "12", reason="OCR misread")
    summary = client.post(f"/api/inspections/{inspection_id}/reanalyse").json()
    assert summary["extraction"]["fields"]["mrp"]["value"] == "₹12.00" and summary["extraction"]["fields"]["mrp"]["source"] == "INSPECTOR"
    assert finding(summary, "MRP_FORMAT")["status"] == "PASS"
    assert [item["corrected_value"] for item in summary["corrections"]] == ["₹10.00", "₹12.00"] and summary["corrections"][1]["original_value"] is None
    with sessions() as session:
        names = [row.field_name for row in session.scalars(select(ExtractedField).where(ExtractedField.inspection_id == UUID(inspection_id)))]
    assert names.count("mrp") == 1


def test_correcting_a_field_overwrites_an_ocr_read_value_and_keeps_its_box(api) -> None:
    client, sessions = api
    inspection_id = parle_inspection(client, sessions)
    original = client.get(f"/api/inspections/{inspection_id}").json()["extraction"]["fields"]["net_quantity"]
    assert original["value"] == "70 g" and original["source_bboxes"]
    corrected = correct(client, inspection_id, "net_quantity", "75 g", reason="OCR misread").json()
    field = corrected["extraction"]["fields"]["net_quantity"]
    assert field["value"] == "75 g" and field["source_bboxes"] == original["source_bboxes"] and corrected["corrections"][0]["original_value"] == "70 g"


# ------------------------------------------------------------ verification + audit

def test_verify_requires_completed_records_who_and_when_and_resets_on_a_new_correction(api) -> None:
    client, sessions = api
    pending = client.post("/api/inspections", json={}).json()
    assert client.post(f"/api/inspections/{pending['id']}/verify", json={"remarks": "x"}).status_code == 409

    inspection_id = parle_inspection(client, sessions)
    assert client.get(f"/api/inspections/{inspection_id}").json()["review"]["status"] == "UNREVIEWED"
    verified = client.post(f"/api/inspections/{inspection_id}/verify", json={"remarks": "  Checked against the pack.  "}).json()["review"]
    assert verified["status"] == "VERIFIED" and verified["by"] == "Asha Official" and verified["remarks"] == "Checked against the pack." and verified["at"]
    assert client.get("/api/inspections").json()["items"][0]["review_status"] == "VERIFIED"

    reset = correct(client, inspection_id, "mrp", "10").json()["review"]
    assert reset["status"] == "UNREVIEWED" and reset["by"] is None and reset["at"] is None

    again = client.post(f"/api/inspections/{inspection_id}/verify", json={"remarks": "Re-checked after correction."}).json()["review"]
    assert again["status"] == "VERIFIED" and again["remarks"] == "Re-checked after correction."


def test_audit_trail_is_chronological_and_complete(api) -> None:
    client, sessions = api
    inspection_id = parle_inspection(client, sessions)
    correction = correct(client, inspection_id, "mrp", "10").json()["corrections"][0]
    client.post(f"/api/inspections/{inspection_id}/verify", json={"remarks": "ok"})
    client.delete(f"/api/inspections/{inspection_id}/corrections/{correction['id']}")
    client.post(f"/api/inspections/{inspection_id}/reanalyse")

    events = client.get(f"/api/inspections/{inspection_id}/audit").json()

    kinds = [event["type"] for event in events]
    assert kinds[:4] == ["CREATED", "PHOTO_UPLOADED", "OCR_COMPLETED", "COMPLIANCE_EVALUATED"]
    assert kinds.count("CORRECTION") == 0 and {"VERIFIED", "CORRECTION_UNDONE", "REANALYSED"} <= set(kinds)
    assert [event["at"] for event in events] == sorted(event["at"] for event in events)
    # Setting the record up re-analysed it once; the second re-analysis comes last.
    assert kinds.count("REANALYSED") == 2 and kinds.index("VERIFIED") < kinds.index("CORRECTION_UNDONE") < len(kinds) - 1 - kinds[::-1].index("REANALYSED")
    by_type = {event["type"]: event for event in events}
    assert by_type["PHOTO_UPLOADED"]["details"]["checksum"] == "ab" * 32
    assert by_type["OCR_COMPLETED"]["details"]["duration_ms"] == 24500
    assert by_type["COMPLIANCE_EVALUATED"]["details"]["ruleset_version"]
    assert by_type["VERIFIED"]["actor"] == "Asha Official" and by_type["CORRECTION_UNDONE"]["details"]["corrected"] == "₹10.00"


def test_audit_lists_each_active_correction_in_order(api) -> None:
    client, sessions = api
    inspection_id = parle_inspection(client, sessions)
    correct(client, inspection_id, "mrp", "10")
    correct(client, inspection_id, "net_quantity", "75 g", reason="OCR misread")
    events = client.get(f"/api/inspections/{inspection_id}/audit").json()
    corrections = [event for event in events if event["type"] == "CORRECTION"]
    assert [event["details"]["field"] for event in corrections] == ["mrp", "net_quantity"]
    assert corrections[1]["details"] == {"field": "net_quantity", "original": "70 g", "corrected": "75 g", "reason": "OCR misread"}
    assert events.index(corrections[0]) > [event["type"] for event in events].index("COMPLIANCE_EVALUATED")


def test_other_users_cannot_read_or_change_the_record(api) -> None:
    client, sessions = api
    inspection_id = parle_inspection(client, sessions)
    as_user(User(id=uuid4(), username="other", email="o@test.invalid", full_name="Other", password_hash="x", role="OFFICIAL", is_active=True))
    assert client.get(f"/api/inspections/{inspection_id}/audit").status_code == 404
    assert correct(client, inspection_id, "mrp", "10").status_code == 404
    assert client.post(f"/api/inspections/{inspection_id}/verify", json={}).status_code == 404


def test_manufacturer_summary_has_no_risk_but_shows_the_review(api) -> None:
    client, sessions = api
    as_user(MANUFACTURER)
    inspection_id = parle_inspection(client, sessions)
    summary = client.get(f"/api/inspections/{inspection_id}").json()
    assert summary["risk"] is None and summary["review"]["status"] == "UNREVIEWED" and summary["image"]["width"] == 1000


# ------------------------------------------------------------------- PDF

def test_pdf_contains_the_inspector_review_section(api, monkeypatch, tmp_path) -> None:
    client, sessions = api
    inspection_id = parle_inspection(client, sessions)
    correct(client, inspection_id, "mrp", "10", reason="OCR misread")
    client.post(f"/api/inspections/{inspection_id}/verify", json={"remarks": "Matches the physical pack."})
    captured = {}
    monkeypatch.setattr("backend.app.services.report.SimpleDocTemplate.build", lambda self, story, **kwargs: captured.setdefault("story", story))
    with sessions() as session:
        InspectionReportService(session, tmp_path).generate(session.get(Inspection, UUID(inspection_id)))
    texts = [flowable.getPlainText() for flowable in captured["story"] if hasattr(flowable, "getPlainText")]
    cells = [cell.getPlainText() for flowable in captured["story"] if hasattr(flowable, "_cellvalues") for row in flowable._cellvalues for cell in row if hasattr(cell, "getPlainText")]
    text = " ".join(texts + cells)
    assert "Inspector review" in text and "Inspector corrections" in text
    assert "Verified by an inspector" in text and "Asha Official" in text and "Matches the physical pack." in text
    assert "not read → ₹10.00" in text.replace("  ", " ") and "OCR misread" in text
    assert "Inspector-corrected value" in text  # the passed MRP finding is marked


def test_pdf_generates_for_real_with_a_correction(api, tmp_path) -> None:
    client, sessions = api
    inspection_id = parle_inspection(client, sessions)
    correct(client, inspection_id, "mrp", "10")
    response = client.get(f"/api/inspections/{inspection_id}/report")
    assert response.status_code == 200 and response.content.startswith(b"%PDF")


def test_upload_config_states_the_backend_limit(api) -> None:
    client, _ = api
    body = client.get("/api/inspections/config").json()
    assert body["max_upload_bytes"] > 0 and "image/png" in body["allowed_types"]
