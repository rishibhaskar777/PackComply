from datetime import datetime, timezone
from functools import lru_cache
import logging
from time import perf_counter
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, File, HTTPException, Query, Response, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy import case, delete, func, or_, select
from sqlalchemy.orm import Session

from backend.app.core.config import get_ocr_languages, get_settings
from backend.app.core.database import get_db
from backend.app.models import ComplianceResult, ComplianceRule, ExtractedField, FieldCorrection, Inspection, InspectionImage, OcrResult, Product
from backend.app.schemas.inspection import AuditEvent, ComplianceResponse, FieldCorrectionCreate, VerifyRequest, ExtractionResponse, ImageUploaded, InspectionCreate, InspectionCreated, InspectionHistoryItem, InspectionHistoryPage, InspectionSummary, OcrProcessed, RiskAssessmentResponse, RiskReasonResponse
from backend.app.services.compliance import ComplianceEngine
from backend.app.services.compliance.rule_engine import verdict
from backend.app.services.extraction import ExtractionService
from backend.app.services.extraction.evidence import build_extracted_fields, field_evidence
from backend.app.services.ocr import OcrInitializationError, OcrProcessingError, PaddleOcrService
from backend.app.services.uploads import UploadValidationError, store_image, validate_image
from backend.app.services.workflow import LATEST_IMAGE_ORDER, InspectionWorkflow, WorkflowError, compliance_response, extraction_field_response, latest_extracted_fields, latest_image, persist_compliance, resolve_upload_path
from backend.app.services.report import InspectionReportService
from backend.app.api.auth import get_current_user, require_official
from backend.app.services.audit import build_audit, record_event
from backend.app.services.corrections import CorrectionError, active_corrections, mark_verified, normalise_value, reset_review, sync_label_identity
from backend.app.models import User
from backend.app.services.risk import RiskService

router = APIRouter(prefix="/inspections", tags=["inspections"], dependencies=[Depends(get_current_user)])
logger = logging.getLogger("uvicorn.error")


def require_owner(inspection: Inspection | None, user: User) -> Inspection:
    # A missing owner is legacy data and deliberately remains inaccessible.
    if inspection is None or inspection.user_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Inspection not found.")
    return inspection


def make_inspection_number() -> str:
    return f"PC-{datetime.now(timezone.utc):%Y%m%d}-{uuid4().hex[:8].upper()}"


@lru_cache
def get_ocr_service() -> PaddleOcrService:
    """Dependency factory kept separate so tests and future providers can replace it."""
    settings = get_settings()
    return PaddleOcrService(
        languages=get_ocr_languages(settings),
        max_image_dimension=settings.ocr_max_image_dimension,
        secondary_language_max_image_dimension=settings.ocr_secondary_language_max_image_dimension,
        secondary_language_timeout_seconds=settings.ocr_secondary_language_timeout_seconds,
        recognition_batch_size=settings.ocr_recognition_batch_size,
        detection_model=settings.ocr_detection_model,
        recognition_model=settings.ocr_recognition_model,
        enable_mkldnn=settings.ocr_enable_mkldnn,
        cpu_threads=settings.ocr_cpu_threads,
        auto_rotate=settings.ocr_auto_rotate,
    )


def get_extraction_service() -> ExtractionService:
    return ExtractionService()


def get_compliance_engine() -> ComplianceEngine:
    return ComplianceEngine()


def get_workflow(db: Session = Depends(get_db), ocr_service: PaddleOcrService = Depends(get_ocr_service), extraction_service: ExtractionService = Depends(get_extraction_service), compliance_engine: ComplianceEngine = Depends(get_compliance_engine)) -> InspectionWorkflow:
    settings = get_settings()
    return InspectionWorkflow(db, ocr_service, extraction_service, compliance_engine, settings.upload_dir, settings.max_upload_bytes)


def _overall_compliance_status():
    """Correlated per-inspection status: FAIL > REVIEW > PASS, or NULL without results.

    NOT_APPLICABLE results are neither issues nor passes: an inspection whose
    results are only PASS / NOT_APPLICABLE is PASS (verdict COMPLIANT).
    """
    def count(state: str):
        return func.sum(case((ComplianceResult.result == state, 1), else_=0))
    return (
        select(case((count("FAIL") > 0, "FAIL"), (count("REVIEW") > 0, "REVIEW"), (func.count(ComplianceResult.id) > 0, "PASS"), else_=None))
        .where(ComplianceResult.inspection_id == Inspection.id)
        .correlate(Inspection)
        .scalar_subquery()
    )


