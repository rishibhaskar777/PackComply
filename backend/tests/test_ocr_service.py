from pathlib import Path
from io import BytesIO
from types import SimpleNamespace
from uuid import UUID, uuid4
import sys
from queue import Empty

from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.api.inspections import get_ocr_service
from backend.app.core.config import Settings, get_ocr_languages, get_settings
import pytest
from backend.app.core.database import get_db
from backend.app.main import app
from backend.app.models import Base, OcrResult
from backend.app.services.ocr import OcrDetection, OcrDocument
from backend.app.services.ocr import OcrProcessingError, PaddleOcrService


class LegacyEngine:
    def ocr(self, image_path: str, cls: bool):
        assert cls is True
        assert image_path.endswith("package.png")
        return [[[[[1, 2], [3, 2], [3, 4], [1, 4]], ("MRP Rs 50", 0.96)]]]


def test_ocr_service_accepts_an_injected_engine() -> None:
    service = PaddleOcrService(engine=LegacyEngine())
    document = service._run_engine(service._get_engine("en"), Path("package.png"))

    assert document[0].text == "MRP Rs 50"
    assert document[0].confidence == 0.96
    assert document[0].bbox == [[1.0, 2.0], [3.0, 2.0], [3.0, 4.0], [1.0, 4.0]]


def test_paddle_engine_enables_onednn_for_recognition_only(monkeypatch) -> None:
    received: dict[str, object] = {}

    class PaddleOCR:
        def __init__(self, **kwargs) -> None:
            received.update(kwargs)

    monkeypatch.setitem(sys.modules, "paddleocr", SimpleNamespace(PaddleOCR=PaddleOCR))

    PaddleOcrService(cpu_threads=4)._get_engine("en")

    assert received["lang"] == "en"
    # Detection models cannot use oneDNN, so the pipeline-wide flag stays off...
    assert received["enable_mkldnn"] is False
    assert received["text_recognition_batch_size"] == 8
    assert received["cpu_threads"] == 4
    # ...while the recognition sub-module opts in through the pipeline config.
    recognition = received["paddlex_config"]["SubModules"]["TextRecognition"]["engine_config"]["paddle_static"]
    assert "mkldnn_cache_capacity" in recognition and recognition["cpu_threads"] == 4


def test_onednn_can_be_disabled(monkeypatch) -> None:
    received: dict[str, object] = {}

    class PaddleOCR:
        def __init__(self, **kwargs) -> None:
            received.update(kwargs)

    monkeypatch.setitem(sys.modules, "paddleocr", SimpleNamespace(PaddleOCR=PaddleOCR))
    PaddleOcrService(enable_mkldnn=False)._get_engine("en")

    assert received["enable_mkldnn"] is False
    assert "paddlex_config" not in received
    assert "cpu_threads" not in received


def _fake_paddle_with_failing_onednn(monkeypatch, fail_at: str) -> list[bool]:
    """Fake PaddleOCR whose oneDNN variant fails at construction or at predict."""
    built: list[bool] = []

    class PaddleOCR:
        def __init__(self, **kwargs) -> None:
            self.onednn = "paddlex_config" in kwargs
            built.append(self.onednn)
            if self.onednn and fail_at == "init":
                raise RuntimeError("oneDNN unavailable")

        def predict(self, image_path: str):
            if self.onednn and fail_at == "predict":
                raise NotImplementedError("onednn_instruction not supported")
            return [{"res": {"rec_texts": ["MRP Rs 50"], "rec_scores": [0.9], "dt_polys": [[[0, 0], [1, 0], [1, 1], [0, 1]]]}}]

    monkeypatch.setitem(sys.modules, "paddleocr", SimpleNamespace(PaddleOCR=PaddleOCR))
    return built


def test_onednn_failure_at_inference_falls_back_to_standard_executor(monkeypatch) -> None:
    built = _fake_paddle_with_failing_onednn(monkeypatch, "predict")
    service = PaddleOcrService()

    first = service._predict("en", Path("a.png"))
    second = service._predict("en", Path("a.png"))

    assert first[0].text == second[0].text == "MRP Rs 50"
    assert built == [True, False]  # rebuilt once without oneDNN, then reused


