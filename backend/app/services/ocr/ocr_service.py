"""PaddleOCR adapter.

Keeping this adapter separate means a future OCR provider can implement the
same `scan` method without changing routes or database persistence.
"""

from dataclasses import asdict, dataclass, field, replace
import json
import logging
import multiprocessing
import os
from pathlib import Path
import re
import tempfile
from threading import RLock
from time import perf_counter
from typing import Any
from queue import Empty

from backend.app.services.uploads import image_dimensions


# Uvicorn configures this logger at INFO, unlike the process root logger.
logger = logging.getLogger("uvicorn.error")

# Library defaults, mirrored so a custom recognition engine_config keeps them.
_DEFAULT_CPU_THREADS = 10
_DEFAULT_MKLDNN_CACHE_CAPACITY = 10


class OcrInitializationError(RuntimeError):
    """PaddleOCR or its model assets could not be initialized."""


class OcrProcessingError(RuntimeError):
    """A valid image could not be preprocessed or read by the OCR engine."""


@dataclass(frozen=True)
class OcrDetection:
    text: str
    confidence: float | None
    bbox: list[list[float]] | None
    # Recognition language that produced this text; None for legacy/injected results.
    language: str | None = None


# Unicode block of each configured language's native script.  A secondary
# recogniser also re-reads Latin text, so only output in its own script is new.
_DEVANAGARI = "ऀ-ॿ"
LANGUAGE_SCRIPTS = {
    "hi": _DEVANAGARI, "mr": _DEVANAGARI, "ne": _DEVANAGARI, "sa": _DEVANAGARI, "mai": _DEVANAGARI, "gom": _DEVANAGARI,
    "ta": "஀-௿", "te": "ఀ-౿", "ka": "ಀ-೿", "ur": "؀-ۿݐ-ݿ",
}
# A secondary box overlapping a primary box at least this much is a re-read.
SECONDARY_IOU_THRESHOLD = 0.3
# ...as is a secondary fragment lying mostly inside one primary box.
SECONDARY_CONTAINMENT_THRESHOLD = 0.6


def bbox_extent(bbox: list[list[float]] | None) -> tuple[float, float, float, float] | None:
    """Axis-aligned (x0, y0, x1, y1) of a polygon box."""
    if not bbox:
        return None
    xs = [point[0] for point in bbox]
    ys = [point[1] for point in bbox]
    return min(xs), min(ys), max(xs), max(ys)


def _area(extent: tuple[float, float, float, float]) -> float:
    return max(extent[2] - extent[0], 0.0) * max(extent[3] - extent[1], 0.0)


def _intersection(first: tuple[float, float, float, float], second: tuple[float, float, float, float]) -> float:
    return max(min(first[2], second[2]) - max(first[0], second[0]), 0.0) * max(min(first[3], second[3]) - max(first[1], second[1]), 0.0)


def bbox_iou(first: list[list[float]] | None, second: list[list[float]] | None) -> float:
    a, b = bbox_extent(first), bbox_extent(second)
    if a is None or b is None:
        return 0.0
    shared = _intersection(a, b)
    union = _area(a) + _area(b) - shared
    return shared / union if union > 0 else 0.0


def bbox_containment(inner: list[list[float]] | None, outer: list[list[float]] | None) -> float:
    """Share of ``inner``'s area that lies inside ``outer``."""
    a, b = bbox_extent(inner), bbox_extent(outer)
    if a is None or b is None or _area(a) <= 0:
        return 0.0
    return _intersection(a, b) / _area(a)


def has_script(text: str, language: str | None) -> bool:
    script = LANGUAGE_SCRIPTS.get(language or "")
    return bool(script and re.search(f"[{script}]", text))


def overlaps_any(detection: OcrDetection, others: list[OcrDetection]) -> bool:
    return any(
        bbox_iou(detection.bbox, other.bbox) >= SECONDARY_IOU_THRESHOLD
        or bbox_containment(detection.bbox, other.bbox) >= SECONDARY_CONTAINMENT_THRESHOLD
        for other in others
    )


def filter_secondary_detections(primary: list[OcrDetection], secondary: list[OcrDetection], language: str) -> tuple[list[OcrDetection], int]:
    """Drop a secondary language's re-reads of text the primary language already read.

    A secondary detection is kept when it contains characters of that
    language's own script, or when its box does not overlap any primary box.
    Returns the kept detections and how many were dropped.
    """
    kept = [item for item in secondary if has_script(item.text, language) or not item.bbox or not overlaps_any(item, primary)]
    return kept, len(secondary) - len(kept)


