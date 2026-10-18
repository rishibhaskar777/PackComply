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
    ocr_max_image_dimension: int = 1280
    # Secondary multilingual passes use a smaller image to avoid a very dense
    # Latin label producing hundreds of unnecessary non-primary script crops.
    ocr_secondary_language_max_image_dimension: int = 960
    ocr_secondary_language_timeout_seconds: int = 90
    # PaddleOCR 3.7 accepts this directly. Batching recognition crops is
    # important for dense package labels on the Windows CPU runtime.
    ocr_recognition_batch_size: int = 8
    # Each extra language adds processing time; "en,hi" is the sensible default.
    ocr_languages: str = "en,hi"
    # Languages read per photo panel, as PANEL=lang+lang pairs separated by ";". A date stamp is Latin
    # digits and codes, so skipping Hindi there saves a whole pass. Panels not listed use OCR_LANGUAGES.
    ocr_panel_languages: str = "DATE_STAMP=en;BACK=en+hi;FRONT=en+hi;OTHER=en+hi"
    # Optional PaddleOCR model names (e.g. PP-OCRv5_mobile_det); empty keeps defaults.
    ocr_detection_model: str = ""
    ocr_recognition_model: str = ""
    # oneDNN for the recognition models only (detection models cannot use it
    # on Windows); ~2.4x faster. Falls back to off automatically on failure.
    ocr_enable_mkldnn: bool = True
    # 0 keeps the library default thread count.
    ocr_cpu_threads: int = 0
    # Re-read a photo turned 90 / 270 degrees when its text runs sideways.
    ocr_auto_rotate: bool = True
    # Load OCR models in a background thread when the API starts.
    ocr_warmup_on_startup: bool = True
    session_cookie_name: str = "packcomply_session"
    session_ttl_hours: int = 12
    # Lifetime when "Keep me signed in on this device" is ticked at login.
    session_remember_days: int = 30
    session_secure: bool = False
    pattern_min_occurrences: int = 2
    # Copy the versioned ruleset YAML into compliance_rules when the API starts.
    compliance_rules_sync_on_startup: bool = True
    allow_official_self_registration: bool = False
    login_max_attempts: int = 5
    login_lockout_minutes: int = 15
    # One-time 6-digit password-reset codes (issued by an administrator, or e-mailed when SMTP is set).
    password_reset_code_minutes: int = 15
    password_reset_max_attempts: int = 5
    # Reset requests allowed per username and per client IP within the window.
    password_reset_rate_limit: int = 3
    password_reset_rate_window_minutes: int = 15
    # Optional e-mail delivery of reset codes (Python smtplib). Disabled while smtp_host is empty.
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_from: str = ""
    smtp_starttls: bool = True
    smtp_ssl: bool = False
    smtp_timeout_seconds: int = 10
    # GET /api/system/status caches its checks for this long.
    system_status_cache_seconds: float = 5.0
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"
    # The laptop's Wi-Fi/LAN IPv4 and the full phone URL; scripts/start-demo.ps1 sets them. Empty = detect.
    lan_ip: str = ""
    phone_url: str = ""
    # Measured accuracy shown on "About & accuracy"; written by `batch_evaluate --report-json`.
    evaluation_report_path: Path = Path(__file__).resolve().parents[1] / "assets" / "evaluation_report.json"

    model_config = SettingsConfigDict(env_file=ROOT_ENV_FILE, env_file_encoding="utf-8", extra="ignore")


def get_cors_origins(settings: "Settings | None" = None) -> list[str]:
    configured = (settings or get_settings()).cors_origins
    return [origin.strip() for origin in configured.split(",") if origin.strip()]


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


def get_ocr_panel_languages(settings: Settings | None = None) -> dict[str, tuple[str, ...]]:
    """Parse OCR_PANEL_LANGUAGES ("DATE_STAMP=en;BACK=en+hi") into {panel: languages}; bad entries are rejected."""
    configured = (settings or get_settings()).ocr_panel_languages
    result: dict[str, tuple[str, ...]] = {}
    for entry in configured.split(";"):
        if not entry.strip():
            continue
        panel, _, codes = entry.partition("=")
        languages = tuple(dict.fromkeys(code.strip().lower() for code in codes.replace(",", "+").split("+") if code.strip()))
        unsupported = sorted(set(languages) - SUPPORTED_PADDLE_OCR_LANGUAGES)
        if not panel.strip() or not languages or unsupported:
            raise ValueError(f"OCR_PANEL_LANGUAGES entry {entry!r} is invalid; expected PANEL=lang+lang with supported language codes.")
        result[panel.strip().upper()] = languages
    return result
