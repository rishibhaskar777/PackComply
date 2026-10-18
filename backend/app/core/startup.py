"""One log line at the end of startup that says whether the demo machine is ready."""
import logging
from time import perf_counter

from sqlalchemy import text

from backend.app.core.config import get_ocr_languages, get_settings
from backend.app.core.database import SessionLocal
from backend.app.core.network import phone_url
from backend.app.core.ocr_status import get_ocr_state
from backend.app.services.compliance.ruleset import default_ruleset

logger = logging.getLogger("uvicorn.error")


def startup_summary(warmup_seconds: float | None) -> str:
    """e.g. "PackComply ready | DB ok | ruleset lm_pc_2011 v2 (14 rules) | OCR en+hi ready (warm-up 12.4 s) | LAN https://..."."""
    try:
        with SessionLocal() as db:
            db.execute(text("SELECT 1"))
        database = "ok"
    except Exception:  # noqa: BLE001 - the summary must never stop the API from starting
        database = "UNREACHABLE"
    ruleset = default_ruleset()
    try:
        languages = "+".join(get_ocr_languages(get_settings()))
    except ValueError:
        languages = "invalid OCR_LANGUAGES"
    state = get_ocr_state()
    warm = f" (warm-up {warmup_seconds:.1f} s)" if warmup_seconds is not None else ""
    lan = phone_url() or "unknown"
    return f"PackComply ready | DB {database} | ruleset {ruleset.ruleset_id} v{ruleset.version} ({len(ruleset.enabled_rules)} rules) | OCR {languages} {state}{warm} | LAN {lan}"


def log_startup_summary(started: float | None = None) -> None:
    logger.info(startup_summary(perf_counter() - started if started is not None else None))
