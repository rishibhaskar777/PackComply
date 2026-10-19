"""Manufacturer portal API: remediation catalogue, products, fix list, linked re-checks and the self-check PDF."""
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.api.auth import get_current_user
from backend.app.core.database import get_db
from backend.app.main import app
from backend.app.models import Base, ComplianceResult, ExtractedField, Inspection, InspectionImage, OcrResult, User
from backend.app.services.compliance.registry import sync_compliance_rules
from backend.app.services.compliance.remediation import load_remediation, missing_remediation, rule_chip
from backend.app.services.compliance.ruleset import default_ruleset
from backend.app.services.products import normalise_product_key

MAKER = User(id=uuid4(), username="maker", email="maker@test.invalid", full_name="Sample Foods", organization="Sample Foods Pvt Ltd", password_hash="x", role="MANUFACTURER", is_active=True)
OTHER_MAKER = User(id=uuid4(), username="other", email="other@test.invalid", full_name="Other", password_hash="x", role="MANUFACTURER", is_active=True)
OFFICIAL = User(id=uuid4(), username="official", email="official@test.invalid", full_name="Official", password_hash="x", role="OFFICIAL", is_active=True)
NOW = datetime.now(timezone.utc)


@pytest.fixture
def sessions():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)

    def override_db():
        with factory() as db:
            yield db
    app.dependency_overrides[get_db] = override_db
    yield factory
    app.dependency_overrides.clear()
    engine.dispose()


def client_as(user: User) -> TestClient:
    app.dependency_overrides[get_current_user] = lambda: user
    return TestClient(app)


def add_check(db, user, name, outcomes, *, age=timedelta(0), previous=None, key=None, number=None, quantity="70 g"):
    """A completed self-check whose rules carry `outcomes` ({rule_code: result}); the other rules pass."""
    rules = sync_compliance_rules(db)
    created = NOW - age
    number = number or f"SC-{uuid4().hex[:8]}"
    inspection = Inspection(id=uuid4(), user_id=user.id, inspection_number=number, status="COMPLETED", inspected_at=created, created_at=created, completed_at=created + timedelta(seconds=30),
                            product_name=name, previous_inspection_id=previous.id if previous else None, product_key=key)
    db.add(inspection); db.flush()
    db.add(InspectionImage(inspection_id=inspection.id, storage_key=f"{number}.png", original_filename=f"{number}.png", content_type="image/png", byte_size=10, checksum=number, created_at=created))
    ocr = OcrResult(inspection_id=inspection.id, raw_text="", detections=[], languages=["en"])
    db.add(ocr); db.flush()
    db.add(ExtractedField(inspection_id=inspection.id, ocr_result_id=ocr.id, field_name="net_quantity", value=quantity, review_status="ACCEPTED"))
    for code, rule in rules.items():
        db.add(ComplianceResult(inspection_id=inspection.id, rule_id=rule.id, result=outcomes.get(code, "PASS"), explanation=f"{code} {outcomes.get(code, 'PASS')}", ruleset_id="LM_PC_2011", ruleset_version="2"))
    db.commit()
    return inspection


# ------------------------------------------------------------------ remediation catalogue

def test_every_rule_of_the_active_ruleset_has_a_complete_remediation_entry() -> None:
    assert missing_remediation() == []
    catalogue = load_remediation()
    for rule in default_ruleset().rules:
        entry = catalogue[rule.code]
        assert entry.problem_title and entry.example and entry.pass_text and entry.fix_steps, rule.code
        assert all(step.endswith(".") for step in entry.fix_steps), rule.code