def test_onednn_failure_at_engine_creation_falls_back(monkeypatch) -> None:
    built = _fake_paddle_with_failing_onednn(monkeypatch, "init")
    service = PaddleOcrService()

    assert service._predict("en", Path("a.png"))[0].text == "MRP Rs 50"
    assert built == [True, False]


def test_preprocessing_downscales_large_camera_images(tmp_path) -> None:
    image_path = tmp_path / "large.png"
    Image.new("RGB", (2400, 1200), color="white").save(image_path)

    processed = PaddleOcrService._preprocess(image_path, max_image_dimension=1200)

    try:
        assert Image.open(processed).size == (1200, 600)
    finally:
        processed.unlink(missing_ok=True)


def test_v3_prediction_parser_preserves_text_score_and_box() -> None:
    predictions = [{"res": {"rec_texts": ["Batch 10"], "rec_scores": [0.91], "dt_polys": [[[0, 0], [5, 0], [5, 2], [0, 2]]]}}]

    result = PaddleOcrService._parse_v3_predictions(predictions)

    assert result[0].text == "Batch 10"
    assert result[0].confidence == 0.91


def test_english_and_hindi_are_default_configured_languages() -> None:
    assert get_ocr_languages(Settings(ocr_languages="en,hi")) == ("en", "hi")


def test_multiple_documented_indian_ocr_languages_are_accepted() -> None:
    assert get_ocr_languages(Settings(ocr_languages="en,hi,mr,ta,te,ur,ka")) == (
        "en", "hi", "mr", "ta", "te", "ur", "ka",
    )


def test_unsupported_ocr_language_configuration_fails_clearly() -> None:
    with pytest.raises(ValueError, match="unsupported PaddleOCR 3.x language code"):
        get_ocr_languages(Settings(ocr_languages="en,bn"))


def test_duplicate_multilingual_detections_keep_best_confidence() -> None:
    duplicate_box = [[0.0, 0.0], [5.0, 0.0], [5.0, 2.0], [0.0, 2.0]]
    detections = PaddleOcrService._deduplicate([
        OcrDetection("MRP", 0.83, duplicate_box),
        OcrDetection("MRP", 0.96, duplicate_box),
        OcrDetection("मूल्य", 0.91, duplicate_box),
    ])

    assert len(detections) == 2
    assert next(item for item in detections if item.text == "MRP").confidence == 0.96


def png_bytes() -> bytes:
    image = Image.new("RGB", (4, 4), color="white")
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


class FakeOcrService:
    def scan(self, image_path: Path) -> OcrDocument:
        assert image_path.is_file()
        return OcrDocument(
            engine="PaddleOCR",
            languages=["en", "hi"],
            detections=[OcrDetection(text="MRP Rs 50", confidence=0.96, bbox=[[1, 2], [3, 2], [3, 4], [1, 4]])],
        )


def test_ocr_endpoint_stores_real_service_output(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path))
    get_settings.cache_clear()
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine)

    def override_db():
        with sessions() as session:
            yield session

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_ocr_service] = lambda: FakeOcrService()
    try:
        client = TestClient(app)
        inspection = client.post("/api/inspections", json={}).json()
        uploaded = client.post(
            f"/api/inspections/{inspection['id']}/image",
            files={"image": ("label.png", png_bytes(), "image/png")},
        ).json()

        response = client.post(f"/api/inspections/{inspection['id']}/images/{uploaded['id']}/ocr")

        assert response.status_code == 201
        assert response.json()["languages"] == ["en", "hi"]
        assert response.json()["results"][0]["text"] == "MRP Rs 50"
        with sessions() as session:
            stored = session.scalar(select(OcrResult).where(OcrResult.id == UUID(response.json()["id"])))
            assert stored is not None
            assert stored.raw_text == "MRP Rs 50"
            assert stored.detections[0]["confidence"] == 0.96
    finally:
        app.dependency_overrides.clear()
        get_settings.cache_clear()
        engine.dispose()


def test_ocr_endpoint_reports_missing_inspection_and_image(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path))
    get_settings.cache_clear()
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine)

    def override_db():
        with sessions() as session:
            yield session

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_ocr_service] = lambda: FakeOcrService()
    try:
        client = TestClient(app)
        missing_inspection = client.post(f"/api/inspections/{uuid4()}/images/{uuid4()}/ocr")
        assert missing_inspection.status_code == 404

        inspection = client.post("/api/inspections", json={}).json()
        missing_image = client.post(f"/api/inspections/{inspection['id']}/images/{uuid4()}/ocr")
        assert missing_image.status_code == 404
    finally:
        app.dependency_overrides.clear()
        get_settings.cache_clear()
        engine.dispose()


