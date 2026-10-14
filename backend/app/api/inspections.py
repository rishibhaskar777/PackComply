from datetime import datetime, timezone
from functools import lru_cache
import logging
from time import perf_counter
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, File, HTTPException, Query, Response, UploadFile, status
from sqlalchemy import delete, func, or_, select
from sqlalchemy.orm import Session

from backend.app.core.config import get_ocr_languages, get_settings
from backend.app.core.database import get_db
from backend.app.models import ComplianceResult, ComplianceRule, ExtractedField, Inspection, InspectionImage, OcrResult, Product
from backend.app.schemas.inspection import ComplianceFindingResponse, ComplianceResponse, ComplianceSummary, ExtractionFieldResponse, ExtractionResponse, ImageUploaded, InspectionCreate, InspectionCreated, InspectionHistoryItem, InspectionHistoryPage, InspectionSummary, OcrProcessed, RiskAssessmentResponse, RiskReasonResponse
from backend.app.services.compliance import ComplianceEngine
from backend.app.services.compliance.rule_engine import FieldEvidence, RULES
from backend.app.services.extraction import ExtractionService
from backend.app.services.ocr import OcrInitializationError, OcrProcessingError, PaddleOcrService
from backend.app.services.uploads import UploadValidationError, store_image, validate_image
from backend.app.services.workflow import InspectionWorkflow, WorkflowError
from backend.app.services.report import InspectionReportService
from backend.app.api.auth import get_current_user
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
    )


def get_extraction_service() -> ExtractionService:
    return ExtractionService()


def get_compliance_engine() -> ComplianceEngine:
    return ComplianceEngine()


def get_workflow(db: Session = Depends(get_db), ocr_service: PaddleOcrService = Depends(get_ocr_service), extraction_service: ExtractionService = Depends(get_extraction_service), compliance_engine: ComplianceEngine = Depends(get_compliance_engine)) -> InspectionWorkflow:
    settings = get_settings()
    return InspectionWorkflow(db, ocr_service, extraction_service, compliance_engine, settings.upload_dir, settings.max_upload_bytes)


def _compliance_response(inspection_id: UUID, results: list[ComplianceResult]) -> ComplianceResponse:
    findings = [ComplianceFindingResponse(rule_id=result.rule.rule_code, rule_name=result.rule.description.split(".", 1)[0], status=result.result, severity=result.rule.severity, message=result.explanation, field=result.field_name, value=result.value, confidence=float(result.confidence) if result.confidence is not None else None, evidence=result.evidence, expected=result.rule.description) for result in results]
    counts = {name: sum(finding.status == name for finding in findings) for name in ("PASS", "FAIL", "REVIEW")}
    overall = "FAIL" if counts["FAIL"] else "REVIEW" if counts["REVIEW"] else "PASS"
    return ComplianceResponse(inspection_id=inspection_id, overall_status=overall, summary=ComplianceSummary(passed=counts["PASS"], failed=counts["FAIL"], review=counts["REVIEW"]), findings=findings)


