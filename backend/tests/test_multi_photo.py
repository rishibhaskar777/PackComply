"""Multi-photo inspections: panels, per-image OCR, merge, conflicts, add-photo, background progress and SSE."""
import json
import threading
from io import BytesIO
from types import SimpleNamespace
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.api.auth import get_current_user
from backend.app.api.inspections import get_ocr_service
from backend.app.core.config import get_settings
from backend.app.core.database import get_db
from backend.app.main import app
from backend.app.models import Base, ExtractedField, Inspection, InspectionEvent, InspectionImage, OcrResult, User
from backend.app.services import jobs
from backend.app.services.extraction.extraction_service import ExtractedValue
from backend.app.services.merge import PhotoExtraction, merge_photos
from backend.app.services.ocr import OcrProcessingError
from backend.app.services.progress import progress_store
from backend.tests.conftest import process_and_wait

OFFICIAL = User(id=UUID("00000000-0000-0000-0000-000000000001"), username="test", email="test@example.invalid", full_name="Test", password_hash="x", role="OFFICIAL", is_active=True)

BACK, FRONT, DATE = 40, 50, 60  # photo widths identify which text the fake reads
TEXT = {
    BACK: ["Manufactured by: ABC Foods Pvt Ltd", "12 Industrial Area, Pune 411001", "MRP Rs 100 (incl. of all taxes)", "Net Qty: 500 g", "Customer care: 1800123456", "care@abcfoods.example"],
    FRONT: ["GLUCO BISCUITS", "Net Qty: 450 g"],
    DATE: ["MFG 03/2026", "EXP 03/2027", "BATCH A123"],
}


def png(width: int, height: int = 40, shade: int = 255) -> bytes:
    output = BytesIO()
    Image.new("RGB", (width, height), (shade, shade, shade)).save(output, "PNG")
    return output.getvalue()


def detections(lines: list[str]) -> list[dict]:
    return [{"text": text, "confidence": 0.95, "bbox": [[10, 30 * i], [300, 30 * i], [300, 30 * i + 20], [10, 30 * i + 20]], "language": "en"} for i, text in enumerate(lines)]


class PanelOcr:
    """Fake OCR that reads different text depending on the photo's width and records what it scanned."""
    languages = ("en", "hi")

    def __init__(self, gate: threading.Event | None = None, fail_widths: set[int] | None = None):
        self.scanned: list[int] = []
        self.gate, self.fail_widths = gate, fail_widths or set()

    def scan(self, path, progress=None):
        width = Image.open(path).width
        self.scanned.append(width)
        if progress:
            progress("en", "start")
            progress("hi", "start")
        if self.gate is not None:
            assert self.gate.wait(10)
        if width in self.fail_widths:
            raise OcrProcessingError("worker timeout")
        if progress:
            progress("en", "done")
            progress("hi", "done")
        lines = TEXT.get(width, ["PLAIN"])
        return SimpleNamespace(engine="PaddleOCR", languages=["en", "hi"], raw_text="\n".join(lines), skipped_languages=[], as_database_detections=lambda: detections(lines))


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path))
    get_settings.cache_clear()
    progress_store.clear()
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    with sessions() as session:
        session.merge(OFFICIAL)
        session.commit()

    def override_db():
        with sessions() as session:
            yield session
    ocr = PanelOcr()
    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = lambda: OFFICIAL
    app.dependency_overrides[get_ocr_service] = lambda: ocr
    state = SimpleNamespace(client=TestClient(app), sessions=sessions, ocr=ocr, tmp=tmp_path)
    yield state
    app.dependency_overrides.clear()
    get_settings.cache_clear()
    progress_store.clear()
    engine.dispose()


def create(env) -> str:
    return env.client.post("/api/inspections", json={}).json()["id"]


def upload(env, inspection_id: str, width: int, panel: str | None = None, content: bytes | None = None):
    data = {"panel": panel} if panel else None
    return env.client.post(f"/api/inspections/{inspection_id}/image", files={"image": ("p.png", content or png(width), "image/png")}, data=data)


