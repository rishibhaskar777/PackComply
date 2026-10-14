"""PaddleOCR adapter.

Keeping this adapter separate means a future OCR provider can implement the
same `scan` method without changing routes or database persistence.
"""

from dataclasses import asdict, dataclass, field
import json
import logging
import multiprocessing
import os
from pathlib import Path
import tempfile
from threading import RLock
from time import perf_counter
from typing import Any
from queue import Empty


# Uvicorn configures this logger at INFO, unlike the process root logger.
logger = logging.getLogger("uvicorn.error")


class OcrInitializationError(RuntimeError):
    """PaddleOCR or its model assets could not be initialized."""


class OcrProcessingError(RuntimeError):
    """A valid image could not be preprocessed or read by the OCR engine."""


def _scan_secondary_language(
    language: str,
    image_path: str,
    max_image_dimension: int,
    recognition_batch_size: int,
    result_queue: Any,
) -> None:
    """Run a secondary PaddleOCR language in a fresh Windows process.

    PaddleOCR 3.7's English and Devanagari predictors can deadlock when both
    have executed in the same Windows Python process.  A spawned worker keeps
    the genuine cached Paddle model while isolating its native runtime state.
    """
    try:
        service = PaddleOcrService(
            languages=(language,),
            max_image_dimension=max_image_dimension,
            recognition_batch_size=recognition_batch_size,
        )
        result_queue.put(("ok", service.scan(Path(image_path)).detections))
    except Exception as error:
        result_queue.put(("error", str(error)))


@dataclass(frozen=True)
class OcrDetection:
    text: str
    confidence: float | None
    bbox: list[list[float]] | None


@dataclass(frozen=True)
class OcrDocument:
    engine: str
    languages: list[str]
    detections: list[OcrDetection]
    skipped_languages: list[str] = field(default_factory=list)

    @property
    def raw_text(self) -> str:
        return "\n".join(item.text for item in self.detections)

    def as_database_detections(self) -> list[dict[str, Any]]:
        return [asdict(item) for item in self.detections]


