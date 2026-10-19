from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.api.auth import get_current_user
from backend.app.core.database import get_db
from backend.app.main import app
from backend.app.models import Base, ComplianceResult, ComplianceRule, ExtractedField, Inspection, InspectionImage, OcrResult, User
from backend.app.services.compliance.ruleset import default_ruleset

OFFICIAL = User(id=uuid4(), username="official", email="official@test.invalid", full_name="Official", password_hash="x", role="OFFICIAL", is_active=True)
MANUFACTURER = User(id=uuid4(), username="maker", email="maker@test.invalid", full_name="Maker", password_hash="x", role="MANUFACTURER", is_active=True)
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


def _inspection(db, user, number, results=(), age=timedelta(0), status="COMPLETED", fields=None, checksum=None, processing=timedelta(seconds=20)):
    created = NOW - age
    inspection = Inspection(id=uuid4(), user_id=user.id, inspection_number=number, status=status, inspected_at=created, created_at=created, completed_at=created + processing if status == "COMPLETED" else None)
    db.add(inspection); db.flush()
    image = InspectionImage(inspection_id=inspection.id, storage_key=f"{number}.png", original_filename=f"{number}.png", content_type="image/png", byte_size=10, checksum=checksum or number, created_at=created)
    ocr = OcrResult(inspection_id=inspection.id, raw_text="", detections=[], languages=["en"])
    db.add_all([image, ocr]); db.flush()
    for name, value in (fields or {}).items():
        db.add(ExtractedField(inspection_id=inspection.id, ocr_result_id=ocr.id, field_name=name, value=value, review_status="ACCEPTED"))
    for rule, result in results:
        db.add(ComplianceResult(inspection_id=inspection.id, rule_id=rule.id, result=result, explanation=f"{rule.rule_code} {result}"))
    return inspection


def _rules(db):
    mrp = ComplianceRule(rule_code="MRP_FORMAT", description="Retail sale price.", target_field="mrp", severity="HIGH", enabled=True, configuration={"title": "Retail sale price (MRP)", "legal_reference": "LM(PC) Rules 2011, Rule 6(1)(e)"})
    origin = ComplianceRule(rule_code="COUNTRY_OF_ORIGIN", description="Country of origin.", target_field="country_of_origin", severity="WARNING", enabled=True, configuration={})
    name = ComplianceRule(rule_code="PRODUCT_IDENTITY", description="Generic name.", target_field="product_name", severity="WARNING", enabled=True, configuration={})
    db.add_all([mrp, origin, name]); db.flush()
    return mrp, origin, name


def test_empty_database_returns_zeroes(sessions) -> None:
    payload = client_as(OFFICIAL).get("/api/dashboard/summary").json()
    assert payload == {
        "period": "all", "total_inspections": 0,
        "verdict_counts": {"NON_COMPLIANT": 0, "NEEDS_REVIEW": 0, "COMPLIANT": 0, "PENDING": 0},
        "top_risk": [], "repeat_issues": [], "median_processing_seconds": None,
        "ruleset_version": default_ruleset().version, "rule_counts": [],
    }


def test_official_summary_is_owner_scoped_ranked_and_explained(sessions) -> None:
    with sessions() as db:
        mrp, origin, name = _rules(db)
        worst = _inspection(db, OFFICIAL, "O-1", [(mrp, "FAIL"), (origin, "REVIEW"), (name, "PASS")], fields={"product_name": "Parle-G", "brand_owner": "Parle Products", "manufacturer": "Contract Packer"}, processing=timedelta(seconds=10))
        _inspection(db, OFFICIAL, "O-2", [(mrp, "FAIL"), (name, "PASS")], fields={"product_name": "Marie", "manufacturer": "Britannia"}, processing=timedelta(seconds=30))
        _inspection(db, OFFICIAL, "O-3", [(origin, "REVIEW"), (name, "NOT_APPLICABLE")], processing=timedelta(seconds=50))
        _inspection(db, OFFICIAL, "O-4", [(name, "PASS")], processing=timedelta(seconds=40))
        _inspection(db, OFFICIAL, "O-5", status="OCR_COMPLETED")
        # Another user's labels never appear, however risky.
        _inspection(db, MANUFACTURER, "M-1", [(mrp, "FAIL"), (origin, "FAIL")])
        _inspection(db, MANUFACTURER, "M-2", [(mrp, "FAIL")])
        db.commit(); worst_id = str(worst.id)
    payload = client_as(OFFICIAL).get("/api/dashboard/summary").json()
    assert payload["total_inspections"] == 5
    assert payload["verdict_counts"] == {"NON_COMPLIANT": 2, "NEEDS_REVIEW": 1, "COMPLIANT": 1, "PENDING": 1}
    assert [item["inspection_number"] for item in payload["top_risk"]][:2] == ["O-1", "O-2"]
    assert {item["inspection_number"] for item in payload["top_risk"]} == {"O-1", "O-2", "O-3", "O-4"}
    top = payload["top_risk"][0]
    assert top["id"] == worst_id and top["product_name"] == "Parle-G" and top["maker"] == "Parle Products"
    assert top["verdict"] == "NON_COMPLIANT" and top["risk_score"] > 0 and top["risk_level"] in {"LOW", "MEDIUM", "HIGH", "CRITICAL"}
    assert payload["top_risk"][1]["maker"] == "Britannia"
    scores = [item["risk_score"] for item in payload["top_risk"]]
    assert scores == sorted(scores, reverse=True)
    # Both rules recur on two distinct photos; a violation ranks above a review at equal counts; a PASS is never an issue.
    assert payload["repeat_issues"] == [
        {"rule_id": "MRP_FORMAT", "title": "Retail sale price (MRP)", "legal_reference": "LM(PC) Rules 2011, Rule 6(1)(e)", "label_count": 2},
        {"rule_id": "COUNTRY_OF_ORIGIN", "title": "Country of origin", "legal_reference": None, "label_count": 2},
    ]
    assert payload["median_processing_seconds"] == 35.0


