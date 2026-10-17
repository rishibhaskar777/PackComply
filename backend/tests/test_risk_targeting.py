from datetime import datetime, timedelta, timezone
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.api.auth import get_current_user
from backend.app.core.database import get_db
from backend.app.main import app
from backend.app.models import Base, ComplianceResult, ComplianceRule, ExtractedField, Inspection, User
from backend.app.services.risk import RiskService

OFFICIAL = User(id=uuid4(), username="official", email="official@test.invalid", full_name="Official", password_hash="x", role="OFFICIAL", is_active=True)
MANUFACTURER = User(id=uuid4(), username="manufacturer", email="manufacturer@test.invalid", full_name="Manufacturer", password_hash="x", role="MANUFACTURER", is_active=True)

def test_risk_scoring_is_bounded_explainable_and_repeated() -> None:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool); Base.metadata.create_all(engine); Session = sessionmaker(bind=engine)
    with Session() as db:
        rule = ComplianceRule(rule_code="DECLARATION", description="Required declaration.", target_field="declaration", severity="HIGH", enabled=True, configuration={}); db.add(rule); db.flush()
        now = datetime.now(timezone.utc)
        inspections = [Inspection(id=uuid4(), user_id=OFFICIAL.id, inspection_number=f"R-{i}", status="COMPLETED", completed_at=now + timedelta(minutes=i), inspected_at=now) for i in range(3)]
        db.add_all(inspections); db.flush()
        for inspection in inspections:
            db.add_all([ComplianceResult(inspection_id=inspection.id, rule_id=rule.id, result="FAIL", explanation="Declaration failed"), ExtractedField(inspection_id=inspection.id, field_name="declaration", value=None, review_status="REVIEW")])
        db.commit()
        assessment = RiskService(db).for_inspection(inspections[0])
        assert assessment.risk_score == 61 and assessment.risk_level == "HIGH"
        assert {reason.type for reason in assessment.reasons} == {"COMPLIANCE_FAILURE", "MISSING_FIELD", "REPEATED_PATTERN"}
        assert all(reason.points >= 0 for reason in assessment.reasons)
    engine.dispose()

def test_risk_targeting_is_official_only_sorted_filtered_and_paged() -> None:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool); Base.metadata.create_all(engine); Session = sessionmaker(bind=engine)
    with Session() as db:
        high = ComplianceRule(rule_code="HIGH", description="High issue.", target_field="x", severity="HIGH", enabled=True, configuration={}); info = ComplianceRule(rule_code="INFO", description="Info issue.", target_field="y", severity="INFO", enabled=True, configuration={}); db.add_all([high, info]); db.flush()
        now = datetime.now(timezone.utc)
        first = Inspection(id=uuid4(), user_id=OFFICIAL.id, inspection_number="HIGH-1", status="COMPLETED", completed_at=now, inspected_at=now)
        second = Inspection(id=uuid4(), user_id=OFFICIAL.id, inspection_number="LOW-1", status="COMPLETED", completed_at=now - timedelta(days=1), inspected_at=now)
        open_item = Inspection(id=uuid4(), user_id=OFFICIAL.id, inspection_number="OPEN", status="OCR_COMPLETED", inspected_at=now)
        foreign = Inspection(id=uuid4(), user_id=MANUFACTURER.id, inspection_number="FOREIGN", status="COMPLETED", completed_at=now, inspected_at=now)
        db.add_all([first, second, open_item, foreign]); db.flush(); first_id = first.id
        db.add_all([ComplianceResult(inspection_id=first.id, rule_id=high.id, result="FAIL", explanation="High failure"), ComplianceResult(inspection_id=second.id, rule_id=info.id, result="REVIEW", explanation="Review")]); db.commit()
    def override_db():
        with Session() as db: yield db
    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = lambda: OFFICIAL
    try:
        client = TestClient(app); response = client.get("/api/risk-targeting?page_size=1")
        assert response.status_code == 200
        payload = response.json(); assert payload["total"] == 2 and payload["items"][0]["inspection_id"] == str(first_id)
        assert "ocr" not in payload["items"][0] and client.get("/api/risk-targeting?risk_level=LOW").json()["total"] == 1
    finally:
        app.dependency_overrides.clear(); engine.dispose()

def test_manufacturer_cannot_access_risk_targeting() -> None:
    app.dependency_overrides[get_current_user] = lambda: MANUFACTURER
    try:
        assert TestClient(app).get("/api/risk-targeting").status_code == 403
    finally:
        app.dependency_overrides.clear()


def test_photo_key_uses_the_active_back_photo_else_the_earliest_active_photo() -> None:
    from backend.app.models import InspectionImage
    from backend.app.services.risk import photo_keys

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool); Base.metadata.create_all(engine); Session = sessionmaker(bind=engine)
    with Session() as db:
        def make(number: str, photos: list[tuple[str, str]]) -> Inspection:
            inspection = Inspection(id=uuid4(), user_id=OFFICIAL.id, inspection_number=number, status="COMPLETED")
            db.add(inspection); db.flush()
            for panel, checksum in photos:  # upload order
                db.add(InspectionImage(inspection_id=inspection.id, panel=panel, storage_key=f"{number}-{checksum}", original_filename="p.jpg", content_type="image/jpeg", byte_size=1, checksum=checksum))
                db.flush()
            return inspection
        with_back = make("A", [("FRONT", "front"), ("BACK", "back"), ("DATE_STAMP", "date")])  # latest upload is the date stamp
        replaced = make("B", [("BACK", "old"), ("BACK", "new"), ("FRONT", "front")])  # the old back photo was replaced
        no_back = make("C", [("DATE_STAMP", "stamp1"), ("FRONT", "front2"), ("DATE_STAMP", "stamp2")])  # earliest *active*: front2 (stamp1 replaced)
        none = make("D", [])
        db.commit()
        keys = photo_keys(db, [with_back.id, replaced.id, no_back.id, none.id])
        assert keys[with_back.id] == "image:back"
        assert keys[replaced.id] == "image:new"
        assert keys[no_back.id] == "image:front2"
        assert keys[none.id] == f"inspection:{none.id}"
    engine.dispose()