def fields(summary: dict) -> dict:
    return summary["extraction"]["fields"]


def three_photo_inspection(env) -> str:
    inspection_id = create(env)
    for width, panel in ((BACK, "BACK"), (FRONT, "FRONT"), (DATE, "DATE_STAMP")):
        assert upload(env, inspection_id, width, panel).status_code == 201
    started, summary = process_and_wait(env.client, inspection_id)
    assert started.status_code == 202 and summary.json()["status"] == "COMPLETED", summary.json()
    return inspection_id


# ------------------------------------------------------------ merge rules (pure)

def value(text, confidence=0.9, normalized=None):
    return ExtractedValue(text, confidence, False, text, "test", normalized)


def photo(panel, **values):
    return PhotoExtraction(UUID(int=hash(panel) % 10**9 + 1), panel, SimpleNamespace(id=UUID(int=1), detections=[]), values)


def test_ties_go_to_the_panel_that_owns_the_field() -> None:
    back, front, date = photo("BACK", product_name=value("Back name", 0.8), net_quantity=value("500 g", 0.9), expiry_date=value("03/2027", 0.9)), \
        photo("FRONT", product_name=value("Front name", 0.8), net_quantity=value("500 g", 0.9), expiry_date=value("03/2028", 0.9)), \
        photo("DATE_STAMP", product_name=value("Stamp name", 0.8), net_quantity=value("500 g", 0.9), expiry_date=value("03/2029", 0.9))
    merged = merge_photos([back, front, date])
    assert (merged["product_name"].panel, merged["net_quantity"].panel, merged["expiry_date"].panel) == ("FRONT", "BACK", "DATE_STAMP")
    assert merged["expiry_date"].value.value == "03/2029" and merged["expiry_date"].image_id == date.image_id


def test_higher_confidence_beats_the_panel_preference() -> None:
    merged = merge_photos([photo("BACK", product_name=value("Back name", 0.8)), photo("FRONT", product_name=value("Front name", 0.6))])
    assert merged["product_name"].panel == "BACK"
    merged = merge_photos([photo("BACK", expiry_date=value("03/2027", 0.95)), photo("DATE_STAMP", expiry_date=value("03/2027", 0.7))])
    assert merged["expiry_date"].panel == "BACK" and merged["expiry_date"].alternatives == []


def test_different_values_for_a_rule_field_are_recorded_as_alternatives() -> None:
    merged = merge_photos([photo("BACK", net_quantity=value("500 g", 0.9, "500 g")), photo("FRONT", net_quantity=value("450 g", 0.85, "450 g"))])
    item = merged["net_quantity"]
    assert item.value.value == "500 g" and [(alt["panel"], alt["value"]) for alt in item.alternatives] == [("FRONT", "450 g")]
    # Names differing between front and back are normal and not a conflict.
    assert merge_photos([photo("BACK", product_name=value("Gluco")), photo("FRONT", product_name=value("Gluco Biscuits"))])["product_name"].alternatives == []


def test_an_absent_declaration_never_beats_a_positive_one_and_single_photos_merge_to_themselves() -> None:
    merged = merge_photos([photo("BACK", mrp_declaration=value("absent", 0.99)), photo("DATE_STAMP", mrp_declaration=value("present_without_value", 0.5))])
    assert merged["mrp_declaration"].value.value == "present_without_value"
    single = photo("BACK", mrp=value("₹10.00"), net_quantity=ExtractedValue())
    only = merge_photos([single])
    assert only["mrp"].value is single.values["mrp"] and only["net_quantity"].value.value is None and only["mrp"].image_id == single.image_id


# --------------------------------------------------------------- upload and files

def test_upload_defaults_to_back_validates_the_panel_and_caps_photos_at_four(env) -> None:
    inspection_id = create(env)
    assert upload(env, inspection_id, BACK).json()["panel"] == "BACK"
    assert upload(env, inspection_id, FRONT, "sideways").status_code == 422
    assert upload(env, inspection_id, FRONT, "front").json()["panel"] == "FRONT"
    assert upload(env, inspection_id, DATE, "DATE_STAMP").status_code == 201
    assert upload(env, inspection_id, 70, "OTHER").status_code == 201
    assert upload(env, inspection_id, 71, "OTHER").status_code == 409  # a fifth photo
    assert upload(env, inspection_id, 72, "BACK").status_code == 201  # replacing a panel is not a fifth photo