def _ensure_compliance_rules(db: Session) -> dict[str, ComplianceRule]:
    existing = {rule.rule_code: rule for rule in db.scalars(select(ComplianceRule)).all()}
    for code, name, target_field, severity in RULES:
        if code not in existing:
            rule = ComplianceRule(rule_code=code, description=f"{name}. Deterministic extraction-evidence check; it is not a legal determination.", target_field=target_field, severity=severity, enabled=True, configuration={})
            db.add(rule)
            existing[code] = rule
    db.flush()
    return existing


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
    latest_image = select(InspectionImage.original_filename).where(InspectionImage.inspection_id == Inspection.id).order_by(InspectionImage.created_at.desc()).limit(1).scalar_subquery()
    latest_compliance = select(ComplianceResult.result).where(ComplianceResult.inspection_id == Inspection.id).order_by(ComplianceResult.updated_at.desc()).limit(1).scalar_subquery()
    query = select(Inspection, Product.name, latest_image, latest_compliance).outerjoin(Product, Product.id == Inspection.product_id).where(Inspection.user_id == user.id)
    count_query = select(func.count()).select_from(Inspection).outerjoin(Product, Product.id == Inspection.product_id).where(Inspection.user_id == user.id)
    if search and (term := search.strip()):
        predicate = or_(Product.name.ilike(f"%{term}%"), Inspection.inspection_number.ilike(f"%{term}%"))
        query = query.where(predicate)
        count_query = count_query.where(predicate)
    if status_filter:
        query = query.where(Inspection.status == status_filter)
        count_query = count_query.where(Inspection.status == status_filter)
    if compliance_status:
        normalized_compliance = "REVIEW" if compliance_status == "WARNING" else compliance_status
        query = query.where(latest_compliance == normalized_compliance)
        count_query = count_query.where(latest_compliance == normalized_compliance)
    total = db.scalar(count_query) or 0
    rows = db.execute(query.order_by(Inspection.created_at.desc()).offset((page - 1) * page_size).limit(page_size)).all()
    return InspectionHistoryPage(items=[InspectionHistoryItem(id=i.id, inspection_number=i.inspection_number, status=i.status, created_at=i.created_at, updated_at=i.updated_at, product_name=name, image_filename=image, compliance_status=compliance) for i, name, image, compliance in rows], page=page, page_size=page_size, total=total, total_pages=(total + page_size - 1) // page_size)


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


@router.post("/{inspection_id}/process", response_model=InspectionSummary)
def process_inspection(inspection_id: UUID, workflow: InspectionWorkflow = Depends(get_workflow), user: User = Depends(get_current_user)) -> InspectionSummary:
    """Run any unfinished stage once; safe to call again after success or failure."""
    try:
        require_owner(workflow.db.get(Inspection, inspection_id), user); return workflow.process(inspection_id)
    except KeyError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    except WorkflowError as error:
        raise HTTPException(status_code=error.status_code, detail=error.message) from error


@router.get("/{inspection_id}", response_model=InspectionSummary)
def get_inspection_summary(inspection_id: UUID, workflow: InspectionWorkflow = Depends(get_workflow), user: User = Depends(get_current_user)) -> InspectionSummary:
    try:
        inspection = require_owner(workflow.db.get(Inspection, inspection_id), user)
        summary = workflow.summary(inspection_id)
        # Numerical targeting evidence is an official view; manufacturers retain their existing detail surface.
        if user.role == "OFFICIAL" and inspection.status == "COMPLETED":
            assessment = RiskService(workflow.db, get_settings().pattern_min_occurrences).for_inspection(inspection)
            summary.risk = RiskAssessmentResponse(risk_score=assessment.risk_score, risk_level=assessment.risk_level, reasons=[RiskReasonResponse(**reason.__dict__) for reason in assessment.reasons])
        return summary
    except KeyError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error


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
    upload_root = settings.upload_dir.resolve()
    image_path = (upload_root / image.storage_key).resolve()
    if not image_path.is_relative_to(upload_root) or not image_path.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Uploaded image file is unavailable.")
    try:
        validate_image(image_path.read_bytes(), image.content_type, settings.max_upload_bytes)
    except UploadValidationError as error:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Stored image is invalid.") from error

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

    fields = extraction_service.extract(ocr_result.raw_text)
    # Re-running extraction replaces only this interpretation layer, never OCR evidence.
    db.execute(delete(ExtractedField).where(ExtractedField.ocr_result_id == ocr_result.id))
    # Findings are derived from extraction. Keeping them after a new parse made
    # reports show an old FAIL/REVIEW beside a corrected extracted value.
    invalidated = db.execute(delete(ComplianceResult).where(ComplianceResult.inspection_id == inspection.id)).rowcount
    for field_name, field in fields.items():
        db.add(ExtractedField(
            inspection_id=inspection.id,
            ocr_result_id=ocr_result.id,
            field_name=field_name,
            value=field.value,
            confidence=field.confidence,
            review_status="REVIEW" if field.needs_review else "EXTRACTED",
        ))
    db.commit()
    logger.info("Extraction persisted inspection_id=%s ocr_result_id=%s fields=%s invalidated_compliance_results=%s", inspection.id, ocr_result.id, {name: {"raw_ocr": field.source_text, "parsed": field.value, "normalized": field.normalized_value} for name, field in fields.items() if field.value}, invalidated)
    return ExtractionResponse(
        inspection_id=inspection.id,
        ocr_result_id=ocr_result.id,
        fields={name: ExtractionFieldResponse(**field.__dict__) for name, field in fields.items()},
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
    fields = {field.field_name: FieldEvidence(value=field.value, confidence=float(field.confidence) if field.confidence is not None else None, needs_review=field.review_status == "REVIEW", evidence=field.value) for field in extracted}
    logger.info("Compliance input inspection_id=%s ocr_result_id=%s fields=%s", inspection.id, ocr_result.id, {name: {"raw": item.value, "normalized": compliance_engine._normalized("NET_QUANTITY_FORMAT" if name == "net_quantity" else "MRP_FORMAT" if name == "mrp" else "", item.value)} for name, item in fields.items()})
    rules = _ensure_compliance_rules(db)
    existing = {result.rule_id: result for result in db.scalars(select(ComplianceResult).where(ComplianceResult.inspection_id == inspection.id)).all()}
    for finding in compliance_engine.evaluate(fields):
        rule = rules.get(finding.rule_id)
        if rule is None or not rule.enabled:
            continue
        result = existing.get(rule.id)
        if result is None:
            result = ComplianceResult(inspection_id=inspection.id, rule_id=rule.id, result=finding.status, explanation=finding.message)
            db.add(result)
        result.result, result.explanation, result.field_name = finding.status, finding.message, finding.field
        result.value, result.confidence, result.evidence = finding.value, finding.confidence, finding.evidence
    db.commit()
    persisted = db.scalars(select(ComplianceResult).where(ComplianceResult.inspection_id == inspection.id).join(ComplianceRule).order_by(ComplianceRule.rule_code)).all()
    response = _compliance_response(inspection.id, persisted)
    logger.info("Compliance persisted inspection_id=%s overall=%s findings=%s", inspection.id, response.overall_status, {finding.rule_id: {"status": finding.status, "reason": finding.message} for finding in response.findings})
    return response


@router.get("/{inspection_id}/compliance", response_model=ComplianceResponse)
def get_inspection_compliance(inspection_id: UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> ComplianceResponse:
    require_owner(db.get(Inspection, inspection_id), user)
    results = db.scalars(select(ComplianceResult).where(ComplianceResult.inspection_id == inspection_id).join(ComplianceRule).order_by(ComplianceRule.rule_code)).all()
    if not results:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Compliance analysis has not been run.")
    return _compliance_response(inspection_id, results)
