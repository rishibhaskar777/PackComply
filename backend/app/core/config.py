from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


# Resolve this from the source tree, not the process working directory, so
# local Uvicorn invocations from either the repository or backend directory
# load the repository's single environment file consistently.
ROOT_ENV_FILE = Path(__file__).resolve().parents[3] / ".env"


# These are the India-relevant language identifiers documented for the
# PP-OCRv3 models used by PaddleOCR 3.x.  They are deliberately separate from
# the UI locale list: a UI locale must never imply OCR support.
SUPPORTED_PADDLE_OCR_LANGUAGES = frozenset({
    "en", "hi", "gom", "mai", "mr", "ne", "sa", "ta", "te", "ur", "ka",
})


class Settings(BaseSettings):
    """Runtime configuration loaded from environment variables."""

    app_name: str = "PackComply API"
    environment: str = "development"
    database_url: str = "postgresql+psycopg://packcomply:packcomply@localhost:5432/packcomply"
    upload_dir: Path = Path("uploads")
    max_upload_bytes: int = 10 * 1024 * 1024
    # Full-resolution phone photographs can make CPU OCR take many minutes.
    # This remains configurable for deployments with unusually small print.
    ocr_max_image_dimension: int = 640
    # Secondary multilingual passes use a smaller image to avoid a very dense
    # Latin label producing hundreds of unnecessary non-primary script crops.
    ocr_secondary_language_max_image_dimension: int = 320
    ocr_secondary_language_timeout_seconds: int = 45
    # PaddleOCR 3.7 accepts this directly. Batching recognition crops is
    # important for dense package labels on the Windows CPU runtime.
    ocr_recognition_batch_size: int = 8
    ocr_languages: str = "en,hi,gom,mai,mr,ne,sa,ta,te,ur,ka"
    session_cookie_name: str = "packcomply_session"
    session_ttl_hours: int = 12
    session_secure: bool = False
    pattern_min_occurrences: int = 2

    model_config = SettingsConfigDict(env_file=ROOT_ENV_FILE, env_file_encoding="utf-8", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()


def get_ocr_languages(settings: Settings | None = None) -> tuple[str, ...]:
    """Parse a comma-separated language list while preserving future extensibility."""
    configured = (settings or get_settings()).ocr_languages
    languages = tuple(dict.fromkeys(code.strip().lower() for code in configured.split(",") if code.strip()))
    if not languages:
        raise ValueError("OCR_LANGUAGES must include at least one PaddleOCR language code.")
    unsupported = sorted(set(languages) - SUPPORTED_PADDLE_OCR_LANGUAGES)
    if unsupported:
        raise ValueError(
            "OCR_LANGUAGES includes unsupported PaddleOCR 3.x language code(s): "
            f"{', '.join(unsupported)}. Supported codes: "
            f"{', '.join(sorted(SUPPORTED_PADDLE_OCR_LANGUAGES))}."
        )
    return languages
