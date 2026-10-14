from pathlib import Path
from io import BytesIO
from types import SimpleNamespace
from uuid import UUID, uuid4
import sys

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


def test_paddle_engine_disables_onednn_for_compatible_cpu_ocr(monkeypatch) -> None:
    received: dict[str, object] = {}

    class PaddleOCR:
        def __init__(self, **kwargs) -> None:
            received.update(kwargs)

    monkeypatch.setitem(sys.modules, "paddleocr", SimpleNamespace(PaddleOCR=PaddleOCR))

    PaddleOcrService()._get_engine("en")

    assert received["lang"] == "en"
    assert received["enable_mkldnn"] is False
    assert received["text_recognition_batch_size"] == 8


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

    monkeypatch.setattr(service, "_run_secondary_language", fail)
    document = service.scan(image)

    assert [item.text for item in document.detections] == ["MRP Rs 50"]
    assert document.languages == ["en"]
    assert document.skipped_languages == ["hi"]


def test_successful_secondary_language_is_reported(tmp_path, monkeypatch) -> None:
    service, image = _scannable_service(tmp_path, monkeypatch)
    monkeypatch.setattr(service, "_run_secondary_language", lambda language, path: [OcrDetection("मूल्य", 0.9, None)])
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
    monkeypatch.setattr(service, "_run_secondary_language", lambda language, path: [])
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