def test_replacing_a_panel_keeps_the_old_file_and_checksum_as_evidence(env) -> None:
    inspection_id = create(env)
    first = upload(env, inspection_id, BACK).json()
    second = upload(env, inspection_id, 41, "BACK").json()
    listing = env.client.get(f"/api/inspections/{inspection_id}/images").json()
    assert [(row["id"], row["panel"], row["active"]) for row in listing] == [(first["id"], "BACK", False), (second["id"], "BACK", True)]
    assert all(set(row) >= {"id", "panel", "width", "height", "checksum", "uploaded_at"} for row in listing) and listing[0]["checksum"] != listing[1]["checksum"]
    assert env.client.get(f"/api/inspections/{inspection_id}/images/{first['id']}").status_code == 200  # the replaced file is still served
    assert Image.open(BytesIO(env.client.get(f"/api/inspections/{inspection_id}/images/{second['id']}").content)).width == 41
    assert Image.open(BytesIO(env.client.get(f"/api/inspections/{inspection_id}/image").content)).width == 41  # the old endpoint = newest BACK
    assert env.client.get(f"/api/inspections/{inspection_id}/images/{UUID(int=5)}").status_code == 404
    events = [event["type"] for event in env.client.get(f"/api/inspections/{inspection_id}/audit").json()]
    assert events.count("PHOTO_UPLOADED") == 1 and events.count("PHOTO_REPLACED") == 1


def test_the_old_image_endpoint_serves_the_newest_photo_when_there_is_no_back_panel(env) -> None:
    inspection_id = create(env)
    upload(env, inspection_id, FRONT, "FRONT")
    assert Image.open(BytesIO(env.client.get(f"/api/inspections/{inspection_id}/image").content)).width == FRONT


# ------------------------------------------------------------- processing and merge

def test_three_photos_are_read_and_merged_with_the_image_each_field_came_from(env) -> None:
    inspection_id = three_photo_inspection(env)
    summary = env.client.get(f"/api/inspections/{inspection_id}").json()
    images = {row["panel"]: row["id"] for row in summary["images"]}
    assert sorted(env.ocr.scanned) == [BACK, FRONT, DATE] and len(summary["images"]) == 3
    merged = fields(summary)
    assert merged["manufacturer"]["image_id"] == images["BACK"] and merged["manufacturer"]["panel"] == "BACK"
    assert merged["mrp"]["image_id"] == images["BACK"]
    assert merged["product_name"]["image_id"] == images["FRONT"] and merged["product_name"]["value"] == "GLUCO BISCUITS"
    assert summary["product_name"] == "Gluco Biscuits"  # display form; the raw OCR value is kept on the field
    assert merged["manufacturing_date"]["image_id"] == images["DATE_STAMP"] and merged["expiry_date"]["panel"] == "DATE_STAMP"
    assert merged["batch_number"]["panel"] == "DATE_STAMP"
    for name in ("manufacturer", "net_quantity", "product_name"):
        assert merged[name]["source_bboxes"], name
    # Boxes lie on the photo named by image_id: the finding carries the same photo id.
    finding = next(item for item in summary["compliance"]["findings"] if item["rule_id"] == "MANUFACTURER_INFORMATION")
    assert finding["image_id"] == images["BACK"] and finding["source_bboxes"]
    with env.sessions() as session:
        assert session.scalar(select(func.count()).select_from(OcrResult).where(OcrResult.inspection_id == UUID(inspection_id))) == 3


