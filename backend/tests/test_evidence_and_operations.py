"""Evidence storage, image access, history status, readiness and Indian-script PDFs."""
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
from PIL import Image
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.api.auth import get_current_user
from backend.app.api.inspections import get_ocr_service
from backend.app.core.config import get_settings
from backend.app.core.database import get_db
from backend.app.main import app
from backend.app.models import Base, ComplianceResult, ComplianceRule, ExtractedField, Inspection, InspectionImage, OcrResult, User
from backend.app.services.compliance import ensure_compliance_rules
from backend.app.services.compliance.ruleset import default_ruleset
from backend.app.services.extraction.evidence import field_evidence, source_bboxes
from backend.app.services.ocr import OcrDetection, PaddleOcrService
from backend.app.services.report import InspectionReportService
from backend.app.services.risk import RiskService
from backend.app.services.uploads import image_dimensions
from backend.app.services.workflow import latest_image
from backend.scripts import wait_for_db

QUANTITY_BOX = [[10.0, 40.0], [200.0, 40.0], [200.0, 60.0], [10.0, 60.0]]
DETECTIONS = [
    {"text": "Biscuits", "confidence": 0.99, "bbox": [[10.0, 0.0], [90.0, 0.0], [90.0, 15.0], [10.0, 15.0]]},
    {"text": "Manufactured by: ABC Foods", "confidence": 0.97, "bbox": [[10.0, 20.0], [250.0, 20.0], [250.0, 35.0], [10.0, 35.0]]},
    {"text": "Net Qty: 500g", "confidence": 0.95, "bbox": QUANTITY_BOX},
]


def png(width: int = 4, height: int = 4) -> bytes:
    output = BytesIO(); Image.new("RGB", (width, height), "white").save(output, "PNG"); return output.getvalue()


class DetectingOcr:
    def scan(self, _path):
        return SimpleNamespace(engine="PaddleOCR", languages=["en"], raw_text="\n".join(item["text"] for item in DETECTIONS), as_database_detections=lambda: DETECTIONS)


@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path / "uploads")); get_settings.cache_clear()
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool); Base.metadata.create_all(engine); sessions = sessionmaker(bind=engine)
    def override_db():
        with sessions() as session: yield session
    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_ocr_service] = lambda: DetectingOcr()
    try:
        yield TestClient(app), sessions, tmp_path / "uploads"
    finally:
        app.dependency_overrides.pop(get_db, None); app.dependency_overrides.pop(get_ocr_service, None); get_settings.cache_clear(); engine.dispose()


def processed_inspection(client: TestClient, image: bytes | None = None) -> dict:
    inspection = client.post("/api/inspections", json={}).json()
    assert client.post(f"/api/inspections/{inspection['id']}/image", files={"image": ("label.png", image or png(), "image/png")}).status_code == 201
    response = client.post(f"/api/inspections/{inspection['id']}/process"); assert response.status_code == 200
    return response.json()


# 0. Repeat patterns count distinct photos -----------------------------------

def test_uploading_the_same_image_twice_does_not_create_a_repeat_pattern(api) -> None:
    client, sessions, _ = api
    first, second = processed_inspection(client, png()), processed_inspection(client, png())
    issues = {finding["rule_id"] for finding in first["compliance"]["findings"] if finding["status"] in ("FAIL", "REVIEW")}
    assert issues and first["compliance"]["findings"] == second["compliance"]["findings"]
    # Same photo, two inspections: one occurrence, so no pattern and no repeat risk.
    assert client.get("/api/patterns").json() == []
    with sessions() as session:
        inspection = session.get(Inspection, UUID(second["id"]))
        assert not any(reason.type == "REPEATED_PATTERN" for reason in RiskService(session).for_inspection(inspection).reasons)
    # A genuinely different photo with the same issues is a second occurrence.
    processed_inspection(client, png(5, 5))
    patterns = {pattern["pattern_key"]: pattern for pattern in client.get("/api/patterns").json()}
    assert issues <= set(patterns)
    assert all(patterns[code]["occurrence_count"] == 2 and len(patterns[code]["inspection_ids"]) == 3 for code in issues)


