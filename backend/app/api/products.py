"""Manufacturer portal data: products (self-checks grouped by product) and the open fix list. Owner-scoped, MANUFACTURER only."""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from backend.app.api.auth import require_manufacturer
from backend.app.core.database import get_db
from backend.app.models import User
from backend.app.schemas.inspection import FixesResponse, ProductsSummary
from backend.app.services.products import open_fixes, product_summary

router = APIRouter(tags=["products"])


@router.get("/products", response_model=ProductsSummary, summary="The manufacturer's products with their latest self-check")
def list_products(db: Session = Depends(get_db), user: User = Depends(require_manufacturer)) -> ProductsSummary:
    return product_summary(db, user.id)


@router.get("/fixes", response_model=FixesResponse, summary="Open fix items across the latest self-checks, grouped by rule")
def list_fixes(db: Session = Depends(get_db), user: User = Depends(require_manufacturer)) -> FixesResponse:
    return open_fixes(db, user.id)