@dataclass(frozen=True)
class OcrDocument:
    engine: str
    languages: list[str]
    detections: list[OcrDetection]
    skipped_languages: list[str] = field(default_factory=list)
    secondary_duplicates_dropped: int = 0

    @property
    def raw_text(self) -> str:
        return "\n".join(item.text for item in self.detections)

    def as_database_detections(self) -> list[dict[str, Any]]:
        return [asdict(item) for item in self.detections]


def _secondary_worker_main(language: str, options: dict[str, Any], requests: Any, responses: Any) -> None:
    """Long-lived worker process for one secondary OCR language.

    PaddleOCR 3.7's English and Devanagari predictors can deadlock when both
    have executed in the same Windows Python process.  A dedicated process
    keeps each language's native runtime state isolated while loading its
    models only once.  The service is built lazily so a failed model load is
    reported against the request that triggered it.
    """
    service: PaddleOcrService | None = None
    while True:
        request = requests.get()
        if request is None:
            return
        job_id, image_path = request
        try:
            if service is None:
                service = PaddleOcrService(languages=(language,), **options)
            responses.put((job_id, "ok", service._predict(language, Path(image_path))))
        except Exception as error:
            responses.put((job_id, "error", str(error)))


class _SecondaryWorker:
    """Parent-side handle to one worker; started lazily, restarted when dead or timed out."""

    def __init__(self, language: str, options: dict[str, Any]) -> None:
        self.language = language
        self._options = options
        self._process: Any = None
        self._requests: Any = None
        self._responses: Any = None
        self._next_job = 0
        # Held from submit to collect so concurrent scans cannot interleave replies.
        self.lock = RLock()

    def alive(self) -> bool:
        return self._process is not None and self._process.is_alive()

    def _start(self) -> None:
        self.stop()
        context = multiprocessing.get_context("spawn")
        self._requests = context.Queue()
        self._responses = context.Queue()
        self._process = context.Process(
            target=_secondary_worker_main,
            args=(self.language, self._options, self._requests, self._responses),
            daemon=True,
        )
        self._process.start()
        logger.info("OCR worker started language=%s pid=%s", self.language, self._process.pid)

    def submit(self, image_path: Path) -> int:
        if not self.alive():
            self._start()
        self._next_job += 1
        self._requests.put((self._next_job, str(image_path)))
        return self._next_job

    def collect(self, job_id: int, timeout: float) -> list[OcrDetection]:
        deadline = perf_counter() + timeout
        while True:
            remaining = deadline - perf_counter()
            if remaining <= 0:
                self.stop()
                raise OcrProcessingError(f"PaddleOCR {self.language} processing exceeded {timeout:.0f} seconds.")
            try:
                received_id, state, payload = self._responses.get(timeout=min(remaining, 0.5))
            except Empty:
                if not self.alive():
                    self.stop()
                    raise OcrProcessingError(f"PaddleOCR {self.language} worker exited without a result.")
                continue
            if received_id != job_id:
                continue  # stale reply from an abandoned request
            if state != "ok":
                raise OcrProcessingError(f"PaddleOCR {self.language} worker failed: {payload}")
            return payload

    def stop(self) -> None:
        process, self._process = self._process, None
        if process is not None and process.is_alive():
            process.terminate()
            process.join(5)
        for queue in (self._requests, self._responses):
            if queue is not None:
                queue.close()
        self._requests = self._responses = None