# 1. Evidence storage --------------------------------------------------------

def test_source_bboxes_match_detections_contained_in_the_source_line() -> None:
    detections = [{"text": " net qty: ", "bbox": [[1, 1]]}, {"text": "500G", "bbox": [[2, 2]]}, {"text": "g", "bbox": [[3, 3]]}, {"text": "MRP", "bbox": [[4, 4]]}, {"text": "500g", "bbox": None}]
    assert source_bboxes("Net  Qty: 500g", detections) == [[[1, 1]], [[2, 2]]]
    assert source_bboxes(None, detections) is None and source_bboxes("MRP Rs 10", []) is None


def test_workflow_persists_and_returns_extraction_evidence(api) -> None:
    client, sessions, _ = api
    payload = processed_inspection(client)
    quantity = payload["extraction"]["fields"]["net_quantity"]
    assert quantity["source_text"] == "Net Qty: 500g" and quantity["matched_pattern"] and quantity["normalized_value"] == "500 g"
    assert quantity["source_bboxes"] == [QUANTITY_BOX]
    finding = next(item for item in payload["compliance"]["findings"] if item["rule_id"] == "NET_QUANTITY_FORMAT")
    assert finding["evidence"] == "Net Qty: 500g" and finding["value"] == "500 g" and finding["source_bboxes"] == [QUANTITY_BOX]
    assert not any(item["rule_id"].startswith("LOW_CONFIDENCE_") for item in payload["compliance"]["findings"])
    with sessions() as session:
        stored = session.scalar(select(ExtractedField).where(ExtractedField.inspection_id == UUID(payload["id"]), ExtractedField.field_name == "net_quantity"))
        assert (stored.source_text, stored.normalized_value, stored.source_bboxes) == ("Net Qty: 500g", "500 g", [QUANTITY_BOX])
    assert client.get(f"/api/inspections/{payload['id']}").json()["extraction"] == payload["extraction"]
    compliance = client.get(f"/api/inspections/{payload['id']}/compliance").json()
    assert next(item for item in compliance["findings"] if item["field"] == "net_quantity")["source_bboxes"] == [QUANTITY_BOX]


def test_extract_and_compliance_endpoints_store_and_use_source_text(api) -> None:
    client, sessions, _ = api
    inspection = client.post("/api/inspections", json={}).json()
    with sessions() as session:
        session.add(OcrResult(inspection_id=UUID(inspection["id"]), engine="PaddleOCR", languages=["en"], raw_text="\n".join(item["text"] for item in DETECTIONS), detections=DETECTIONS)); session.commit()
    extraction = client.post(f"/api/inspections/{inspection['id']}/extract").json()
    manufacturer = extraction["fields"]["manufacturer"]
    assert manufacturer["value"] == "ABC Foods" and manufacturer["source_text"] == "Manufactured by: ABC Foods" and manufacturer["source_bboxes"] == [DETECTIONS[1]["bbox"]]
    with sessions() as session:
        assert session.scalar(select(ExtractedField.matched_pattern).where(ExtractedField.field_name == "manufacturer")) == "manufacturer:same_line"
    compliance = client.post(f"/api/inspections/{inspection['id']}/compliance").json()
    assert next(item for item in compliance["findings"] if item["field"] == "manufacturer")["evidence"] == "Manufactured by: ABC Foods"


def test_compliance_evidence_falls_back_to_value_for_legacy_rows() -> None:
    assert field_evidence(ExtractedField(field_name="mrp", value="Rs 10", review_status="EXTRACTED")).evidence == "Rs 10"
    assert field_evidence(ExtractedField(field_name="mrp", value="Rs 10", source_text="MRP: Rs 10", review_status="REVIEW")).evidence == "MRP: Rs 10"


def test_rule_registration_is_shared_and_idempotent() -> None:
    engine = create_engine("sqlite://"); Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as db:
        codes = {rule.code for rule in default_ruleset().rules}
        first = ensure_compliance_rules(db); db.commit(); second = ensure_compliance_rules(db)
        assert set(first) == set(second) == codes
        assert len(db.scalars(select(ComplianceRule)).all()) == len(codes)
    engine.dispose()


