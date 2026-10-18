"""Public system status for the sign-in page: no authentication, nothing sensitive."""
import json
import logging
from threading import Lock
from time import monotonic
from typing import Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.app.api.health import database_ready
from backend.app.api.auth import get_current_user
from backend.app.core.config import get_ocr_languages, get_settings
from backend.app.core.database import get_db
from backend.app.core.network import detect_lan_ip, phone_url
from backend.app.core.ocr_status import OcrState, get_ocr_state
from backend.app.services.compliance.ruleset import default_ruleset

router = APIRouter(prefix="/system", tags=["system"])


class SystemStatus(BaseModel):
    status: Literal["ok", "degraded", "down"]
    database: bool
    ocr: OcrState
    ruleset_version: str
    rule_count: int
    languages: list[str]


class _StatusCache:
    """Keeps the last status for a few seconds so polling clients cannot hammer the database."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._value: SystemStatus | None = None
        self._at = 0.0

    def get(self, ttl: float) -> SystemStatus | None:
        with self._lock:
            return self._value if self._value is not None and monotonic() - self._at < ttl else None

    def put(self, value: SystemStatus) -> None:
        with self._lock:
            self._value, self._at = value, monotonic()

    def clear(self) -> None:
        with self._lock:
            self._value = None


status_cache = _StatusCache()


def _build_status(db: Session) -> SystemStatus:
    database = database_ready(db, log_traceback=False)
    ocr = get_ocr_state()
    ruleset = default_ruleset()
    try:
        languages = list(get_ocr_languages())
    except ValueError:
        languages = []
    overall = "down" if not database else "degraded" if ocr == "unavailable" else "ok"
    return SystemStatus(status=overall, database=database, ocr=ocr, ruleset_version=ruleset.version, rule_count=len(ruleset.enabled_rules), languages=languages)


@router.get("/status", response_model=SystemStatus, summary="Public readiness summary for the sign-in page")
def system_status(db: Session = Depends(get_db)) -> SystemStatus:
    """Always 200 while the API runs: `status` is "down" without the database, "degraded" without OCR."""
    ttl = get_settings().system_status_cache_seconds
    cached = status_cache.get(ttl)
    if cached is not None:
        return cached
    value = _build_status(db)
    status_cache.put(value)
    return value


class NetworkInfo(BaseModel):
    lan_ip: str | None
    phone_url: str | None


@router.get("/network", response_model=NetworkInfo, summary="Where a phone on the same Wi-Fi opens the app", dependencies=[Depends(get_current_user)])
def network_info(request: Request) -> NetworkInfo:
    """The laptop's LAN address and the phone URL (for the "Open on phone" QR code). Signed-in users only."""
    scheme = request.headers.get("x-forwarded-proto") or request.url.scheme
    port = request.headers.get("x-forwarded-port")
    return NetworkInfo(lan_ip=detect_lan_ip(), phone_url=phone_url(scheme, port))


logger = logging.getLogger("uvicorn.error")


@router.get("/evaluation", summary="Measured accuracy of the pipeline, from the evaluation report file", dependencies=[Depends(get_current_user)])
def evaluation_report() -> dict:
    """The JSON written by ``batch_evaluate --report-json`` (``{"schema": 1, "runs": [...]}``); no runs when no report exists.

    Nothing is computed here: the page shows exactly what the evaluation script measured, with its dataset and date.
    """
    path = get_settings().evaluation_report_path
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(report, dict) and isinstance(report.get("runs"), list):
            return report
        logger.warning("Evaluation report %s has no 'runs' list; ignoring it", path)
    except FileNotFoundError:
        pass
    except (OSError, ValueError):
        logger.warning("Evaluation report %s could not be read", path, exc_info=True)
    return {"schema": 1, "runs": []}