def test_conflicting_net_quantities_become_a_review_note_not_a_silent_pick(env) -> None:
    inspection_id = three_photo_inspection(env)
    summary = env.client.get(f"/api/inspections/{inspection_id}").json()
    quantity = fields(summary)["net_quantity"]
    assert quantity["value"] == "500 g" and quantity["panel"] == "BACK"
    assert [(alt["panel"], alt["value"]) for alt in quantity["alternatives"]] == [("FRONT", "450 g")]
    finding = next(item for item in summary["compliance"]["findings"] if item["rule_id"] == "NET_QUANTITY_FORMAT")
    assert finding["status"] == "REVIEW" and "450 g" in finding["message"] and "500 g" in finding["message"] and "Confirm on the pack" in finding["message"]
    # An inspector's correction settles the disagreement.
    fixed = env.client.post(f"/api/inspections/{inspection_id}/corrections", json={"field_name": "net_quantity", "corrected_value": "500 g", "reason": "Verified on physical pack"}).json()
    assert next(item for item in fixed["compliance"]["findings"] if item["rule_id"] == "NET_QUANTITY_FORMAT")["status"] == "PASS"
    assert fields(fixed)["net_quantity"]["alternatives"] == []


def test_reanalyse_re_merges_every_photo_without_new_ocr(env) -> None:
    inspection_id = three_photo_inspection(env)
    scans = len(env.ocr.scanned)
    before = fields(env.client.get(f"/api/inspections/{inspection_id}").json())
    again = env.client.post(f"/api/inspections/{inspection_id}/reanalyse").json()
    assert len(env.ocr.scanned) == scans
    assert {name: (item["value"], item["image_id"]) for name, item in fields(again).items()} == {name: (item["value"], item["image_id"]) for name, item in before.items()}


def test_the_same_file_is_never_read_twice_in_one_inspection(env) -> None:
    inspection_id = create(env)
    same = png(BACK)
    upload(env, inspection_id, BACK, "BACK", same)
    upload(env, inspection_id, BACK, "FRONT", same)
    _, summary = process_and_wait(env.client, inspection_id)
    assert summary.json()["status"] == "COMPLETED" and env.ocr.scanned == [BACK]
    with env.sessions() as session:
        rows = session.scalars(select(OcrResult).where(OcrResult.inspection_id == UUID(inspection_id))).all()
        assert len(rows) == 2 and {row.raw_text for row in rows} == {"\n".join(TEXT[BACK])} and len({row.image_id for row in rows}) == 2


def test_a_single_photo_inspection_behaves_as_before(env) -> None:
    inspection_id = create(env)
    upload(env, inspection_id, BACK)
    _, response = process_and_wait(env.client, inspection_id)
    summary = response.json()
    assert summary["status"] == "COMPLETED" and summary["image"]["panel"] == "BACK" and len(summary["images"]) == 1
    assert fields(summary)["net_quantity"]["value"] == "500 g" and fields(summary)["net_quantity"]["alternatives"] == []
    assert summary["ocr"]["image_id"] == summary["image"]["id"] and summary["compliance"]["findings"]
    assert env.client.get(f"/api/inspections/{inspection_id}/image").status_code == 200


# ---------------------------------------------------------------------- add photo

def test_adding_a_photo_to_a_completed_inspection_reads_only_the_new_photo(env) -> None:
    inspection_id = create(env)
    upload(env, inspection_id, BACK)
    process_and_wait(env.client, inspection_id)
    assert env.client.post(f"/api/inspections/{inspection_id}/verify", json={"remarks": "ok"}).json()["review"]["status"] == "VERIFIED"
    before = fields(env.client.get(f"/api/inspections/{inspection_id}").json())
    assert before["manufacturing_date"]["value"] is None
    env.ocr.scanned.clear()

    added = upload(env, inspection_id, DATE, "DATE_STAMP")
    assert added.status_code == 201
    mid = env.client.get(f"/api/inspections/{inspection_id}").json()
    assert mid["status"] == "COMPLETED" and mid["review"]["status"] == "UNREVIEWED"  # still readable, no longer verified
    started, response = process_and_wait(env.client, inspection_id)
    assert started.status_code == 202
    summary = response.json()
    assert env.ocr.scanned == [DATE]  # the back panel is not read again
    assert summary["status"] == "COMPLETED" and summary["review"]["status"] == "UNREVIEWED"
    assert fields(summary)["manufacturing_date"]["panel"] == "DATE_STAMP" and fields(summary)["manufacturing_date"]["value"]
    assert fields(summary)["manufacturer"]["panel"] == "BACK"
    audit = env.client.get(f"/api/inspections/{inspection_id}/audit").json()
    event = next(item for item in audit if item["type"] == "PHOTO_ADDED")
    assert event["details"]["panel"] == "DATE_STAMP" and event["details"]["checksum"] and not any(item["details"].get("image_id") == added.json()["id"] for item in audit if item["type"] == "PHOTO_UPLOADED")