# 2a. History compliance filter ---------------------------------------------

def test_history_filter_uses_overall_status_not_latest_row(api) -> None:
    client, sessions, _ = api
    ids = [UUID(client.post("/api/inspections", json={}).json()["id"]) for _ in range(3)]
    mixed, review, empty = ids
    now = datetime.now(timezone.utc)
    with sessions() as session:
        rules = ensure_compliance_rules(session)
        # The PASS row is the most recently updated; the old filter reported PASS.
        session.add_all([
            ComplianceResult(inspection_id=mixed, rule_id=rules["MRP_FORMAT"].id, result="FAIL", explanation="x", updated_at=now - timedelta(hours=1)),
            ComplianceResult(inspection_id=mixed, rule_id=rules["PRODUCT_IDENTITY"].id, result="PASS", explanation="x", updated_at=now),
            ComplianceResult(inspection_id=review, rule_id=rules["MRP_FORMAT"].id, result="PASS", explanation="x"),
            ComplianceResult(inspection_id=review, rule_id=rules["PRODUCT_IDENTITY"].id, result="REVIEW", explanation="x"),
        ]); session.commit()
    def listed(**params):
        return {UUID(item["id"]): item["compliance_status"] for item in client.get("/api/inspections", params=params).json()["items"]}
    assert listed() == {mixed: "FAIL", review: "REVIEW", empty: None}
    assert listed(compliance_status="FAIL") == {mixed: "FAIL"}
    assert listed(compliance_status="WARNING") == {review: "REVIEW"}
    assert listed(compliance_status="PASS") == {}
    assert client.get("/api/inspections", params={"compliance_status": "FAIL"}).json()["total"] == 1


# 3. Image access -----------------------------------------------------------

def test_owner_can_fetch_the_latest_image_with_private_headers(api) -> None:
    client, _, _ = api
    inspection = client.post("/api/inspections", json={}).json()
    client.post(f"/api/inspections/{inspection['id']}/image", files={"image": ("old.png", png(2, 2), "image/png")})
    latest = png(6, 3)
    uploaded = client.post(f"/api/inspections/{inspection['id']}/image", files={"image": ("new.png", latest, "image/png")}).json()
    assert (uploaded["width"], uploaded["height"]) == (6, 3)
    response = client.get(f"/api/inspections/{inspection['id']}/image")
    assert response.status_code == 200 and response.content == latest
    assert response.headers["content-type"] == "image/png"
    assert response.headers["cache-control"] == "private, max-age=300" and response.headers["x-content-type-options"] == "nosniff"


def test_latest_image_is_the_last_upload_even_when_timestamps_tie(api) -> None:
    client, sessions, _ = api
    inspection = client.post("/api/inspections", json={}).json()
    inspection_id = UUID(inspection["id"])
    same_instant = datetime(2026, 10, 15, 12, 0, tzinfo=timezone.utc)
    with sessions() as session:
        # Random UUIDs: on a created_at tie the id alone would pick either row.
        for name in ("first.png", "second.png", "third.png"):
            session.add(InspectionImage(inspection_id=inspection_id, storage_key=name, original_filename=name, content_type="image/png", byte_size=1, created_at=same_instant))
            session.commit()
        assert [image.upload_sequence for image in session.scalars(select(InspectionImage).where(InspectionImage.inspection_id == inspection_id).order_by(InspectionImage.upload_sequence))] == [1, 2, 3]
        assert latest_image(session, inspection_id).original_filename == "third.png"
    history = client.get("/api/inspections").json()["items"]
    assert next(item for item in history if item["id"] == inspection["id"])["image_filename"] == "third.png"


def test_image_is_hidden_from_other_users_and_missing_images(api) -> None:
    client, _, _ = api
    inspection = client.post("/api/inspections", json={}).json()
    assert client.get(f"/api/inspections/{inspection['id']}/image").status_code == 404
    client.post(f"/api/inspections/{inspection['id']}/image", files={"image": ("label.png", png(), "image/png")})
    app.dependency_overrides[get_current_user] = lambda: User(id=uuid4(), username="other", email="other@example.invalid", full_name="Other", password_hash="unused", is_active=True)
    assert client.get(f"/api/inspections/{inspection['id']}/image").status_code == 404
    assert client.get(f"/api/inspections/{uuid4()}/image").status_code == 404