def test_manufacturer_sees_only_own_labels_without_numerical_risk(sessions) -> None:
    with sessions() as db:
        mrp, origin, name = _rules(db)
        _inspection(db, OFFICIAL, "O-1", [(mrp, "FAIL")])
        _inspection(db, MANUFACTURER, "M-ok", [(name, "PASS")], age=timedelta(minutes=1))
        _inspection(db, MANUFACTURER, "M-review", [(origin, "REVIEW")], age=timedelta(minutes=2))
        _inspection(db, MANUFACTURER, "M-bad", [(mrp, "FAIL")], age=timedelta(minutes=3))
        db.commit()
    payload = client_as(MANUFACTURER).get("/api/dashboard/summary").json()
    assert payload["total_inspections"] == 3
    assert payload["verdict_counts"] == {"NON_COMPLIANT": 1, "NEEDS_REVIEW": 1, "COMPLIANT": 1, "PENDING": 0}
    assert [item["inspection_number"] for item in payload["top_risk"]] == ["M-bad", "M-review", "M-ok"]
    assert all(item["risk_score"] is None and item["risk_level"] is None for item in payload["top_risk"])
    assert payload["repeat_issues"] == []


def test_period_filter_limits_every_section(sessions) -> None:
    with sessions() as db:
        mrp, _, name = _rules(db)
        _inspection(db, OFFICIAL, "recent", [(name, "PASS")], age=timedelta(days=2))
        _inspection(db, OFFICIAL, "month-a", [(mrp, "FAIL")], age=timedelta(days=10))
        _inspection(db, OFFICIAL, "month-b", [(mrp, "FAIL")], age=timedelta(days=20))
        _inspection(db, OFFICIAL, "old", [(mrp, "FAIL")], age=timedelta(days=90))
        db.commit()
    client = client_as(OFFICIAL)
    week, month, everything = (client.get(f"/api/dashboard/summary?period={period}").json() for period in ("7d", "30d", "all"))
    assert (week["total_inspections"], month["total_inspections"], everything["total_inspections"]) == (1, 3, 4)
    assert week["verdict_counts"]["COMPLIANT"] == 1 and week["verdict_counts"]["NON_COMPLIANT"] == 0
    assert [item["inspection_number"] for item in week["top_risk"]] == ["recent"]
    assert week["repeat_issues"] == [] and month["repeat_issues"][0]["label_count"] == 2 and everything["repeat_issues"][0]["label_count"] == 3
    assert week["period"] == "7d"


def test_same_photo_counts_once_for_repeat_issues(sessions) -> None:
    with sessions() as db:
        mrp, _, _ = _rules(db)
        _inspection(db, OFFICIAL, "A", [(mrp, "FAIL")], checksum="same-photo")
        _inspection(db, OFFICIAL, "B", [(mrp, "FAIL")], checksum="same-photo")
        db.commit()
    assert client_as(OFFICIAL).get("/api/dashboard/summary").json()["repeat_issues"] == []


def test_summary_requires_authentication_and_a_known_period(sessions) -> None:
    assert client_as(OFFICIAL).get("/api/dashboard/summary?period=1y").status_code == 422
    app.dependency_overrides[get_current_user] = lambda: (_ for _ in ()).throw(HTTPException(status_code=401, detail="Not authenticated."))
    assert TestClient(app).get("/api/dashboard/summary").status_code == 401


def test_rule_counts_rank_rules_by_violations_per_distinct_photo(sessions) -> None:
    with sessions() as db:
        mrp, origin, name = _rules(db)
        _inspection(db, OFFICIAL, "R-1", [(mrp, "FAIL"), (origin, "REVIEW")], checksum="a")
        _inspection(db, OFFICIAL, "R-2", [(mrp, "FAIL"), (name, "PASS")], checksum="b")
        _inspection(db, OFFICIAL, "R-3", [(mrp, "FAIL"), (origin, "FAIL")], checksum="a")  # same photo as R-1: counted once
        _inspection(db, MANUFACTURER, "M-1", [(origin, "FAIL")], checksum="m")
        db.commit()
    counts = client_as(OFFICIAL).get("/api/dashboard/summary").json()["rule_counts"]
    assert [(item["rule_id"], item["violations"], item["review"]) for item in counts] == [("MRP_FORMAT", 2, 0), ("COUNTRY_OF_ORIGIN", 1, 0)]
    assert counts[0]["title"] == "Retail sale price (MRP)" and counts[0]["legal_reference"].endswith("Rule 6(1)(e)")