def test_retaking_a_photo_replaces_it_and_re_reads_only_that_photo(env) -> None:
    inspection_id = three_photo_inspection(env)
    env.ocr.scanned.clear()
    assert upload(env, inspection_id, FRONT, "FRONT", png(FRONT, 44)).status_code == 201  # same text, a new file
    _, response = process_and_wait(env.client, inspection_id)
    assert env.ocr.scanned == [FRONT] and response.json()["status"] == "COMPLETED"
    summary = response.json()
    assert len(summary["images"]) == 4 and sum(row["active"] for row in summary["images"]) == 3
    assert any(item["type"] == "PHOTO_REPLACED" for item in env.client.get(f"/api/inspections/{inspection_id}/audit").json())


# ------------------------------------------------------------------ live progress

def test_processing_returns_202_at_once_and_progress_lists_every_step(env) -> None:
    gate = threading.Event()
    env.ocr.gate = gate
    inspection_id = create(env)
    upload(env, inspection_id, BACK, "BACK")
    upload(env, inspection_id, DATE, "DATE_STAMP")
    started = env.client.post(f"/api/inspections/{inspection_id}/process")
    assert started.status_code == 202
    body = started.json()
    assert body["state"] == "running" and body["percent"] < 100
    kinds = [step["kind"] for step in body["steps"]]
    assert kinds == ["prepare", "read", "prepare", "read", "link", "rules", "risk"]
    assert [step["label"] for step in body["steps"]][-3:] == ["Linking declarations", "Checking 14 rules", "Scoring risk"]
    assert [step["languages"] for step in body["steps"] if step["kind"] == "read"] == [["en", "hi"], ["en"]]
    gate.set()
    jobs.wait_for(UUID(inspection_id))
    done = env.client.get(f"/api/inspections/{inspection_id}/progress").json()
    assert done["state"] == "completed" and done["percent"] == 100 and done["stage"] == "COMPLETED" and done["error"] is None
    assert {step["status"] for step in done["steps"]} == {"done"} and all(step["duration_ms"] is not None for step in done["steps"])
    assert set(done) >= {"inspection_id", "state", "percent", "elapsed_ms", "steps", "completed_steps", "total_steps", "error", "stage"}


def test_percent_comes_only_from_finished_steps(env) -> None:
    gate = threading.Event()
    env.ocr.gate = gate
    inspection_id = create(env)
    upload(env, inspection_id, BACK)
    env.client.post(f"/api/inspections/{inspection_id}/process")
    running = env.client.get(f"/api/inspections/{inspection_id}/progress").json()
    done_steps = sum(step["status"] == "done" for step in running["steps"])
    assert running["percent"] == round(100 * done_steps / len(running["steps"])) and running["percent"] < 100
    gate.set()
    jobs.wait_for(UUID(inspection_id))


def test_a_second_start_while_running_is_a_conflict(env) -> None:
    gate = threading.Event()
    env.ocr.gate = gate
    inspection_id = create(env)
    upload(env, inspection_id, BACK)
    assert env.client.post(f"/api/inspections/{inspection_id}/process").status_code == 202
    second = env.client.post(f"/api/inspections/{inspection_id}/process")
    assert second.status_code == 409 and "already" in second.json()["detail"]
    assert upload(env, inspection_id, FRONT, "FRONT").status_code == 409  # no new photo mid-run
    gate.set()
    jobs.wait_for(UUID(inspection_id))
    assert env.client.get(f"/api/inspections/{inspection_id}").json()["status"] == "COMPLETED"