def test_image_path_traversal_is_impossible(api, tmp_path) -> None:
    client, sessions, upload_dir = api
    secret = tmp_path / "secret.png"; secret.write_bytes(png())
    inspection = client.post("/api/inspections", json={}).json()
    with sessions() as session:
        session.add(InspectionImage(inspection_id=UUID(inspection["id"]), storage_key="../secret.png", original_filename="x.png", content_type="image/png", byte_size=1)); session.commit()
    upload_dir.mkdir(parents=True, exist_ok=True)
    response = client.get(f"/api/inspections/{inspection['id']}/image")
    assert response.status_code == 404 and response.content != secret.read_bytes()


def test_summary_backfills_dimensions_for_legacy_images(api) -> None:
    client, sessions, upload_dir = api
    inspection = client.post("/api/inspections", json={}).json()
    upload_dir.mkdir(parents=True, exist_ok=True); (upload_dir / "legacy.png").write_bytes(png(8, 5))
    with sessions() as session:
        session.add(InspectionImage(inspection_id=UUID(inspection["id"]), storage_key="legacy.png", original_filename="legacy.png", content_type="image/png", byte_size=1)); session.commit()
    image = client.get(f"/api/inspections/{inspection['id']}").json()["image"]
    assert (image["width"], image["height"]) == (8, 5)
    with sessions() as session:
        assert session.scalar(select(InspectionImage.width).where(InspectionImage.storage_key == "legacy.png")) == 8


def test_image_dimensions_follow_exif_orientation() -> None:
    output = BytesIO(); exif = Image.Exif(); exif[0x0112] = 6
    Image.new("RGB", (40, 20), "white").save(output, "JPEG", exif=exif)
    assert image_dimensions(output.getvalue()) == (20, 40)
    assert image_dimensions(b"not an image") is None


def test_ocr_boxes_are_mapped_back_to_original_pixels(tmp_path, monkeypatch) -> None:
    image = tmp_path / "large.png"; Image.new("RGB", (2000, 1000), "white").save(image)
    def fake_preprocess(path: Path, max_image_dimension: int = 640) -> Path:
        output = path.parent / f"processed-{max_image_dimension}.png"; output.write_bytes(b"x"); return output
    monkeypatch.setattr(PaddleOcrService, "_preprocess", staticmethod(fake_preprocess))
    service = PaddleOcrService(languages=("en", "hi"), max_image_dimension=1000, secondary_language_max_image_dimension=500)
    monkeypatch.setattr(service, "_predict", lambda language, path: [OcrDetection("MRP", 0.9, [[10.0, 20.0], [30.0, 20.0]])])
    monkeypatch.setattr(service, "_start_secondary_language", lambda language, path: SimpleNamespace(collect=lambda: [OcrDetection("मूल्य", 0.9, [[10.0, 20.0], [30.0, 20.0]])], cancel=lambda: None))
    boxes = {item.text: item.bbox for item in service.scan(image).detections}
    assert boxes == {"MRP": [[20.0, 40.0], [60.0, 40.0]], "मूल्य": [[40.0, 80.0], [120.0, 80.0]]}


# 4. Operational robustness -------------------------------------------------

class BrokenSession:
    def execute(self, *_args, **_kwargs):
        raise OperationalError("SELECT 1", {}, Exception("connection refused"))
    def get(self, *_args, **_kwargs):
        raise OperationalError("SELECT inspections", {}, Exception("connection refused"))
    def close(self):
        pass


def test_readiness_reports_database_state(api) -> None:
    client, _, _ = api
    assert client.get("/api/health/ready").json() == {"status": "ready", "database": "ok"}
    app.dependency_overrides[get_db] = lambda: BrokenSession()
    response = client.get("/api/health/ready")
    assert response.status_code == 503 and response.json() == {"status": "unavailable", "database": "unreachable"}
    assert client.get("/api/health").status_code == 200