class PaddleOcrService:
    """Run PaddleOCR after a small contrast/noise preprocessing step."""

    engine_name = "PaddleOCR"

    def __init__(
        self,
        languages: tuple[str, ...] = ("en", "hi"),
        engine: Any | None = None,
        max_image_dimension: int = 640,
        secondary_language_max_image_dimension: int = 320,
        secondary_language_timeout_seconds: int = 45,
        recognition_batch_size: int = 8,
        detection_model: str = "",
        recognition_model: str = "",
    ) -> None:
        self.languages = languages
        self._engine = engine
        self._engines: dict[str, Any] = {}
        self.max_image_dimension = max_image_dimension
        self.secondary_language_max_image_dimension = secondary_language_max_image_dimension
        self.secondary_language_timeout_seconds = secondary_language_timeout_seconds
        self.recognition_batch_size = recognition_batch_size
        self.detection_model = detection_model.strip()
        self.recognition_model = recognition_model.strip()
        # Paddle/PaddleX model construction and inference are not safe to run
        # concurrently against one in-memory predictor on Windows.
        self._engine_lock = RLock()

    def _get_engine(self, language: str) -> Any:
        if self._engine is not None:
            return self._engine
        with self._engine_lock:
            if language in self._engines:
                logger.info("OCR engine reuse language=%s", language)
                return self._engines[language]
            started = perf_counter()
            logger.info("OCR initializing engine language=%s", language)
            try:
                # Keep model downloads and file locks inside the application
                # workspace. The upstream default is the user's home directory.
                os.environ.setdefault("PADDLE_PDX_CACHE_HOME", str(Path(".paddlex").resolve()))
                # Skip the per-start network probe of model hosting sources.
                os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")
                from paddleocr import PaddleOCR

                # Optional explicit model names; empty keeps PaddleOCR's per-language default.
                model_options: dict[str, str] = {}
                if self.detection_model:
                    model_options["text_detection_model_name"] = self.detection_model
                if self.recognition_model:
                    model_options["text_recognition_model_name"] = self.recognition_model
                # PaddleOCR 3 uses the unified predict interface.
                self._engines[language] = PaddleOCR(
                    lang=language,
                    **model_options,
                    use_doc_orientation_classify=False,
                    use_doc_unwarping=False,
                    use_textline_orientation=False,
                    text_recognition_batch_size=self.recognition_batch_size,
                    # Paddle's oneDNN CPU executor currently raises an
                    # ``NotImplementedError`` on Windows for the bundled OCR
                    # detection model, so use the compatible standard executor.
                    enable_mkldnn=False,
                )
            except ImportError as error:
                logger.exception("OCR engine import failed language=%s", language)
                raise OcrInitializationError("PaddleOCR is not installed on this server.") from error
            except Exception as error:
                logger.exception("OCR engine initialization failed language=%s", language)
                raise OcrInitializationError("PaddleOCR could not be initialized.") from error
            logger.info("OCR engine initialized language=%s elapsed_seconds=%.3f", language, perf_counter() - started)
            return self._engines[language]

    def scan(self, image_path: Path) -> OcrDocument:
        processed_paths: list[Path] = []
        started = perf_counter()
        logger.info("OCR request started image_path=%s languages=%s", image_path, ",".join(self.languages))
        try:
            if not image_path.is_absolute() or not image_path.is_file():
                raise OcrProcessingError("The uploaded image path is unavailable.")
            logger.info("OCR image path resolved path=%s bytes=%d", image_path, image_path.stat().st_size)
            preprocess_started = perf_counter()
            processed_path = self._preprocess(image_path, self.max_image_dimension)
            processed_paths.append(processed_path)
            logger.info("OCR preprocessing completed path=%s elapsed_seconds=%.3f", processed_path, perf_counter() - preprocess_started)
            detections: list[OcrDetection] = []
            completed_languages: list[str] = []
            skipped_languages: list[str] = []
            for index, language in enumerate(self.languages):
                language_image_path = processed_path
                if index and self.secondary_language_max_image_dimension < self.max_image_dimension:
                    secondary_started = perf_counter()
                    language_image_path = self._preprocess(image_path, self.secondary_language_max_image_dimension)
                    processed_paths.append(language_image_path)
                    logger.info("OCR secondary preprocessing completed language=%s path=%s elapsed_seconds=%.3f", language, language_image_path, perf_counter() - secondary_started)
                inference_started = perf_counter()
                logger.info("OCR inference started language=%s", language)
                if index and self._engine is None:
                    try:
                        language_detections = self._run_secondary_language(language, language_image_path)
                    except OcrProcessingError as error:
                        # A secondary language is best-effort: keep the primary result.
                        logger.warning("OCR secondary language skipped language=%s reason=%s", language, error)
                        skipped_languages.append(language)
                        continue
                else:
                    engine = self._get_engine(language)
                    language_detections = self._run_engine(engine, language_image_path)
                logger.info("OCR inference completed language=%s detections=%d elapsed_seconds=%.3f", language, len(language_detections), perf_counter() - inference_started)
                detections.extend(language_detections)
                completed_languages.append(language)
            parse_started = perf_counter()
            deduplicated = self._deduplicate(detections)
            logger.info("OCR result conversion completed detections=%d elapsed_seconds=%.3f", len(deduplicated), perf_counter() - parse_started)
            return OcrDocument(
                engine=self.engine_name,
                languages=completed_languages,
                detections=deduplicated,
                skipped_languages=skipped_languages,
            )
        except OcrInitializationError:
            raise
        except Exception as error:
            logger.exception("OCR request failed image_path=%s", image_path)
            raise OcrProcessingError("PaddleOCR could not process this image.") from error
        finally:
            for processed_path in processed_paths:
                if processed_path.exists():
                    processed_path.unlink()
            logger.info("OCR request completed image_path=%s elapsed_seconds=%.3f", image_path, perf_counter() - started)

    def _run_secondary_language(self, language: str, image_path: Path) -> list[OcrDetection]:
        context = multiprocessing.get_context("spawn")
        result_queue = context.Queue()
        worker = context.Process(
            target=_scan_secondary_language,
            args=(language, str(image_path), self.secondary_language_max_image_dimension, self.recognition_batch_size, result_queue),
        )
        worker.start()
        worker.join(self.secondary_language_timeout_seconds)
        if worker.is_alive():
            worker.terminate()
            worker.join()
            raise OcrProcessingError(f"PaddleOCR {language} processing exceeded {self.secondary_language_timeout_seconds} seconds.")
        try:
            state, payload = result_queue.get(timeout=1)
        except Empty as error:
            raise OcrProcessingError(f"PaddleOCR {language} worker exited without a result.") from error
        finally:
            result_queue.close()
        if state != "ok":
            raise OcrProcessingError(f"PaddleOCR {language} worker failed: {payload}")
        return payload

    @staticmethod
    def _preprocess(image_path: Path, max_image_dimension: int = 640) -> Path:
        """Create a bounded-size grayscale, denoised copy for real PaddleOCR."""
        try:
            import cv2
        except ImportError as error:
            raise OcrInitializationError("OpenCV is not installed on this server.") from error

        image = cv2.imread(str(image_path))
        if image is None:
            raise OcrProcessingError("The stored image cannot be read.")
        if max_image_dimension < 1:
            raise OcrProcessingError("OCR image dimension limit must be positive.")
        height, width = image.shape[:2]
        longest_edge = max(height, width)
        if longest_edge > max_image_dimension:
            scale = max_image_dimension / longest_edge
            image = cv2.resize(
                image,
                (round(width * scale), round(height * scale)),
                interpolation=cv2.INTER_AREA,
            )
        grayscale = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        denoised = cv2.fastNlMeansDenoising(grayscale, None, 10, 7, 21)
        normalized = cv2.normalize(denoised, None, 0, 255, cv2.NORM_MINMAX)
        with tempfile.NamedTemporaryFile(suffix=".png", dir=image_path.parent, delete=False) as temporary:
            output = Path(temporary.name)
        if not cv2.imwrite(str(output), normalized):
            output.unlink(missing_ok=True)
            raise OcrProcessingError("The preprocessed image could not be created.")
        return output

    def _run_engine(self, engine: Any, image_path: Path) -> list[OcrDetection]:
        if hasattr(engine, "predict"):
            return self._parse_v3_predictions(engine.predict(str(image_path)))
        if hasattr(engine, "ocr"):
            return self._parse_legacy_predictions(engine.ocr(str(image_path), cls=True))
        raise OcrInitializationError("The configured OCR engine has no supported prediction method.")

    @staticmethod
    def _parse_legacy_predictions(predictions: Any) -> list[OcrDetection]:
        detections: list[OcrDetection] = []
        for page in predictions or []:
            for item in page or []:
                bbox, (text, confidence) = item
                detections.append(OcrDetection(str(text), float(confidence), PaddleOcrService._bbox(bbox)))
        return detections

    @staticmethod
    def _parse_v3_predictions(predictions: Any) -> list[OcrDetection]:
        detections: list[OcrDetection] = []
        for prediction in predictions:
            payload = PaddleOcrService._payload(prediction)
            result = payload.get("res", payload)
            texts = result.get("rec_texts", [])
            confidences = result.get("rec_scores", [])
            boxes = result.get("dt_polys", [])
            for index, text in enumerate(texts):
                confidence = confidences[index] if index < len(confidences) else None
                bbox = boxes[index] if index < len(boxes) else None
                detections.append(OcrDetection(str(text), float(confidence) if confidence is not None else None, PaddleOcrService._bbox(bbox)))
        return detections

    @staticmethod
    def _payload(prediction: Any) -> dict[str, Any]:
        candidate = getattr(prediction, "json", prediction)
        candidate = candidate() if callable(candidate) else candidate
        if isinstance(candidate, str):
            candidate = json.loads(candidate)
        if not isinstance(candidate, dict):
            raise OcrProcessingError("PaddleOCR returned an unsupported result format.")
        return candidate

    @staticmethod
    def _bbox(value: Any) -> list[list[float]] | None:
        if value is None:
            return None
        return [[float(coordinate) for coordinate in point] for point in value]

    @staticmethod
    def _deduplicate(detections: list[OcrDetection]) -> list[OcrDetection]:
        """Keep the highest-confidence copy of an identical multi-pass detection."""
        unique: dict[tuple[str, tuple[tuple[float, ...], ...] | None], OcrDetection] = {}
        for detection in detections:
            box_key = tuple(tuple(round(value, 2) for value in point) for point in detection.bbox) if detection.bbox else None
            key = (detection.text.strip(), box_key)
            previous = unique.get(key)
            if previous is None or (detection.confidence or 0) > (previous.confidence or 0):
                unique[key] = detection
        return list(unique.values())