def test_catalogue_examples_match_the_approved_wording() -> None:
    catalogue = load_remediation()
    assert catalogue["CONSUMER_CARE_FORMAT"].example == "Consumer Care, Sample Foods Pvt Ltd, Mumbai 400057 · 1800-123-4567 · care@samplefoods.in"
    assert catalogue["MRP_FORMAT"].example == "MRP ₹49.00 (incl. of all taxes)"
    assert "₹0.14 per g" in catalogue["UNIT_SALE_PRICE"].example and "₹1.49 per ml" in catalogue["UNIT_SALE_PRICE"].example
    assert catalogue["MANUFACTURE_DATE"].example == "Mfd: 10/2026"
    assert catalogue["NET_QUANTITY_FORMAT"].example == "Net Quantity: 70 g"


def test_rule_chip_is_the_short_citation() -> None:
    assert rule_chip("LM(PC) Rules 2011, Rule 6(2)") == "Rule 6(2)"
    assert rule_chip("LM(PC) Rules 2011, Rule 6(1)(c) and Rule 9") == "Rule 6(1)(c)"
    assert rule_chip("FSS (Labelling and Display) Regulations 2020 — cross-check, not LM(PC)") is None
    assert rule_chip(None) is None


def test_compliance_findings_carry_remediation_for_both_roles(sessions) -> None:
    with sessions() as db:
        check = add_check(db, MAKER, "Gluco Biscuits", {"CONSUMER_CARE_FORMAT": "FAIL"})
        official_check = add_check(db, OFFICIAL, "Gluco Biscuits", {"CONSUMER_CARE_FORMAT": "FAIL"})
        ids = [(MAKER, check.id), (OFFICIAL, official_check.id)]
    for user, inspection_id in ids:
        findings = client_as(user).get(f"/api/inspections/{inspection_id}/compliance").json()["findings"]
        care = next(item for item in findings if item["rule_id"] == "CONSUMER_CARE_FORMAT")
        assert care["fix_title"] == "Consumer care contact is incomplete"
        assert care["rule_chip"] == "Rule 6(2)"
        assert care["example"].startswith("Consumer Care, Sample Foods")
        assert care["fix_steps"] and care["pass_text"]
    assert all(item["fix_title"] and item["pass_text"] for item in findings)


# ------------------------------------------------------------------ products

def test_products_group_by_name_keep_history_and_show_improvement(sessions) -> None:
    with sessions() as db:
        first = add_check(db, MAKER, "Gluco Biscuits", {"CONSUMER_CARE_FORMAT": "FAIL", "MRP_TAX_INCLUSIVE": "FAIL", "UNIT_SALE_PRICE": "FAIL"}, age=timedelta(days=3))
        add_check(db, MAKER, "GLUCO  biscuits", {}, age=timedelta(days=1), previous=first)
        add_check(db, MAKER, "Marie Gold", {"MRP_FORMAT": "FAIL", "NET_QUANTITY_FORMAT": "FAIL"}, age=timedelta(days=2))
        add_check(db, MAKER, "Rusk", {"MANUFACTURE_DATE": "REVIEW"}, age=timedelta(days=4))
        add_check(db, MAKER, "Cream Cracker", {}, age=timedelta(days=5))
    body = client_as(MAKER).get("/api/products").json()
    assert (body["total"], body["ready"], body["fix"], body["check"]) == (4, 2, 1, 1)
    assert body["readiness_percent"] == 50
    assert (body["open_fixes"], body["labels_to_fix"]) == (2, 1)
    gluco = next(card for card in body["products"] if card["product_name"].lower().startswith("gluco"))
    assert gluco["status"] == "READY" and gluco["checks"] == 2
    assert gluco["previous_fixes"] == 3 and gluco["fixes"] == 0 and gluco["improved"] is True
    assert [item["fixes"] for item in gluco["history"]] == [0, 3]
    marie = next(card for card in body["products"] if card["product_name"] == "Marie Gold")
    assert (marie["status"], marie["fixes"], marie["net_quantity"]) == ("FIX", 2, "70 g")
    rusk = next(card for card in body["products"] if card["product_name"] == "Rusk")
    assert (rusk["status"], rusk["fixes"], rusk["review"]) == ("CHECK", 0, 1)


