"""Reading photos while the inspector captures the next panel: per-panel languages, read-ahead, no duplicate OCR."""
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import pytest
from PIL import Image
from sqlalchemy import func, select

from backend.app.api.inspections import get_compliance_engine, get_extraction_service, get_ocr_service, get_workflow
from backend.app.core.config import Settings, get_ocr_panel_languages
from backend.app.main import app
from backend.app.models import InspectionImage, OcrResult
from backend.app.services import jobs
from backend.app.services.ocr import PaddleOcrService
from backend.tests.test_multi_photo import BACK, DATE, FRONT, TEXT, PanelOcr, create, detections, env, png, upload  # noqa: F401


class LanguageAwareOcr(PanelOcr):
    """PanelOcr that accepts the per-panel ``languages`` argument and records it."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.languages_used: dict[int, tuple[str, ...] | None] = {}

    def scan(self, path, progress=None, languages=None):
        width = Image.open(path).width
        self.languages_used[width] = languages
        self.scanned.append(width)
        codes = list(languages or self.languages)
        for code in codes:
            progress and progress(code, "start")
        if self.gate is not None:
            assert self.gate.wait(10)
        from backend.app.services.ocr import OcrProcessingError
        if width in self.fail_widths:
            raise OcrProcessingError("worker timeout")
        for code in codes:
            progress and progress(code, "done")
        lines = TEXT.get(width, ["PLAIN"])
        return SimpleNamespace(engine="PaddleOCR", languages=codes, raw_text="\n".join(lines), skipped_languages=[], as_database_detections=lambda: detections(lines))


@pytest.fixture
def lang_env(env):  # noqa: F811
    ocr = LanguageAwareOcr()
    app.dependency_overrides[get_ocr_service] = lambda: ocr
    env.ocr = ocr
    return env


def ocr_count(env, inspection_id: str) -> int:
    with env.sessions() as db:
        return db.scalar(select(func.count()).select_from(OcrResult).where(OcrResult.inspection_id == UUID(inspection_id)))


def wait_until(predicate, timeout=10.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(0.02)
    return False


def upload_now(env, inspection_id: str, width: int, panel: str):
    response = env.client.post(f"/api/inspections/{inspection_id}/images?process_now=true", files={"image": ("p.png", png(width), "image/png")}, data={"panel": panel})
    assert response.status_code == 201
    return response


# --------------------------------------------------------------- configuration

def test_panel_languages_default_reads_only_english_on_date_stamps() -> None:
    parsed = get_ocr_panel_languages(Settings())
    assert parsed["DATE_STAMP"] == ("en",) and parsed["BACK"] == ("en", "hi") and parsed["FRONT"] == ("en", "hi")


def test_panel_languages_are_configurable_and_validated() -> None:
    assert get_ocr_panel_languages(Settings(ocr_panel_languages="DATE_STAMP=en;BACK=hi+en")) == {"DATE_STAMP": ("en",), "BACK": ("hi", "en")}
    with pytest.raises(ValueError):
        get_ocr_panel_languages(Settings(ocr_panel_languages="BACK=xx"))


def test_ocr_scan_can_be_narrowed_to_english_only(tmp_path: Path, monkeypatch) -> None:
    image = tmp_path / "stamp.png"
    Image.new("RGB", (120, 80), "white").save(image)
    service = PaddleOcrService(languages=("en", "hi"))
    predicted, secondary = [], []
    monkeypatch.setattr(service, "_predict", lambda language, path: predicted.append(language) or [])
    monkeypatch.setattr(service, "_start_secondary_language", lambda language, path: secondary.append(language) or SimpleNamespace(collect=lambda: [], cancel=lambda: None))

    narrowed = service.scan(image, languages=("en",))
    assert predicted == ["en"] and secondary == [] and narrowed.languages == ["en"]

    both = service.scan(image)
    assert secondary == ["hi"] and both.languages == ["en", "hi"]


def test_each_panel_is_scanned_with_its_own_languages(lang_env) -> None:
    inspection_id = create(lang_env)
    for width, panel in ((BACK, "BACK"), (FRONT, "FRONT"), (DATE, "DATE_STAMP")):
        upload(lang_env, inspection_id, width, panel)
    lang_env.client.post(f"/api/inspections/{inspection_id}/process")
    jobs.wait_for(UUID(inspection_id))
    assert lang_env.ocr.languages_used[DATE] == ("en",)
    assert lang_env.ocr.languages_used[BACK] is None and lang_env.ocr.languages_used[FRONT] is None  # the engine's own list


# ------------------------------------------------------------------- read-ahead

def test_ocr_starts_at_upload_when_process_now_is_set(lang_env) -> None:
    inspection_id = create(lang_env)
    upload_now(lang_env, inspection_id, BACK, "BACK")
    assert wait_until(lambda: lang_env.ocr.scanned == [BACK] and ocr_count(lang_env, inspection_id) == 1)
    # Reading ahead stores the OCR only: the inspection is not processed.
    assert lang_env.client.get(f"/api/inspections/{inspection_id}").json()["status"] == "IMAGE_UPLOADED"


def test_upload_without_the_flag_does_not_start_ocr(lang_env) -> None:
    inspection_id = create(lang_env)
    upload(lang_env, inspection_id, BACK, "BACK")
    time.sleep(0.2)
    assert lang_env.ocr.scanned == []


def test_final_process_does_not_read_photos_a_second_time(lang_env) -> None:
    inspection_id = create(lang_env)
    upload_now(lang_env, inspection_id, BACK, "BACK")
    upload_now(lang_env, inspection_id, FRONT, "FRONT")
    assert wait_until(lambda: ocr_count(lang_env, inspection_id) == 2)
    upload(lang_env, inspection_id, DATE, "DATE_STAMP")
    started = lang_env.client.post(f"/api/inspections/{inspection_id}/process")
    assert started.status_code == 202
    # Photos already read show up as finished read steps with their real duration.
    reads = [step for step in started.json()["steps"] if step["kind"] == "read"]
    assert [step["status"] for step in reads][:2] == ["done", "done"] and all(step["duration_ms"] is not None for step in reads[:2])
    jobs.wait_for(UUID(inspection_id))
    assert sorted(lang_env.ocr.scanned) == sorted([BACK, FRONT, DATE])
    summary = lang_env.client.get(f"/api/inspections/{inspection_id}").json()
    assert summary["status"] == "COMPLETED" and summary["extraction"]["fields"]["net_quantity"]["value"]


def test_final_process_waits_for_a_read_still_running(lang_env) -> None:
    gate = threading.Event()
    lang_env.ocr.gate = gate
    inspection_id = create(lang_env)
    upload_now(lang_env, inspection_id, BACK, "BACK")
    assert wait_until(lambda: lang_env.ocr.scanned == [BACK])  # in flight, held by the gate
    started = lang_env.client.post(f"/api/inspections/{inspection_id}/process")
    assert started.status_code == 202
    time.sleep(0.2)
    progress = lang_env.client.get(f"/api/inspections/{inspection_id}/progress").json()
    assert progress["state"] == "running"
    assert next(step for step in progress["steps"] if step["id"] == "link")["status"] == "pending"
    gate.set()
    jobs.wait_for(UUID(inspection_id))
    done = lang_env.client.get(f"/api/inspections/{inspection_id}/progress").json()
    assert done["state"] == "completed"
    assert lang_env.ocr.scanned == [BACK] and ocr_count(lang_env, inspection_id) == 1  # one read, not two


def test_asking_for_the_same_photo_twice_reads_it_once(lang_env) -> None:
    gate = threading.Event()
    lang_env.ocr.gate = gate
    inspection_id = create(lang_env)
    upload(lang_env, inspection_id, BACK, "BACK")
    with lang_env.sessions() as db:
        image = db.scalar(select(InspectionImage).where(InspectionImage.inspection_id == UUID(inspection_id)))
        workflow = get_workflow(db, lang_env.ocr, get_extraction_service(), get_compliance_engine())
        first = jobs.start_image_ocr(workflow, UUID(inspection_id), image.id)
        second = jobs.start_image_ocr(workflow, UUID(inspection_id), image.id)
    assert first is second
    gate.set()
    first.result(10)
    assert lang_env.ocr.scanned == [BACK]


def test_a_failed_read_ahead_is_retried_by_the_final_process(lang_env) -> None:
    lang_env.ocr.fail_widths = {BACK}
    inspection_id = create(lang_env)
    upload_now(lang_env, inspection_id, BACK, "BACK")
    assert wait_until(lambda: lang_env.ocr.scanned == [BACK] and not jobs.image_ocr_in_flight(UUID(inspection_id)))
    assert ocr_count(lang_env, inspection_id) == 0
    assert lang_env.client.get(f"/api/inspections/{inspection_id}").json()["status"] == "IMAGE_UPLOADED"  # not marked FAILED
    lang_env.ocr.fail_widths = set()
    lang_env.client.post(f"/api/inspections/{inspection_id}/process")
    jobs.wait_for(UUID(inspection_id))
    assert lang_env.client.get(f"/api/inspections/{inspection_id}").json()["status"] == "COMPLETED"


def test_progress_shows_one_read_step_per_photo(lang_env) -> None:
    inspection_id = create(lang_env)
    upload(lang_env, inspection_id, BACK, "BACK")
    upload(lang_env, inspection_id, DATE, "DATE_STAMP")
    lang_env.client.post(f"/api/inspections/{inspection_id}/process")
    jobs.wait_for(UUID(inspection_id))
    steps = lang_env.client.get(f"/api/inspections/{inspection_id}/progress").json()["steps"]
    reads = [step for step in steps if step["kind"] == "read"]
    assert [step["languages"] for step in reads] == [["en", "hi"], ["en"]]
    assert all(step["status"] == "done" and step["duration_ms"] is not None for step in reads)


# ------------------------------------------------------------------ drafts

def test_an_abandoned_draft_can_be_discarded_but_a_processed_inspection_cannot(lang_env) -> None:
    draft = create(lang_env)
    upload(lang_env, draft, BACK, "BACK")
    assert lang_env.client.delete(f"/api/inspections/{draft}").status_code == 204
    assert lang_env.client.get(f"/api/inspections/{draft}").status_code == 404
    assert list(lang_env.tmp.iterdir()) == []  # the stored photo went with it

    done = create(lang_env)
    upload(lang_env, done, BACK, "BACK")
    lang_env.client.post(f"/api/inspections/{done}/process")
    jobs.wait_for(UUID(done))
    assert lang_env.client.delete(f"/api/inspections/{done}").status_code == 409