def test_database_outage_returns_503_instead_of_500(api) -> None:
    client, _, _ = api
    app.dependency_overrides[get_db] = lambda: BrokenSession()
    response = client.get(f"/api/inspections/{uuid4()}/image")
    assert response.status_code == 503 and response.json() == {"detail": "Database unavailable. Please try again shortly."}



class FlakyEngine:
    def __init__(self, failures: int):
        self.failures = failures
    def connect(self):
        if self.failures:
            self.failures -= 1
            raise OperationalError("SELECT 1", {}, Exception("connection refused"))
        return create_engine("sqlite://").connect()


def test_container_start_waits_for_the_database(monkeypatch) -> None:
    monkeypatch.setattr(wait_for_db, "sleep", lambda _seconds: None)
    monkeypatch.setattr(wait_for_db, "engine", FlakyEngine(failures=2))
    assert wait_for_db.wait_for_database(timeout_seconds=30, interval_seconds=0) is True
    monkeypatch.setattr(wait_for_db, "engine", FlakyEngine(failures=10**6))
    assert wait_for_db.wait_for_database(timeout_seconds=0, interval_seconds=1) is False

# 5. PDF Indian scripts -----------------------------------------------------

def test_report_renders_devanagari_with_embedded_noto_fonts(tmp_path) -> None:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool); Base.metadata.create_all(engine)
    with sessionmaker(bind=engine, expire_on_commit=False)() as db:
        inspection = Inspection(id=uuid4(), inspection_number="PC-HINDI", status="CREATED", inspected_at=datetime.now(timezone.utc))
        rule = ComplianceRule(rule_code="NET_QUANTITY_FORMAT", description="Net quantity format.", target_field="net_quantity", severity="WARNING", enabled=True, configuration={})
        db.add_all([inspection, rule]); db.flush()
        db.add_all([
            ExtractedField(inspection_id=inspection.id, field_name="net_quantity", value="70 ग्राम", source_text="शुद्ध मात्रा 70 ग्राम", review_status="REVIEW"),
            ComplianceResult(inspection_id=inspection.id, rule_id=rule.id, result="FAIL", explanation="Unrecognised unit.", field_name="net_quantity", value="70 ग्राम", evidence="70 ग्राम"),
        ]); db.commit()
        pdf = InspectionReportService(db, tmp_path).generate(inspection)
    engine.dispose()
    assert pdf.startswith(b"%PDF") and b"NotoSansDevanagari" in pdf and b"NotoSans-Regular" in pdf


def test_report_evidence_rows_show_the_source_ocr_line(monkeypatch, tmp_path) -> None:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool); Base.metadata.create_all(engine)
    with sessionmaker(bind=engine, expire_on_commit=False)() as db:
        inspection = Inspection(id=uuid4(), inspection_number="PC-EVIDENCE", status="CREATED", inspected_at=datetime.now(timezone.utc))
        rule = ComplianceRule(rule_code="MRP_FORMAT", description="MRP format.", target_field="mrp", severity="WARNING", enabled=True, configuration={})
        db.add_all([inspection, rule]); db.flush()
        # A legacy result whose evidence repeats the value; the stored OCR line wins.
        db.add_all([ExtractedField(inspection_id=inspection.id, field_name="mrp", value="free", source_text="MRP (incl. taxes): free", review_status="REVIEW"), ComplianceResult(inspection_id=inspection.id, rule_id=rule.id, result="FAIL", explanation="Invalid MRP.", field_name="mrp", value="free", evidence="free")]); db.commit()
        captured = {}; monkeypatch.setattr("backend.app.services.report.SimpleDocTemplate.build", lambda self, story, **kwargs: captured.setdefault("story", story))
        InspectionReportService(db, tmp_path).generate(inspection)
    engine.dispose()
    card = next(flowable for flowable in captured["story"] if flowable.__class__.__name__ == "KeepTogether")
    rows = {row[0].getPlainText(): row[1].getPlainText() for row in card._content[0]._cellvalues}
    assert rows["Evidence"] == "MRP (incl. taxes): free" and rows["Extracted value"] == "free"