def test_a_corrected_label_with_a_different_name_stays_on_the_same_product(sessions) -> None:
    with sessions() as db:
        first = add_check(db, MAKER, "Gluco Biscuits", {"MRP_FORMAT": "FAIL"}, age=timedelta(days=2))
        add_check(db, MAKER, "Parle-G Gluco Biscuits", {}, age=timedelta(days=1), previous=first, key=normalise_product_key("Gluco Biscuits"))
    body = client_as(MAKER).get("/api/products").json()
    assert body["total"] == 1 and body["products"][0]["checks"] == 2 and body["products"][0]["improved"] is True


def test_products_are_owner_scoped_and_manufacturer_only(sessions) -> None:
    with sessions() as db:
        add_check(db, MAKER, "Mine", {})
        add_check(db, OTHER_MAKER, "Theirs", {"MRP_FORMAT": "FAIL"})
        add_check(db, OFFICIAL, "Official's", {})
    names = [card["product_name"] for card in client_as(MAKER).get("/api/products").json()["products"]]
    assert names == ["Mine"]
    assert client_as(OFFICIAL).get("/api/products").status_code == 403
    assert client_as(OFFICIAL).get("/api/fixes").status_code == 403


def test_a_new_manufacturer_has_an_empty_product_list(sessions) -> None:
    body = client_as(MAKER).get("/api/products").json()
    assert body["total"] == 0 and body["readiness_percent"] == 0 and body["products"] == []
    assert client_as(MAKER).get("/api/fixes").json() == {"total_items": 0, "groups": []}


# ------------------------------------------------------------------ fixes

def test_fixes_group_by_rule_across_the_latest_checks_only(sessions) -> None:
    with sessions() as db:
        old = add_check(db, MAKER, "Cookies", {"CONSUMER_CARE_FORMAT": "FAIL"}, age=timedelta(days=5))
        add_check(db, MAKER, "Cookies", {}, age=timedelta(days=1), previous=old)  # fixed: no longer open
        for name in ("Rusk", "Toast", "Wafers", "Namkeen"):
            add_check(db, MAKER, name, {"CONSUMER_CARE_FORMAT": "FAIL"}, age=timedelta(days=2))
        add_check(db, MAKER, "Toast", {"CONSUMER_CARE_FORMAT": "FAIL", "MANUFACTURE_DATE": "REVIEW"}, age=timedelta(hours=1))
        add_check(db, OTHER_MAKER, "Theirs", {"CONSUMER_CARE_FORMAT": "FAIL"})
    body = client_as(MAKER).get("/api/fixes").json()
    care = body["groups"][0]
    assert care["rule_id"] == "CONSUMER_CARE_FORMAT" and care["status"] == "FAIL"
    assert care["product_count"] == 4 and care["title"] == "Consumer care contact is incomplete"
    assert care["rule_chip"] == "Rule 6(2)" and care["example"] and care["fix_steps"]
    assert sorted(item["product_name"] for item in care["products"]) == ["Namkeen", "Rusk", "Toast", "Wafers"]
    assert body["total_items"] == 4
    assert body["groups"][-1]["status"] == "REVIEW"


# ------------------------------------------------------------------ linked re-check

def test_create_inspection_continues_the_product_of_an_owned_check(sessions) -> None:
    with sessions() as db:
        first = add_check(db, MAKER, "Gluco Biscuits", {"MRP_FORMAT": "FAIL"})
        foreign = add_check(db, OTHER_MAKER, "Theirs", {})
        first_id, foreign_id = str(first.id), str(foreign.id)
    created = client_as(MAKER).post("/api/inspections", json={"previous_inspection_id": first_id})
    assert created.status_code == 201
    with sessions() as db:
        row = db.get(Inspection, UUID(created.json()["id"]))
        assert str(row.previous_inspection_id) == first_id and row.product_key == "gluco biscuits"
    assert client_as(MAKER).post("/api/inspections", json={"previous_inspection_id": foreign_id}).status_code == 404
    summary_id = created.json()["id"]
    assert client_as(MAKER).get(f"/api/inspections/{summary_id}").json()["previous_inspection_id"] == first_id