class _SecondaryTicket:
    """A submitted secondary-language request.

    Holds the worker lock until `collect` or `cancel`, so one worker serves one
    scan at a time while the primary language runs concurrently in-process.
    """

    def __init__(self, worker: _SecondaryWorker, image_path: Path, timeout: float) -> None:
        self.language = worker.language
        self._worker = worker
        self._timeout = timeout
        worker.lock.acquire()
        self._held = True
        self._started = perf_counter()
        try:
            self._job = worker.submit(image_path)
        except Exception:
            self.cancel()
            raise

    def collect(self) -> list[OcrDetection]:
        try:
            # The worker has been running while the primary language ran, so
            # count the timeout from submission but always allow a short grace.
            remaining = max(self._timeout - (perf_counter() - self._started), 1.0)
            return self._worker.collect(self._job, remaining)
        finally:
            self.cancel()

    def cancel(self) -> None:
        if self._held:
            self._held = False
            self._worker.lock.release()


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
        enable_mkldnn: bool = True,
        cpu_threads: int = 0,
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
        self.enable_mkldnn = enable_mkldnn
        self.cpu_threads = cpu_threads
        # Languages whose engine currently runs recognition on oneDNN, and
        # languages that failed with it and were rebuilt without it.
        self._mkldnn_active: set[str] = set()
        self._mkldnn_disabled: set[str] = set()
        self._workers: dict[str, _SecondaryWorker] = {}
        # Paddle/PaddleX model construction and inference are not safe to run
        # concurrently against one in-memory predictor on Windows.
        self._engine_lock = RLock()
        # Separate so locating a worker never waits behind a running inference.
        self._worker_lock = RLock()

    # ------------------------------------------------------------------ engine

    def _recognition_mkldnn_config(self) -> dict[str, Any] | None:
        """PaddleX pipeline config that enables oneDNN for text recognition only.

        Measured on paddlepaddle 3.3.1 / Windows CPU: recognition models run
        ~2.4x faster with oneDNN, while every detection model raises
        ``NotImplementedError`` in the oneDNN executor.  The pipeline-wide flag
        therefore stays off and only the recognition sub-module opts in.
        """
        try:
            from paddlex.inference import load_pipeline_config

            config = load_pipeline_config("OCR")
            config["SubModules"]["TextRecognition"]["engine_config"] = {
                "paddle_static": {
                    "mkldnn_cache_capacity": _DEFAULT_MKLDNN_CACHE_CAPACITY,
                    "cpu_threads": self.cpu_threads or _DEFAULT_CPU_THREADS,
                    "enable_cinn": False,
                }
            }
            return config
        except Exception:
            logger.warning("OCR oneDNN recognition config unavailable; continuing without oneDNN", exc_info=True)
            return None

    def _build_engine(self, language: str, use_mkldnn: bool) -> Any:
        from paddleocr import PaddleOCR

        # Optional explicit model names; empty keeps PaddleOCR's per-language default.
        options: dict[str, Any] = {}
        if self.detection_model:
            options["text_detection_model_name"] = self.detection_model
        if self.recognition_model:
            options["text_recognition_model_name"] = self.recognition_model
        if self.cpu_threads > 0:
            options["cpu_threads"] = self.cpu_threads
        if use_mkldnn:
            config = self._recognition_mkldnn_config()
            if config is not None:
                options["paddlex_config"] = config
            else:
                use_mkldnn = False
        # PaddleOCR 3 uses the unified predict interface.
        engine = PaddleOCR(
            lang=language,
            **options,
            use_doc_orientation_classify=False,
            use_doc_unwarping=False,
            use_textline_orientation=False,
            text_recognition_batch_size=self.recognition_batch_size,
            # Pipeline-wide oneDNN stays off: Paddle's oneDNN executor raises
            # NotImplementedError for every detection model on Windows.
            enable_mkldnn=False,
        )
        if use_mkldnn:
            self._mkldnn_active.add(language)
        else:
            self._mkldnn_active.discard(language)
        return engine

    def _get_engine(self, language: str) -> Any:
        if self._engine is not None:
            return self._engine
        with self._engine_lock:
            if language in self._engines:
                logger.info("OCR engine reuse language=%s", language)
                return self._engines[language]
            started = perf_counter()
            logger.info("OCR initializing engine language=%s", language)
            # Keep model downloads and file locks inside the application
            # workspace. The upstream default is the user's home directory.
            os.environ.setdefault("PADDLE_PDX_CACHE_HOME", str(Path(".paddlex").resolve()))
            # Skip the per-start network probe of model hosting sources.
            os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")
            try:
                use_mkldnn = self.enable_mkldnn and language not in self._mkldnn_disabled
                try:
                    engine = self._build_engine(language, use_mkldnn)
                except ImportError:
                    raise
                except Exception:
                    if not use_mkldnn:
                        raise
                    logger.warning("OCR engine creation failed with oneDNN; retrying without it language=%s", language, exc_info=True)
                    self._mkldnn_disabled.add(language)
                    engine = self._build_engine(language, False)
                self._engines[language] = engine
            except ImportError as error:
                logger.exception("OCR engine import failed language=%s", language)
                raise OcrInitializationError("PaddleOCR is not installed on this server.") from error
            except Exception as error:
                logger.exception("OCR engine initialization failed language=%s", language)
                raise OcrInitializationError("PaddleOCR could not be initialized.") from error
            logger.info(
                "OCR engine initialized language=%s mkldnn_recognition=%s elapsed_seconds=%.3f",
                language, language in self._mkldnn_active, perf_counter() - started,
            )
            return self._engines[language]

    def _predict(self, language: str, image_path: Path) -> list[OcrDetection]:
        """Run one language, rebuilding the engine without oneDNN if it fails there."""
        with self._engine_lock:
            engine = self._get_engine(language)
            try:
                return self._run_engine(engine, image_path)
            except OcrProcessingError:
                raise
            except (NotImplementedError, RuntimeError) as error:
                if self._engine is not None or language not in self._mkldnn_active:
                    raise
                logger.warning("OCR inference failed with oneDNN; rebuilding without it language=%s reason=%s", language, error)
                self._mkldnn_disabled.add(language)
                self._engines.pop(language, None)
                return self._run_engine(self._get_engine(language), image_path)

    # -------------------------------------------------------------------- scan

    def scan(self, image_path: Path) -> OcrDocument:
        processed_paths: list[Path] = []
        tickets: list[_SecondaryTicket] = []
        started = perf_counter()
        logger.info("OCR request started image_path=%s languages=%s", image_path, ",".join(self.languages))
        try:
            if not image_path.is_absolute() or not image_path.is_file():
                raise OcrProcessingError("The uploaded image path is unavailable.")
            logger.info("OCR image path resolved path=%s bytes=%d", image_path, image_path.stat().st_size)
            preprocess_started = perf_counter()
            processed_path = self._preprocess(image_path, self.max_image_dimension)
            processed_paths.append(processed_path)
            # Boxes are reported in each pass's downscaled space; map them back
            # so every stored box uses original (EXIF-oriented) image pixels.
            original_size = image_dimensions(image_path)
            logger.info("OCR preprocessing completed path=%s elapsed_seconds=%.3f", processed_path, perf_counter() - preprocess_started)
            detections: list[OcrDetection] = []
            completed_languages: list[str] = []
            skipped_languages: list[str] = []

            # Secondary languages run in their own long-lived worker processes;
            # submit them first so they overlap with the primary language.
            pending: list[tuple[str, Any, int]] = []
            for language in self.languages[1:]:
                if self._engine is not None:
                    continue
                language_image_path = processed_path
                language_dimension = self.max_image_dimension
                if self.secondary_language_max_image_dimension < self.max_image_dimension:
                    language_dimension = self.secondary_language_max_image_dimension
                    secondary_started = perf_counter()
                    language_image_path = self._preprocess(image_path, self.secondary_language_max_image_dimension)
                    processed_paths.append(language_image_path)
                    logger.info("OCR secondary preprocessing completed language=%s path=%s elapsed_seconds=%.3f", language, language_image_path, perf_counter() - secondary_started)
                try:
                    ticket = self._start_secondary_language(language, language_image_path)
                except OcrProcessingError as error:
                    logger.warning("OCR secondary language skipped language=%s reason=%s", language, error)
                    skipped_languages.append(language)
                    continue
                tickets.append(ticket)
                pending.append((language, ticket, language_dimension))

            primary = self.languages[0]
            inference_started = perf_counter()
            logger.info("OCR inference started language=%s", primary)
            primary_detections = self._predict(primary, processed_path)
            logger.info("OCR inference completed language=%s detections=%d elapsed_seconds=%.3f", primary, len(primary_detections), perf_counter() - inference_started)
            primary_detections = self._tag(self._to_original_pixels(primary_detections, original_size, self.max_image_dimension), primary)
            detections.extend(primary_detections)
            completed_languages.append(primary)
            dropped_total = 0

            def add_secondary(language: str, language_detections: list[OcrDetection]) -> None:
                nonlocal dropped_total
                kept, dropped = filter_secondary_detections(primary_detections, self._tag(language_detections, language), language)
                dropped_total += dropped
                logger.info("OCR cross-language de-duplication language=%s kept=%d dropped=%d", language, len(kept), dropped)
                detections.extend(kept)

            if self._engine is not None:
                # An injected engine serves every language in this process.
                for language in self.languages[1:]:
                    language_detections = self._predict(language, processed_path)
                    add_secondary(language, self._to_original_pixels(language_detections, original_size, self.max_image_dimension))
                    completed_languages.append(language)

            for language, ticket, language_dimension in pending:
                secondary_started = perf_counter()
                try:
                    language_detections = ticket.collect()
                except OcrProcessingError as error:
                    # A secondary language is best-effort: keep the primary result.
                    logger.warning("OCR secondary language skipped language=%s reason=%s", language, error)
                    skipped_languages.append(language)
                    continue
                logger.info("OCR inference completed language=%s detections=%d wait_seconds=%.3f", language, len(language_detections), perf_counter() - secondary_started)
                add_secondary(language, self._to_original_pixels(language_detections, original_size, language_dimension))
                completed_languages.append(language)
            parse_started = perf_counter()
            deduplicated = self._deduplicate(detections)
            logger.info("OCR result conversion completed detections=%d elapsed_seconds=%.3f", len(deduplicated), perf_counter() - parse_started)
            return OcrDocument(
                engine=self.engine_name,
                languages=completed_languages,
                detections=deduplicated,
                skipped_languages=skipped_languages,
                secondary_duplicates_dropped=dropped_total,
            )
        except OcrInitializationError:
            raise
        except Exception as error:
            logger.exception("OCR request failed image_path=%s", image_path)
            raise OcrProcessingError("PaddleOCR could not process this image.") from error
        finally:
            for ticket in tickets:
                ticket.cancel()
            for processed_path in processed_paths:
                if processed_path.exists():
                    processed_path.unlink()
            logger.info("OCR request completed image_path=%s elapsed_seconds=%.3f", image_path, perf_counter() - started)

    def _worker_for(self, language: str) -> _SecondaryWorker:
        with self._worker_lock:
            worker = self._workers.get(language)
            if worker is None:
                worker = _SecondaryWorker(language, {
                    "max_image_dimension": self.secondary_language_max_image_dimension,
                    "recognition_batch_size": self.recognition_batch_size,
                    "detection_model": self.detection_model,
                    # A recognition model name is script-specific, so it only
                    # applies to the primary language.
                    "recognition_model": "",
                    "enable_mkldnn": self.enable_mkldnn,
                    "cpu_threads": self.cpu_threads,
                })
                self._workers[language] = worker
            return worker

    def _start_secondary_language(self, language: str, image_path: Path) -> _SecondaryTicket:
        try:
            return _SecondaryTicket(self._worker_for(language), image_path, self.secondary_language_timeout_seconds)
        except Exception as error:
            raise OcrProcessingError(f"PaddleOCR {language} worker could not be started: {error}") from error

    def warm(self) -> None:
        """Load every configured engine and run one small inference through each.

        Called from a background thread at application startup so the first
        user request does not pay model-load time.  Safe to call repeatedly.
        """
        started = perf_counter()
        sample = self._warm_image()
        try:
            self._predict(self.languages[0], sample)
            if self._engine is None:
                tickets = []
                for language in self.languages[1:]:
                    try:
                        tickets.append(self._start_secondary_language(language, sample))
                    except OcrProcessingError as error:
                        logger.warning("OCR warm-up skipped language=%s reason=%s", language, error)
                for ticket in tickets:
                    try:
                        ticket.collect()
                    except OcrProcessingError as error:
                        logger.warning("OCR warm-up failed language=%s reason=%s", ticket.language, error)
            logger.info("OCR warm-up completed languages=%s elapsed_seconds=%.3f", ",".join(self.languages), perf_counter() - started)
        finally:
            sample.unlink(missing_ok=True)

    @staticmethod
    def _warm_image() -> Path:
        import cv2
        import numpy

        canvas = numpy.full((160, 640, 3), 255, dtype=numpy.uint8)
        cv2.putText(canvas, "MRP Rs 50.00 Net Wt 200 g", (20, 95), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (0, 0, 0), 2)
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as temporary:
            output = Path(temporary.name)
        cv2.imwrite(str(output), canvas)
        return output

    def close(self) -> None:
        """Stop secondary worker processes."""
        for worker in self._workers.values():
            worker.stop()
        self._workers.clear()

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
    def _tag(detections: list[OcrDetection], language: str) -> list[OcrDetection]:
        return [replace(item, language=language) for item in detections]

    @staticmethod
    def _to_original_pixels(detections: list[OcrDetection], original_size: tuple[int, int] | None, max_image_dimension: int) -> list[OcrDetection]:
        """Undo the preprocessing downscale applied before this pass."""
        if original_size is None or max(original_size) <= max_image_dimension:
            return detections
        factor = max(original_size) / max_image_dimension
        return [replace(item, bbox=[[round(coordinate * factor, 2) for coordinate in point] for point in item.bbox]) if item.bbox else item for item in detections]

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