def test_ocr_endpoint_rejects_a_tampered_stored_image(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path))
    get_settings.cache_clear()
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine)

    def override_db():
        with sessions() as session:
            yield session

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_ocr_service] = lambda: FakeOcrService()
    try:
        client = TestClient(app)
        inspection = client.post("/api/inspections", json={}).json()
        uploaded = client.post(
            f"/api/inspections/{inspection['id']}/image",
            files={"image": ("label.png", png_bytes(), "image/png")},
        ).json()
        with sessions() as session:
            stored = session.get(OcrResult, uuid4())
            assert stored is None
        image_files = list(tmp_path.glob("*.png"))
        image_files[0].write_bytes(b"not an image")

        response = client.post(f"/api/inspections/{inspection['id']}/images/{uploaded['id']}/ocr")
        assert response.status_code == 422
    finally:
        app.dependency_overrides.clear()
        get_settings.cache_clear()
        engine.dispose()


class _PrimaryEngine:
    def ocr(self, image_path: str, cls: bool):
        return [[[[[1, 2], [3, 2], [3, 4], [1, 4]], ("MRP Rs 50", 0.96)]]]


class _Ticket:
    def __init__(self, detections) -> None:
        self._detections = detections

    def collect(self):
        return self._detections

    def cancel(self) -> None:
        pass


def _scannable_service(tmp_path, monkeypatch, languages=("en", "hi")) -> tuple[PaddleOcrService, Path]:
    image = tmp_path / "label.png"
    image.write_bytes(b"not-read")
    counter = iter(range(100))

    def fake_preprocess(path: Path, max_image_dimension: int = 640) -> Path:
        output = path.parent / f"processed-{next(counter)}.png"
        output.write_bytes(b"x")
        return output

    monkeypatch.setattr(PaddleOcrService, "_preprocess", staticmethod(fake_preprocess))
    service = PaddleOcrService(languages=languages)
    monkeypatch.setattr(service, "_get_engine", lambda language: _PrimaryEngine())
    return service, image


def test_failed_secondary_language_keeps_primary_result(tmp_path, monkeypatch) -> None:
    service, image = _scannable_service(tmp_path, monkeypatch)

    def fail(language: str, image_path: Path):
        raise OcrProcessingError(f"PaddleOCR {language} processing exceeded 90 seconds.")

    monkeypatch.setattr(service, "_start_secondary_language", fail)
    document = service.scan(image)

    assert [item.text for item in document.detections] == ["MRP Rs 50"]
    assert document.languages == ["en"]
    assert document.skipped_languages == ["hi"]


def test_successful_secondary_language_is_reported(tmp_path, monkeypatch) -> None:
    service, image = _scannable_service(tmp_path, monkeypatch)
    monkeypatch.setattr(service, "_start_secondary_language", lambda language, path: _Ticket([OcrDetection("मूल्य", 0.9, None)]))
    document = service.scan(image)

    assert document.languages == ["en", "hi"]
    assert document.skipped_languages == []
    assert len(document.detections) == 2


def test_failed_primary_language_still_fails_the_scan(tmp_path, monkeypatch) -> None:
    service, image = _scannable_service(tmp_path, monkeypatch)

    class Broken:
        def ocr(self, image_path: str, cls: bool):
            raise OcrProcessingError("primary failed")

    monkeypatch.setattr(service, "_get_engine", lambda language: Broken())
    monkeypatch.setattr(service, "_start_secondary_language", lambda language, path: _Ticket([]))
    with pytest.raises(OcrProcessingError):
        service.scan(image)


def test_optional_model_names_are_only_passed_when_configured(monkeypatch) -> None:
    received: dict[str, object] = {}

    class PaddleOCR:
        def __init__(self, **kwargs) -> None:
            received.update(kwargs)

    monkeypatch.setitem(sys.modules, "paddleocr", SimpleNamespace(PaddleOCR=PaddleOCR))
    PaddleOcrService()._get_engine("en")
    assert "text_detection_model_name" not in received
    received.clear()
    PaddleOcrService(detection_model="PP-OCRv5_mobile_det")._get_engine("en")
    assert received["text_detection_model_name"] == "PP-OCRv5_mobile_det"