# ------------------------------------------------------------------ self-check PDF

def flowable_text(flowables) -> str:
    """All plain text of a story, descending into tables and KeepTogether blocks."""
    parts = []
    for item in flowables:
        if hasattr(item, "getPlainText"):
            parts.append(item.getPlainText())
        elif hasattr(item, "_cellvalues"):
            parts.extend(flowable_text(cell if isinstance(cell, list) else [cell]) for row in item._cellvalues for cell in row)
        elif hasattr(item, "_content"):
            parts.append(flowable_text(item._content))
    return " ".join(parts)


def test_self_check_pdf_for_the_owner_only(sessions) -> None:
    with sessions() as db:
        check = add_check(db, MAKER, "Gluco Biscuits", {"CONSUMER_CARE_FORMAT": "FAIL"})
        other = add_check(db, OTHER_MAKER, "Theirs", {})
        check_id, other_id = str(check.id), str(other.id)
    response = client_as(MAKER).get(f"/api/inspections/{check_id}/self-check-report")
    assert response.status_code == 200 and response.headers["content-type"] == "application/pdf"
    assert response.content.startswith(b"%PDF") and "self_check_" in response.headers["content-disposition"]
    assert client_as(MAKER).get(f"/api/inspections/{other_id}/self-check-report").status_code == 404
    assert client_as(OFFICIAL).get(f"/api/inspections/{check_id}/self-check-report").status_code == 403
    # A manufacturer asking for the generic report still gets the self-check, never an official report.
    generic = client_as(MAKER).get(f"/api/inspections/{check_id}/report")
    assert generic.status_code == 200 and "self_check_" in generic.headers["content-disposition"]


def test_self_check_pdf_has_the_verdict_fix_list_and_disclaimer(sessions, monkeypatch, tmp_path) -> None:
    from backend.app.services.self_check_report import SELF_CHECK_DISCLAIMER, SelfCheckReportService

    captured = {}
    monkeypatch.setattr("backend.app.services.self_check_report.SimpleDocTemplate.build", lambda self, story, **kwargs: captured.setdefault("story", story))
    with sessions() as db:
        failing = add_check(db, MAKER, "Gluco Biscuits", {"CONSUMER_CARE_FORMAT": "FAIL", "MRP_FORMAT": "FAIL"})
        SelfCheckReportService(db, tmp_path, MAKER).generate(failing)
        text = flowable_text(captured.pop("story"))
        assert "Fix 2 items before printing" in text and "Sample Foods Pvt Ltd" in text and "Gluco Biscuits" in text
        assert "Consumer care contact is incomplete" in text and "How to fix" in text and "Example:" in text and "care@samplefoods.in" in text
        assert "Already correct (12)" in text and SELF_CHECK_DISCLAIMER in text
        assert "Official" not in text and "Violations" not in text
        ready = add_check(db, MAKER, "Cream Cracker", {})
        SelfCheckReportService(db, tmp_path, MAKER).generate(ready)
        assert "Ready to print" in flowable_text(captured["story"]) and "Fix list" not in flowable_text(captured["story"])


def test_rules_guide_endpoint_carries_the_plain_language_catalogue(sessions) -> None:
    rules = client_as(MAKER).get("/api/compliance/rules").json()["rules"]
    assert len(rules) == len(default_ruleset().enabled_rules)
    for rule in rules:
        assert rule["fix_title"] and rule["example"] and rule["pass_text"] and rule["fix_steps"] and rule["expected"], rule["code"]
    care = next(rule for rule in rules if rule["code"] == "CONSUMER_CARE_FORMAT")
    assert care["rule_chip"] == "Rule 6(2)" and care["legal_reference"].endswith("Rule 6(2)")
