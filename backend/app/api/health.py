from fastapi import APIRouter

router = APIRouter(tags=["system"])


@router.get("/health", summary="Check API availability")
def health_check() -> dict[str, str]:
    """Return service availability without exposing configuration details."""
    return {"status": "ok", "service": "packcomply-api"}