# ---------------------------------------------------------------- workers

class _FakeProcess:
    instances: list["_FakeProcess"] = []

    def __init__(self, target, args, daemon) -> None:
        self.pid = len(_FakeProcess.instances) + 1
        self._alive = False
        self.starts = 0
        _FakeProcess.instances.append(self)

    def start(self) -> None:
        self._alive = True
        self.starts += 1

    def is_alive(self) -> bool:
        return self._alive

    def terminate(self) -> None:
        self._alive = False

    def join(self, timeout=None) -> None:
        pass


class _FakeQueue:
    def __init__(self) -> None:
        self.items: list = []

    def put(self, item) -> None:
        self.items.append(item)

    def get(self, timeout=None):
        if not self.items:
            raise Empty
        return self.items.pop(0)

    def close(self) -> None:
        pass


def _install_fake_multiprocessing(monkeypatch) -> None:
    _FakeProcess.instances = []
    context = SimpleNamespace(
        Queue=_FakeQueue,
        Process=lambda target, args, daemon: _FakeProcess(target, args, daemon),
    )
    monkeypatch.setattr("backend.app.services.ocr.ocr_service.multiprocessing.get_context", lambda name: context)


def _answer_requests(worker, detections) -> None:
    """Play the worker process: answer everything queued so far."""
    while worker._requests.items:
        job_id, _path = worker._requests.items.pop(0)
        worker._responses.put((job_id, "ok", detections))


def test_secondary_worker_is_reused_across_scans(monkeypatch) -> None:
    from backend.app.services.ocr.ocr_service import _SecondaryWorker

    _install_fake_multiprocessing(monkeypatch)
    worker = _SecondaryWorker("hi", {})

    first = worker.submit(Path("a.png"))
    _answer_requests(worker, [OcrDetection("मूल्य", 0.9, None)])
    assert worker.collect(first, 1)[0].text == "मूल्य"

    second = worker.submit(Path("b.png"))
    _answer_requests(worker, [OcrDetection("शुद्ध", 0.8, None)])
    assert worker.collect(second, 1)[0].text == "शुद्ध"

    assert len(_FakeProcess.instances) == 1  # one process served both scans
    assert _FakeProcess.instances[0].starts == 1


def test_secondary_worker_restarts_after_crash(monkeypatch) -> None:
    from backend.app.services.ocr.ocr_service import _SecondaryWorker

    _install_fake_multiprocessing(monkeypatch)
    worker = _SecondaryWorker("hi", {})

    job = worker.submit(Path("a.png"))
    _FakeProcess.instances[0]._alive = False  # simulated crash before replying
    with pytest.raises(OcrProcessingError, match="exited without a result"):
        worker.collect(job, 5)

    job = worker.submit(Path("b.png"))
    _answer_requests(worker, [OcrDetection("मूल्य", 0.9, None)])
    assert worker.collect(job, 1)[0].text == "मूल्य"
    assert len(_FakeProcess.instances) == 2  # a fresh process replaced the dead one


def test_secondary_worker_timeout_terminates_and_restarts(monkeypatch) -> None:
    from backend.app.services.ocr.ocr_service import _SecondaryWorker

    _install_fake_multiprocessing(monkeypatch)
    worker = _SecondaryWorker("hi", {})

    job = worker.submit(Path("a.png"))
    with pytest.raises(OcrProcessingError, match="exceeded"):
        worker.collect(job, 0.2)
    assert not _FakeProcess.instances[0].is_alive()

    worker.submit(Path("b.png"))
    assert len(_FakeProcess.instances) == 2