@router.post("", response_model=InspectionCreated, status_code=status.HTTP_201_CREATED)
def create_inspection(payload: InspectionCreate, db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> Inspection:
    inspection = Inspection(
        inspection_number=make_inspection_number(),
        manufacturer_id=payload.manufacturer_id,
        product_id=payload.product_id,
        notes=payload.notes,
        status="CREATED",
        user_id=user.id,
    )
    db.add(inspection)
    db.commit()
    db.refresh(inspection)
    return inspection

@router.get("/config")
def upload_config() -> dict[str, object]:
    """Upload limits for the new-inspection form (declared before the /{id} routes so it is not read as an id)."""
    return {"max_upload_bytes": get_settings().max_upload_bytes, "allowed_types": ["image/jpeg", "image/png"]}


@router.get("", response_model=InspectionHistoryPage)
def list_inspections(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    search: str | None = Query(None, max_length=255),
    status_filter: str | None = Query(None, alias="status", max_length=30),
    compliance_status: str | None = Query(None, max_length=30),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> InspectionHistoryPage:
    """Return a compact, newest-first history owned exclusively by ``user``."""
    latest_filename = select(InspectionImage.original_filename).where(InspectionImage.inspection_id == Inspection.id).order_by(*LATEST_IMAGE_ORDER).limit(1).scalar_subquery()
    overall_compliance = _overall_compliance_status()
    product_name = func.coalesce(Inspection.product_name, Product.name)
    query = select(Inspection, product_name, latest_filename, overall_compliance).outerjoin(Product, Product.id == Inspection.product_id).where(Inspection.user_id == user.id)
    count_query = select(func.count()).select_from(Inspection).outerjoin(Product, Product.id == Inspection.product_id).where(Inspection.user_id == user.id)
    if search and (term := search.strip()):
        pattern = f"%{term}%"
        predicate = or_(Inspection.product_name.ilike(pattern), Inspection.brand_name.ilike(pattern), Inspection.maker_name.ilike(pattern), Product.name.ilike(pattern), Inspection.inspection_number.ilike(pattern))
        query = query.where(predicate)
        count_query = count_query.where(predicate)
    if status_filter:
        query = query.where(Inspection.status == status_filter)
        count_query = count_query.where(Inspection.status == status_filter)
    if compliance_status:
        normalized_compliance = "REVIEW" if compliance_status == "WARNING" else compliance_status
        query = query.where(overall_compliance == normalized_compliance)
        count_query = count_query.where(overall_compliance == normalized_compliance)
    total = db.scalar(count_query) or 0
    rows = db.execute(query.order_by(Inspection.created_at.desc()).offset((page - 1) * page_size).limit(page_size)).all()
    return InspectionHistoryPage(items=[InspectionHistoryItem(id=i.id, inspection_number=i.inspection_number, status=i.status, created_at=i.created_at, updated_at=i.updated_at, product_name=name or "Untitled label", brand_name=i.brand_name, maker_name=i.maker_name, review_status=i.review_status, image_filename=image, compliance_status=compliance, verdict=verdict([compliance]) if compliance else None) for i, name, image, compliance in rows], page=page, page_size=page_size, total=total, total_pages=(total + page_size - 1) // page_size)


@router.get("/{inspection_id}/report", response_class=Response, responses={200: {"content": {"application/pdf": {}}}})
def download_inspection_report(
    inspection_id: UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Response:
    """Generate an authorized inspection report without persisting a file."""
    inspection = require_owner(db.get(Inspection, inspection_id), user)
    try:
        report = InspectionReportService(db, get_settings().upload_dir).generate(inspection)
    except Exception as error:
        logger.exception("PDF report generation failed inspection_id=%s", inspection_id)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Unable to generate inspection report.") from error
    filename = f"inspection_{inspection.id}.pdf"
    return Response(content=report, media_type="application/pdf", headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@router.post("/{inspection_id}/image", response_model=ImageUploaded, status_code=status.HTTP_201_CREATED)
async def upload_inspection_image(
    inspection_id: UUID,
    image: UploadFile = File(..., description="Package image in JPG, JPEG, or PNG format."),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> InspectionImage:
    inspection = require_owner(db.get(Inspection, inspection_id), user)

    settings = get_settings()
    content = await image.read(settings.max_upload_bytes + 1)
    content_type = (image.content_type or "").lower()
    try:
        validate_image(content, content_type, settings.max_upload_bytes)
        stored = store_image(content, image.filename or "package-image", content_type, settings.upload_dir)
    except UploadValidationError as error:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(error)) from error

    image_record = InspectionImage(inspection_id=inspection.id, **stored.__dict__)
    db.add(image_record)
    inspection.status = "IMAGE_UPLOADED"
    inspection.failure_stage = inspection.failure_message = None
    db.commit()
    db.refresh(image_record)
    return image_record


@router.get("/{inspection_id}/image", response_class=FileResponse, responses={200: {"content": {"image/jpeg": {}, "image/png": {}}}})
def get_inspection_image(inspection_id: UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> FileResponse:
    """Serve the latest stored package photo to its owner only."""
    inspection = require_owner(db.get(Inspection, inspection_id), user)
    image = latest_image(db, inspection.id)
    path = resolve_upload_path(get_settings().upload_dir, image.storage_key) if image else None
    if path is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Inspection image not found.")
    return FileResponse(path, media_type=image.content_type, headers={"Cache-Control": "private, max-age=300", "X-Content-Type-Options": "nosniff"})


@router.post("/{inspection_id}/process", response_model=InspectionSummary)
def process_inspection(inspection_id: UUID, workflow: InspectionWorkflow = Depends(get_workflow), user: User = Depends(get_current_user)) -> InspectionSummary:
    """Run any unfinished stage once; safe to call again after success or failure."""
    try:
        require_owner(workflow.db.get(Inspection, inspection_id), user); return workflow.process(inspection_id)
    except KeyError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    except WorkflowError as error:
        raise HTTPException(status_code=error.status_code, detail=error.message) from error


@router.post("/{inspection_id}/reanalyse", response_model=InspectionSummary)
def reanalyse_inspection(inspection_id: UUID, workflow: InspectionWorkflow = Depends(get_workflow), user: User = Depends(get_current_user)) -> InspectionSummary:
    """Re-run extraction and compliance on the stored OCR result; OCR is not repeated."""
    try:
        inspection = require_owner(workflow.db.get(Inspection, inspection_id), user)
        workflow.reanalyse(inspection_id)
        record_event(workflow.db, inspection.id, "REANALYSED", user.id)
        workflow.db.refresh(inspection)
        return summary_with_risk(workflow, inspection, user)
    except KeyError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    except WorkflowError as error:
        raise HTTPException(status_code=error.status_code, detail=error.message) from error


def summary_with_risk(workflow: InspectionWorkflow, inspection: Inspection, user: User) -> InspectionSummary:
    summary = workflow.summary(inspection.id)
    # Numerical targeting evidence is an official view; manufacturers retain their existing detail surface.
    if user.role == "OFFICIAL" and inspection.status == "COMPLETED":
        assessment = RiskService(workflow.db, get_settings().pattern_min_occurrences).for_inspection(inspection)
        summary.risk = RiskAssessmentResponse(risk_score=assessment.risk_score, risk_level=assessment.risk_level, reasons=[RiskReasonResponse(**reason.__dict__) for reason in assessment.reasons])
    return summary


@router.get("/{inspection_id}", response_model=InspectionSummary)
def get_inspection_summary(inspection_id: UUID, workflow: InspectionWorkflow = Depends(get_workflow), user: User = Depends(get_current_user)) -> InspectionSummary:
    try:
        inspection = require_owner(workflow.db.get(Inspection, inspection_id), user)
        return summary_with_risk(workflow, inspection, user)
    except KeyError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error


@router.get("/{inspection_id}/audit", response_model=list[AuditEvent])
def get_inspection_audit(inspection_id: UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> list[AuditEvent]:
    """Chronological events: creation, photo (with checksum), OCR, compliance, corrections, re-analysis, verification."""
    return build_audit(db, require_owner(db.get(Inspection, inspection_id), user))


def _re_evaluate(workflow: InspectionWorkflow, inspection: Inspection, user: User) -> InspectionSummary:
    """Re-run extraction on the stored OCR, re-apply corrections, then compliance and risk; OCR is never repeated."""
    try:
        workflow.reanalyse(inspection.id)
    except WorkflowError as error:
        raise HTTPException(status_code=error.status_code, detail=error.message) from error
    workflow.db.refresh(inspection)
    return summary_with_risk(workflow, inspection, user)


@router.post("/{inspection_id}/corrections", response_model=InspectionSummary)
def add_correction(inspection_id: UUID, payload: FieldCorrectionCreate, workflow: InspectionWorkflow = Depends(get_workflow), user: User = Depends(require_official)) -> InspectionSummary:
    """Officials override one extracted field; the value is validated like the extractor's own, then compliance and risk are re-evaluated."""
    db = workflow.db
    inspection = require_owner(db.get(Inspection, inspection_id), user)
    if db.scalar(select(OcrResult.id).where(OcrResult.inspection_id == inspection.id).limit(1)) is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="OCR must be run before a value can be corrected.")
    try:
        value, _ = normalise_value(payload.field_name, payload.corrected_value)
    except CorrectionError as error:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(error)) from error
    previous = active_corrections(db, inspection.id).get(payload.field_name)
    if previous is not None:
        original = previous.original_value  # always the OCR-read value, never an earlier correction
    else:
        current = next((field for field in latest_extracted_fields(db, inspection.id) if field.field_name == payload.field_name), None)
        original = current.value if current else None
    db.add(FieldCorrection(inspection_id=inspection.id, field_name=payload.field_name, original_value=original, corrected_value=value, reason=(payload.reason or "").strip() or None, corrected_by=user.id))
    reset_review(inspection)
    db.commit()
    return _re_evaluate(workflow, inspection, user)


@router.delete("/{inspection_id}/corrections/{correction_id}", response_model=InspectionSummary)
def undo_correction(inspection_id: UUID, correction_id: UUID, workflow: InspectionWorkflow = Depends(get_workflow), user: User = Depends(require_official)) -> InspectionSummary:
    db = workflow.db
    inspection = require_owner(db.get(Inspection, inspection_id), user)
    correction = db.get(FieldCorrection, correction_id)
    if correction is None or correction.inspection_id != inspection.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Correction not found.")
    record_event(db, inspection.id, "CORRECTION_UNDONE", user.id, {"field": correction.field_name, "original": correction.original_value, "corrected": correction.corrected_value})
    db.delete(correction)
    reset_review(inspection)
    db.commit()
    return _re_evaluate(workflow, inspection, user)


@router.post("/{inspection_id}/verify", response_model=InspectionSummary)
def verify_inspection(inspection_id: UUID, payload: VerifyRequest, workflow: InspectionWorkflow = Depends(get_workflow), user: User = Depends(require_official)) -> InspectionSummary:
    db = workflow.db
    inspection = require_owner(db.get(Inspection, inspection_id), user)
    if inspection.status != "COMPLETED":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Only a completed inspection can be verified.")
    remarks = (payload.remarks or "").strip() or None
    mark_verified(inspection, user.id, remarks)
    db.commit()
    statuses = [result for (result,) in db.execute(select(ComplianceResult.result).where(ComplianceResult.inspection_id == inspection.id)).all()]
    record_event(db, inspection.id, "VERIFIED", user.id, {"remarks": remarks, "verdict": verdict(statuses) if statuses else None})
    return summary_with_risk(workflow, inspection, user)


@router.post("/{inspection_id}/images/{image_id}/ocr", response_model=OcrProcessed, status_code=status.HTTP_201_CREATED)
def process_inspection_image_ocr(
    inspection_id: UUID,
    image_id: UUID,
    db: Session = Depends(get_db),
    ocr_service: PaddleOcrService = Depends(get_ocr_service),
    user: User = Depends(get_current_user),
) -> OcrProcessed:
    started = perf_counter()
    logger.info("OCR route started inspection_id=%s image_id=%s", inspection_id, image_id)
    inspection = require_owner(db.get(Inspection, inspection_id), user)
    image = db.get(InspectionImage, image_id)
    if image is None or image.inspection_id != inspection.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Inspection image not found.")

    settings = get_settings()
    image_path = resolve_upload_path(settings.upload_dir, image.storage_key)
    if image_path is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Uploaded image file is unavailable.")
    try:
        validate_image(image_path.read_bytes(), image.content_type, settings.max_upload_bytes)
    except UploadValidationError as error:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Stored image is invalid.") from error

    scan_started = perf_counter()
    try:
        document = ocr_service.scan(image_path)
    except OcrInitializationError as error:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(error)) from error
    except OcrProcessingError as error:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(error)) from error

    ocr_result = OcrResult(
        inspection_id=inspection.id,
        image_id=image.id,
        engine=document.engine,
        languages=document.languages,
        raw_text=document.raw_text,
        detections=document.as_database_detections(),
        duration_ms=round((perf_counter() - scan_started) * 1000),
    )
    logger.info("OCR persistence started inspection_id=%s", inspection_id)
    db.add(ocr_result)
    db.commit()
    db.refresh(ocr_result)
    logger.info("OCR route completed inspection_id=%s elapsed_seconds=%.3f", inspection_id, perf_counter() - started)
    return OcrProcessed(
        id=ocr_result.id,
        inspection_id=ocr_result.inspection_id,
        image_id=ocr_result.image_id,
        engine=ocr_result.engine,
        languages=ocr_result.languages,
        skipped_languages=document.skipped_languages,
        processed_at=ocr_result.created_at,
        raw_text=ocr_result.raw_text,
        results=ocr_result.detections,
    )


@router.post("/{inspection_id}/extract", response_model=ExtractionResponse)
def extract_inspection_information(
    inspection_id: UUID,
    db: Session = Depends(get_db),
    extraction_service: ExtractionService = Depends(get_extraction_service),
    user: User = Depends(get_current_user),
) -> ExtractionResponse:
    inspection = require_owner(db.get(Inspection, inspection_id), user)
    ocr_result = db.scalar(
        select(OcrResult).where(OcrResult.inspection_id == inspection.id).order_by(OcrResult.created_at.desc())
    )
    if ocr_result is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="OCR must be run before information extraction.")

    fields = extraction_service.extract(ocr_result.raw_text, ocr_result.detections)
    # Re-running extraction replaces only this interpretation layer, never OCR evidence.
    db.execute(delete(ExtractedField).where(ExtractedField.ocr_result_id == ocr_result.id))
    # Findings are derived from extraction. Keeping them after a new parse made
    # reports show an old FAIL/REVIEW beside a corrected extracted value.
    invalidated = db.execute(delete(ComplianceResult).where(ComplianceResult.inspection_id == inspection.id)).rowcount
    persisted = build_extracted_fields(inspection.id, ocr_result, fields)
    db.add_all(persisted)
    db.commit()
    logger.info("Extraction persisted inspection_id=%s ocr_result_id=%s fields=%s invalidated_compliance_results=%s", inspection.id, ocr_result.id, {name: {"raw_ocr": field.source_text, "parsed": field.value, "normalized": field.normalized_value} for name, field in fields.items() if field.value}, invalidated)
    return ExtractionResponse(
        inspection_id=inspection.id,
        ocr_result_id=ocr_result.id,
        fields={field.field_name: extraction_field_response(field) for field in persisted},
    )


@router.post("/{inspection_id}/compliance", response_model=ComplianceResponse)
def run_inspection_compliance(inspection_id: UUID, db: Session = Depends(get_db), compliance_engine: ComplianceEngine = Depends(get_compliance_engine), user: User = Depends(get_current_user)) -> ComplianceResponse:
    inspection = require_owner(db.get(Inspection, inspection_id), user)
    ocr_result = db.scalar(select(OcrResult).where(OcrResult.inspection_id == inspection.id).order_by(OcrResult.created_at.desc()))
    if ocr_result is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="OCR must be run before compliance analysis.")
    extracted = db.scalars(select(ExtractedField).where(ExtractedField.ocr_result_id == ocr_result.id)).all()
    if not extracted:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Information extraction must be run before compliance analysis.")
    fields = {field.field_name: field_evidence(field) for field in extracted}
    logger.info("Compliance input inspection_id=%s ocr_result_id=%s fields=%s", inspection.id, ocr_result.id, {name: {"raw": item.value, "normalized": compliance_engine._normalized("NET_QUANTITY_FORMAT" if name == "net_quantity" else "MRP_FORMAT" if name == "mrp" else "", item.value)} for name, item in fields.items()})
    persist_compliance(db, compliance_engine, inspection.id, fields)
    sync_label_identity(db, inspection, list(extracted))
    persisted = db.scalars(select(ComplianceResult).where(ComplianceResult.inspection_id == inspection.id).join(ComplianceRule).order_by(ComplianceRule.rule_code)).all()
    response = compliance_response(inspection.id, persisted, list(extracted))
    logger.info("Compliance persisted inspection_id=%s overall=%s findings=%s", inspection.id, response.overall_status, {finding.rule_id: {"status": finding.status, "reason": finding.message} for finding in response.findings})
    return response


@router.get("/{inspection_id}/compliance", response_model=ComplianceResponse)
def get_inspection_compliance(inspection_id: UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> ComplianceResponse:
    require_owner(db.get(Inspection, inspection_id), user)
    results = db.scalars(select(ComplianceResult).where(ComplianceResult.inspection_id == inspection_id).join(ComplianceRule).order_by(ComplianceRule.rule_code)).all()
    if not results:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Compliance analysis has not been run.")
    return compliance_response(inspection_id, results, latest_extracted_fields(db, inspection_id))
