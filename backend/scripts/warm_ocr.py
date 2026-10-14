"""Download and warm PaddleOCR models for every configured language.

Run once after install and before any demo so no model downloads happen live:
    python -m backend.scripts.warm_ocr

Does not touch the database.
"""
from pathlib import Path
import shutil
import tempfile
from time import perf_counter

from PIL import Image, ImageDraw, ImageFont

from backend.app.core.config import get_ocr_languages, get_settings
from backend.app.services.ocr import PaddleOcrService

SAMPLE_TEXT = "MRP Rs 50.00 Net Wt 200 g"


def _sample_image(directory: Path) -> Path:
    image = Image.new("RGB", (640, 160), "white")
    try:
        font = ImageFont.truetype("arial.ttf", 40)
    except OSError:
        font = ImageFont.load_default()
    ImageDraw.Draw(image).text((20, 50), SAMPLE_TEXT, fill="black", font=font)
    path = directory / "warm.png"
    image.save(path)
    return path


def main() -> int:
    settings = get_settings()
    languages = get_ocr_languages(settings)
    directory = Path(tempfile.mkdtemp(prefix="packcomply-warm-"))
    failures = 0
    try:
        image_path = _sample_image(directory).resolve()
        for language in languages:
            started = perf_counter()
            try:
                service = PaddleOcrService(
                    languages=(language,),
                    max_image_dimension=settings.ocr_max_image_dimension,
                    recognition_batch_size=settings.ocr_recognition_batch_size,
                    detection_model=settings.ocr_detection_model,
                    recognition_model=settings.ocr_recognition_model,
                )
                document = service.scan(image_path)
                print(f"[ OK ] {language}: {len(document.detections)} detections in {perf_counter() - started:.1f}s")
            except Exception as error:
                failures += 1
                print(f"[FAIL] {language}: {error} ({perf_counter() - started:.1f}s)")
    finally:
        shutil.rmtree(directory, ignore_errors=True)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
