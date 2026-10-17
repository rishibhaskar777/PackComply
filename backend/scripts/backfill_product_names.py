"""Fill product / brand / maker on inspections created before label identity was stored.

    python -m backend.scripts.backfill_product_names

Reads each inspection's latest extracted fields (inspector corrections included)
and writes product_name, brand_name and maker_name. Idempotent: inspections
whose stored values already match are left alone, so re-running changes nothing.
No OCR, extraction or compliance is re-run.
"""
from __future__ import annotations

import sys

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.core.database import SessionLocal
from backend.app.models import Inspection
from backend.app.services.corrections import sync_label_identity
from backend.app.services.workflow import latest_extracted_fields


def backfill(db: Session) -> dict[str, int]:
    counts = {"inspections": 0, "updated": 0, "unchanged": 0, "no_extraction": 0}
    for inspection in db.scalars(select(Inspection).order_by(Inspection.created_at)).all():
        counts["inspections"] += 1
        extracted = latest_extracted_fields(db, inspection.id)
        if not extracted:
            counts["no_extraction"] += 1
            continue
        before = (inspection.product_name, inspection.brand_name, inspection.maker_name)
        sync_label_identity(db, inspection, extracted)
        counts["updated" if before != (inspection.product_name, inspection.brand_name, inspection.maker_name) else "unchanged"] += 1
    return counts


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    with SessionLocal() as db:
        counts = backfill(db)
    print(" ".join(f"{name}={value}" for name, value in counts.items()))


if __name__ == "__main__":
    main()