def test_one_unreadable_photo_does_not_stop_the_others_and_retry_reads_only_it(env) -> None:
    env.ocr.fail_widths = {DATE}
    inspection_id = create(env)
    for width, panel in ((BACK, "BACK"), (DATE, "DATE_STAMP")):
        upload(env, inspection_id, width, panel)
    _, finished = process_and_wait(env.client, inspection_id)
    summary = finished.json()
    assert summary["status"] == "COMPLETED"
    broken = next(image for image in summary["images"] if image["panel"] == "DATE_STAMP")
    assert broken["read_error"] and next(image for image in summary["images"] if image["panel"] == "BACK")["read_error"] is None
    # The date declarations are on the unread photo: they are REVIEW with a note, never a fake PASS or FAIL.
    findings = {item["rule_id"]: item for item in summary["compliance"]["findings"]}
    dated = [item for item in findings.values() if item["field"] in ("manufacturing_date", "expiry_date")]
    assert dated and all(item["status"] == "REVIEW" and "could not be read" in item["message"] for item in dated), dated
    assert summary["compliance"]["verdict"] != "COMPLIANT"
    progress = env.client.get(f"/api/inspections/{inspection_id}/progress").json()
    failed = [step for step in progress["steps"] if step["status"] == "failed"]
    assert progress["state"] == "completed" and failed and all(step["panel"] == "DATE_STAMP" for step in failed)
    env.ocr.scanned.clear()
    env.ocr.fail_widths = set()
    _, resumed = process_and_wait(env.client, inspection_id)
    after = resumed.json()
    assert after["status"] == "COMPLETED" and env.ocr.scanned == [DATE]  # the back panel's reading was kept
    assert all(image["read_error"] is None for image in after["images"])
    assert any(item["status"] == "PASS" for item in after["compliance"]["findings"] if item["field"] in ("manufacturing_date", "expiry_date"))


def test_failure_marks_the_step_and_try_again_resumes_when_no_photo_can_be_read(env) -> None:
    env.ocr.fail_widths = {FRONT}
    inspection_id = create(env)
    upload(env, inspection_id, FRONT, "FRONT")
    _, failed = process_and_wait(env.client, inspection_id)
    assert failed.json()["status"] == "FAILED"
    progress = env.client.get(f"/api/inspections/{inspection_id}/progress").json()
    broken = next(step for step in progress["steps"] if step["status"] == "failed" and step["kind"] == "read")
    assert progress["state"] == "failed" and broken["panel"] == "FRONT" and broken["message"] and progress["error"]["step_id"] == broken["id"]
    assert progress["percent"] < 100
    env.ocr.scanned.clear()
    env.ocr.fail_widths = set()
    _, resumed = process_and_wait(env.client, inspection_id)
    assert resumed.json()["status"] == "COMPLETED" and env.ocr.scanned == [FRONT]
    with env.sessions() as session:
        assert session.scalar(select(func.count()).select_from(Inspection)) == 1


def test_progress_after_a_restart_falls_back_to_the_stored_stage(env) -> None:
    inspection_id = create(env)
    upload(env, inspection_id, BACK)
    assert env.client.get(f"/api/inspections/{inspection_id}/progress").json()["state"] == "idle"
    with env.sessions() as session:
        session.get(Inspection, UUID(inspection_id)).status = "OCR_PROCESSING"
        session.commit()
    stale = env.client.get(f"/api/inspections/{inspection_id}/progress").json()
    assert stale["state"] == "failed" and "interrupted" in stale["error"]["message"] and stale["steps"] == []
    process_and_wait(env.client, inspection_id)
    progress_store.clear()
    assert env.client.get(f"/api/inspections/{inspection_id}/progress").json()["state"] == "completed"


def test_progress_is_owner_only(env) -> None:
    inspection_id = create(env)
    other = User(id=UUID(int=99), username="o", email="o@example.invalid", full_name="O", password_hash="x", is_active=True)
    app.dependency_overrides[get_current_user] = lambda: other
    for path in ("progress", "progress/stream", "images"):
        assert env.client.get(f"/api/inspections/{inspection_id}/{path}").status_code == 404


