import logging

from fastapi import APIRouter, Depends, status
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from backend.app.core.database import get_db

router = APIRouter(tags=["system"])
logger = logging.getLogger("uvicorn.error")


@router.get("/health", summary="Check API availability")
def health_check() -> dict[str, str]:
    """Return service availability without exposing configuration details."""
    return {"status": "ok", "service": "packcomply-api"}


@router.get("/health/ready", summary="Check that the API can serve requests", responses={503: {"description": "Database unreachable"}})
def readiness_check(db: Session = Depends(get_db)) -> JSONResponse:
    """Readiness: the API is only useful when PostgreSQL answers a trivial query."""
    try:
        db.execute(text("SELECT 1"))
    except SQLAlchemyError:
        logger.exception("Readiness check failed: database unreachable")
        return JSONResponse(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, content={"status": "unavailable", "database": "unreachable"})
    return JSONResponse(content={"status": "ready", "database": "ok"})
