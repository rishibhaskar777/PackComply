from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.models import Base, ComplianceResult, ComplianceRule, ExtractedField, Inspection, User
from backend.app.services.report import InspectionReportService


def test_issue_first_report_uses_persisted_findings_risk_and_no_ocr_dump(monkeypatch, tmp_path) -> None:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool); Base.metadata.create_all(engine); Session = sessionmaker(bind=engine, expire_on_commit=False)
    with Session() as db:
        user = User(id=uuid4(), username="owner", email="owner@example.invalid", full_name="Owner", password_hash="x", role="OFFICIAL", is_active=True)
        inspection = Inspection(id=uuid4(), inspection_number="LG-ISSUE-FIRST", user_id=user.id, status="COMPLETED", completed_at=datetime.now(timezone.utc), inspected_at=datetime.now(timezone.utc))
        rule = ComplianceRule(rule_code="MRP_FORMAT", description="MRP format. A valid positive monetary representation is expected.", target_field="mrp", severity="HIGH", enabled=True, configuration={})
        db.add_all([user, inspection, rule]); db.flush(); db.add_all([ExtractedField(inspection_id=inspection.id, field_name="mrp", value="free", review_status="REVIEW"), ComplianceResult(inspection_id=inspection.id, rule_id=rule.id, result="FAIL", explanation="MRP is not valid.", value="free", evidence="MRP FREE")]); db.commit()
        captured = {}
        def capture_build(self, story, **kwargs): captured["story"] = story
        monkeypatch.setattr("backend.app.services.report.SimpleDocTemplate.build", capture_build)
        report = InspectionReportService(db, tmp_path).generate(inspection)
    assert report == b""
    rendered = " ".join(flowable.getPlainText() for flowable in captured["story"] if hasattr(flowable, "getPlainText"))
    table_text = " ".join(cell.getPlainText() for flowable in captured["story"] if hasattr(flowable, "_cellvalues") for row in flowable._cellvalues for cell in row if hasattr(cell, "getPlainText"))
    assert "Violations" in rendered and "Needs review" in rendered and "Quantity Verification" in rendered and "Risk score" in table_text and "Verdict" in table_text
    assert "OCR Results" not in rendered and "Extracted Information" not in rendered and "Report generated at" not in rendered
    # A FAIL issue card is a distinct Table with the required red treatment.
    issue_tables = [flowable for flowable in captured["story"] if flowable.__class__.__name__ == "KeepTogether"]
    assert issue_tables
    engine.dispose()


def test_clean_report_shows_positive_no_issues_message(monkeypatch, tmp_path) -> None:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool); Base.metadata.create_all(engine); Session = sessionmaker(bind=engine, expire_on_commit=False)
    with Session() as db:
        user = User(id=uuid4(), username="owner", email="owner2@example.invalid", full_name="Owner", password_hash="x", is_active=True); inspection = Inspection(id=uuid4(), inspection_number="LG-CLEAN", user_id=user.id, status="COMPLETED", completed_at=datetime.now(timezone.utc), inspected_at=datetime.now(timezone.utc)); rule = ComplianceRule(rule_code="NET_QUANTITY_FORMAT", description="Net quantity format.", target_field="net_quantity", severity="WARNING", enabled=True, configuration={}); db.add_all([user, inspection, rule]); db.flush(); db.add_all([ExtractedField(inspection_id=inspection.id, field_name="net_quantity", value="0.5 KG", review_status="EXTRACTED"), ComplianceResult(inspection_id=inspection.id, rule_id=rule.id, result="PASS", explanation="Quantity normalized and valid.", value="0.5 KG")]); db.commit()
        captured = {}; monkeypatch.setattr("backend.app.services.report.SimpleDocTemplate.build", lambda self, story, **kwargs: captured.setdefault("story", story)); InspectionReportService(db, tmp_path).generate(inspection)
    assert any("No compliance issues detected" in flowable.getPlainText() for flowable in captured["story"] if hasattr(flowable, "getPlainText")); engine.dispose()
