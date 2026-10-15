"""Re-run extraction and compliance for every completed inspection, without new OCR.

    python -m backend.scripts.reanalyse_all [--inspection UUID]

Uses each inspection's latest stored OcrResult (text and boxes) and replaces
its ExtractedField and ComplianceResult rows, exactly like
POST /api/inspections/{id}/reanalyse.
"""
from __future__ import annotations

import argparse
import sys
from uuid import UUID

from sqlalchemy import select

from backend.app.core.config import get_settings
from backend.app.core.database import SessionLocal
from backend.app.models import ExtractedField, Inspection, OcrResult
from backend.app.services.compliance import ComplianceEngine
from backend.app.services.extraction import ExtractionService
from backend.app.services.workflow import InspectionWorkflow, WorkflowError


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--inspection", type=UUID, help="only re-analyse this inspection")
    args = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    settings = get_settings()
    counts = {"reanalysed": 0, "skipped_no_ocr": 0, "failed": 0}
    with SessionLocal() as db:
        query = select(Inspection.id, Inspection.inspection_number).where(Inspection.status == "COMPLETED").order_by(Inspection.created_at)
        if args.inspection:
            query = select(Inspection.id, Inspection.inspection_number).where(Inspection.id == args.inspection)
        inspections = db.execute(query).all()
        # OCR is never run here, so no OCR engine is constructed.
        workflow = InspectionWorkflow(db, None, ExtractionService(), ComplianceEngine(), settings.upload_dir, settings.max_upload_bytes)  # type: ignore[arg-type]
        for inspection_id, number in inspections:
            if db.scalar(select(OcrResult.id).where(OcrResult.inspection_id == inspection_id).limit(1)) is None:
                counts["skipped_no_ocr"] += 1
                print(f"{number} skipped: no stored OCR result")
                continue
            try:
                summary = workflow.reanalyse(inspection_id)
            except (WorkflowError, KeyError) as error:
                counts["failed"] += 1
                print(f"{number} failed: {error}")
                continue
            counts["reanalysed"] += 1
            fields = db.scalars(select(ExtractedField).where(ExtractedField.inspection_id == inspection_id)).all()
            found = sum(field.value is not None for field in fields)
            overall = summary.compliance.overall_status if summary.compliance else "—"
            print(f"{number} {inspection_id} fields_with_value={found}/{len(fields)} compliance={overall}")
    print(f"\ninspections={len(inspections)} reanalysed={counts['reanalysed']} skipped_no_ocr={counts['skipped_no_ocr']} failed={counts['failed']}")


if __name__ == "__main__":
    main()