def sse(env, inspection_id: str) -> list[tuple[str, dict]]:
    events, name = [], None
    with env.client.stream("GET", f"/api/inspections/{inspection_id}/progress/stream") as response:
        assert response.headers["content-type"].startswith("text/event-stream")
        for line in response.iter_lines():
            if line.startswith("event:"):
                name = line.split(":", 1)[1].strip()
            elif line.startswith("data:"):
                events.append((name, json.loads(line.split(":", 1)[1])))
    return events


def test_sse_streams_changes_and_closes_when_the_run_completes(env) -> None:
    gate = threading.Event()
    env.ocr.gate = gate
    inspection_id = create(env)
    upload(env, inspection_id, BACK)
    env.client.post(f"/api/inspections/{inspection_id}/process")
    threading.Timer(0.4, gate.set).start()
    events = sse(env, inspection_id)
    progress = [data for name, data in events if name == "progress"]
    assert progress[0]["state"] == "running" and progress[-1]["state"] == "completed" and progress[-1]["percent"] == 100
    assert [data["version"] for data in progress] == sorted({data["version"] for data in progress})  # only changes are sent
    percents = [data["percent"] for data in progress]
    assert percents == sorted(percents)
    assert events[-1][0] == "done"


def test_sse_for_an_idle_or_finished_inspection_sends_one_snapshot_and_closes(env) -> None:
    inspection_id = create(env)
    upload(env, inspection_id, BACK)
    process_and_wait(env.client, inspection_id)
    events = sse(env, inspection_id)
    assert [name for name, _ in events] == ["progress", "done"] and events[0][1]["state"] == "completed"
    progress_store.clear()
    assert sse(env, inspection_id)[0][1]["state"] == "completed"  # from the stored stage


def test_sse_reports_a_failed_run_and_closes(env) -> None:
    env.ocr.fail_widths = {BACK}
    inspection_id = create(env)
    upload(env, inspection_id, BACK)
    env.client.post(f"/api/inspections/{inspection_id}/process")
    jobs.wait_for(UUID(inspection_id))
    events = sse(env, inspection_id)
    assert events[0][1]["state"] == "failed" and events[0][1]["error"]["message"] and events[-1][0] == "done"


# ---------------------------------------------------------- names, brand correction

def test_product_and_brand_names_are_correctable_and_used_everywhere(env) -> None:
    inspection_id = three_photo_inspection(env)
    renamed = env.client.post(f"/api/inspections/{inspection_id}/corrections", json={"field_name": "product_name", "corrected_value": "Gluco Biscuits 450 g"}).json()
    assert renamed["product_name"] == "Gluco Biscuits 450 g" and fields(renamed)["product_name"]["source"] == "INSPECTOR"
    branded = env.client.post(f"/api/inspections/{inspection_id}/corrections", json={"field_name": "brand_name", "corrected_value": "Himalaya"}).json()
    assert branded["brand_name"] == "Himalaya" and fields(branded)["brand_owner"]["value"] == "Himalaya"
    item = env.client.get("/api/inspections", params={"search": "himalaya"}).json()["items"][0]
    assert item["brand_name"] == "Himalaya" and item["product_name"] == "Gluco Biscuits 450 g"
    assert env.client.get(f"/api/inspections/{inspection_id}/report").status_code == 200
    again = env.client.post(f"/api/inspections/{inspection_id}/reanalyse").json()
    assert again["product_name"] == "Gluco Biscuits 450 g" and again["brand_name"] == "Himalaya"  # corrections survive re-merging
    with env.sessions() as session:
        assert session.scalar(select(func.count()).select_from(InspectionEvent)) >= 2


def test_config_tells_clients_the_ocr_size_and_limits(env) -> None:
    config = env.client.get("/api/inspections/config").json()
    assert config["ocr_max_image_dimension"] == get_settings().ocr_max_image_dimension and config["max_images"] == 4
    assert config["panels"] == ["BACK", "FRONT", "DATE_STAMP", "OTHER"]
