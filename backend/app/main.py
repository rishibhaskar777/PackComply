from contextlib import asynccontextmanager
import logging
from threading import Thread

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.exc import OperationalError, SQLAlchemyError

from backend.app.api.health import router as health_router
from backend.app.api.inspections import get_ocr_service, router as inspections_router
from backend.app.api.auth import router as auth_router
from backend.app.api.patterns import router as patterns_router
from backend.app.api.roles import router as roles_router
from backend.app.api.risk_targeting import router as risk_targeting_router
from backend.app.api.compliance import router as compliance_router
from backend.app.core.config import get_cors_origins, get_settings
from backend.app.core.database import SessionLocal
from backend.app.services.compliance.registry import sync_compliance_rules
from backend.app.services.compliance.ruleset import default_ruleset

settings = get_settings()
logger = logging.getLogger("uvicorn.error")


def _warm_ocr_models() -> None:
    try:
        # Resolve through the override map so tests and alternate providers apply.
        factory = app.dependency_overrides.get(get_ocr_service, get_ocr_service)
        service = factory()
        warm = getattr(service, "warm", None)
        if warm is not None:
            warm()
    except Exception:
        logger.exception("OCR warm-up failed; models will load on the first request instead")


def _sync_compliance_rules() -> None:
    try:
        with SessionLocal() as db:
            sync_compliance_rules(db)
            db.commit()
    except SQLAlchemyError:
        # Database down or not yet migrated: the API still starts; rules sync on first evaluation.
        logger.warning("Compliance rules not synced at startup; they sync on the first evaluation", exc_info=True)


@asynccontextmanager
async def lifespan(_: FastAPI):
    # A malformed ruleset fails here, at startup, rather than mid-inspection.
    default_ruleset()
    if get_settings().compliance_rules_sync_on_startup:
        _sync_compliance_rules()
    # Warm in a daemon thread so the API serves /api/health immediately.
    if get_settings().ocr_warmup_on_startup:
        Thread(target=_warm_ocr_models, name="ocr-warmup", daemon=True).start()
    yield
    if get_ocr_service.cache_info().currsize:
        close = getattr(get_ocr_service(), "close", None)
        if close is not None:
            close()


app = FastAPI(
    lifespan=lifespan,
    title=settings.app_name,
    version="0.1.0",
    description="Inspection-assistance API. Results are not legally binding decisions.",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=get_cors_origins(settings),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.exception_handler(OperationalError)
async def database_unavailable(request: Request, error: OperationalError) -> JSONResponse:
    # Connection-level failures (PostgreSQL stopped, network, timeout) are
    # transient for the client; the full error stays in the server log.
    logger.error("Database unavailable method=%s path=%s", request.method, request.url.path, exc_info=error)
    return JSONResponse(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, content={"detail": "Database unavailable. Please try again shortly."})


@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Permissions-Policy"] = "camera=(self), geolocation=(self), microphone=()"
    return response


app.include_router(health_router, prefix="/api")
app.include_router(auth_router, prefix="/api")
app.include_router(inspections_router, prefix="/api")
app.include_router(patterns_router, prefix="/api")
app.include_router(roles_router, prefix="/api")
app.include_router(risk_targeting_router, prefix="/api")
app.include_router(compliance_router, prefix="/api")