def test_service_scan_uses_the_same_worker_for_two_scans(tmp_path, monkeypatch) -> None:
    from backend.app.services.ocr import ocr_service as module

    _install_fake_multiprocessing(monkeypatch)
    service, image = _scannable_service(tmp_path, monkeypatch)
    original = module._SecondaryWorker.collect

    def collect(self, job_id, timeout):
        _answer_requests(self, [OcrDetection("मूल्य", 0.9, None)])
        return original(self, job_id, timeout)

    monkeypatch.setattr(module._SecondaryWorker, "collect", collect)

    first = service.scan(image)
    image.write_bytes(b"not-read")  # scan consumes nothing, but keep the file present
    second = service.scan(image)

    assert first.languages == second.languages == ["en", "hi"]
    assert len(_FakeProcess.instances) == 1
    assert _FakeProcess.instances[0].starts == 1


def test_startup_warmup_does_not_block_health(monkeypatch) -> None:
    import threading
    import time

    started = threading.Event()
    release = threading.Event()

    class SlowService:
        def warm(self) -> None:
            started.set()
            release.wait(10)

    monkeypatch.setenv("OCR_WARMUP_ON_STARTUP", "true")
    get_settings.cache_clear()
    app.dependency_overrides[get_ocr_service] = lambda: SlowService()
    try:
        began = time.perf_counter()
        with TestClient(app) as client:
            response = client.get("/api/health")
            elapsed = time.perf_counter() - began
            assert started.wait(5)  # warming began in the background
            assert response.status_code == 200
            assert elapsed < 5  # startup and health did not wait for warm-up
            release.set()
    finally:
        release.set()
        app.dependency_overrides.clear()
        get_settings.cache_clear()


# Cross-language de-duplication ----------------------------------------------

def _box(x0: float, y0: float, x1: float, y1: float) -> list[list[float]]:
    return [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]


def test_secondary_rereads_of_primary_text_are_dropped() -> None:
    from backend.app.services.ocr.ocr_service import filter_secondary_detections

    primary = [OcrDetection("NET WEIGHT:", 0.99, _box(599, 313, 689, 333), "en"), OcrDetection("70g", 0.99, _box(682, 308, 732, 336), "en")]
    secondary = [
        OcrDetection("NET WEIGHT:70", 0.98, _box(599, 311, 711, 334), "hi"),   # IoU 0.7 with a primary box
        OcrDetection("Te", 0.6, _box(600, 315, 620, 330), "hi"),              # fragment inside a primary box
        OcrDetection("शुद्ध वजन", 0.9, _box(599, 313, 689, 333), "hi"),       # Devanagari: new information
        OcrDetection("Batch 7", 0.9, _box(10, 500, 80, 520), "hi"),           # no primary box there
        OcrDetection("no box", 0.9, None, "hi"),
    ]

    kept, dropped = filter_secondary_detections(primary, secondary, "hi")

    assert [item.text for item in kept] == ["शुद्ध वजन", "Batch 7", "no box"]
    assert dropped == 2


def test_script_check_follows_the_secondary_language() -> None:
    from backend.app.services.ocr.ocr_service import filter_secondary_detections, has_script

    assert has_script("மொத்த எடை", "ta") and not has_script("मूल्य", "ta") and not has_script("MRP", "hi")
    primary = [OcrDetection("Net Wt", 0.9, _box(0, 0, 100, 20), "en")]
    kept, dropped = filter_secondary_detections(primary, [OcrDetection("मूल्य", 0.9, _box(0, 0, 100, 20), "ta")], "ta")
    assert kept == [] and dropped == 1


def test_scan_tags_languages_and_drops_cross_language_duplicates(tmp_path, monkeypatch, caplog) -> None:
    service, image = _scannable_service(tmp_path, monkeypatch)
    primary_box = [[1.0, 2.0], [3.0, 2.0], [3.0, 4.0], [1.0, 4.0]]
    monkeypatch.setattr(service, "_start_secondary_language", lambda language, path: _Ticket([
        OcrDetection("MRP Rs 5O", 0.7, primary_box), OcrDetection("मूल्य", 0.9, primary_box), OcrDetection("Lot 9", 0.9, _box(50, 50, 60, 60)),
    ]))

    with caplog.at_level("INFO", logger="uvicorn.error"):
        document = service.scan(image)

    assert [(item.text, item.language) for item in document.detections] == [("MRP Rs 50", "en"), ("मूल्य", "hi"), ("Lot 9", "hi")]
    assert document.secondary_duplicates_dropped == 1
    assert document.as_database_detections()[0]["language"] == "en"
    assert "cross-language de-duplication language=hi kept=2 dropped=1" in caplog.text
