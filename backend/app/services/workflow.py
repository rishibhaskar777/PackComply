"""Reliable orchestration for the persisted inspection lifecycle."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

from time import perf_counter

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from backend.app.models import ComplianceResult, ComplianceRule, ExtractedField, Inspection, FieldCorrection, InspectionImage, OcrResult, User
from backend.app.schemas.inspection import ComplianceFindingResponse, ComplianceResponse, ComplianceSummary, ExtractionFieldResponse, ExtractionResponse, FieldCorrectionResponse, ImageUploaded, InspectionSummary, OcrProcessed, QuantityVerificationResponse, ReviewResponse
from backend.app.services.compliance.registry import ensure_compliance_rules, results_ruleset, rule_action, rule_expected, rule_legal_reference, rule_title
from backend.app.services.compliance.rule_engine import NOT_APPLICABLE, ComplianceEngine, FieldEvidence, overall_status, verdict
from backend.app.services.corrections import INSPECTOR_PATTERN, SOURCE_INSPECTOR, SOURCE_OCR, apply_corrections, display_name, sync_label_identity
from backend.app.services.extraction.evidence import build_extracted_fields, field_evidence
from backend.app.services.extraction.extraction_service import ExtractionService
from backend.app.services.quantity_verification import QuantityVerificationService
from backend.app.services.ocr.ocr_service import OcrInitializationError, OcrProcessingError, PaddleOcrService
from backend.app.services.uploads import UploadValidationError, image_dimensions, validate_image

logger = logging.getLogger("uvicorn.error")


def resolve_upload_path(upload_dir: Path, storage_key: str) -> Path | None:
    """Resolve a stored image strictly below the upload root; None if unsafe or missing."""
    root = upload_dir.resolve()
    path = (root / storage_key).resolve()
    return path if path.is_relative_to(root) and path.is_file() else None


# Newest first, deterministic even when two uploads share a created_at.
LATEST_IMAGE_ORDER = (InspectionImage.created_at.desc(), InspectionImage.upload_sequence.desc(), InspectionImage.id.desc())


def latest_image(db: Session, inspection_id: UUID) -> InspectionImage | None:
    return db.scalar(select(InspectionImage).where(InspectionImage.inspection_id == inspection_id).order_by(*LATEST_IMAGE_ORDER).limit(1))


def latest_extracted_fields(db: Session, inspection_id: UUID) -> list[ExtractedField]:
    ocr_id = db.scalar(select(OcrResult.id).where(OcrResult.inspection_id == inspection_id).order_by(OcrResult.created_at.desc()))
    return list(db.scalars(select(ExtractedField).where(ExtractedField.ocr_result_id == ocr_id)).all()) if ocr_id else []


def extraction_field_response(field: ExtractedField) -> ExtractionFieldResponse:
    return ExtractionFieldResponse(value=field.value, confidence=float(field.confidence or 0), needs_review=field.review_status == "REVIEW", source_text=field.source_text, matched_pattern=field.matched_pattern, normalized_value=field.normalized_value, source_bboxes=field.source_bboxes, source=SOURCE_INSPECTOR if field.matched_pattern == INSPECTOR_PATTERN else SOURCE_OCR)


def compliance_response(inspection_id: UUID, results: list[ComplianceResult], extracted: list[ExtractedField]) -> ComplianceResponse:
    boxes = {field.field_name: field.source_bboxes for field in extracted}
    corrected = {field.field_name for field in extracted if field.matched_pattern == INSPECTOR_PATTERN}
    findings = [ComplianceFindingResponse(rule_id=result.rule.rule_code, rule_name=rule_title(result.rule), status=result.result, severity=result.rule.severity, message=result.explanation, field=result.field_name, value=result.value, confidence=float(result.confidence) if result.confidence is not None else None, evidence=result.evidence, expected=rule_expected(result.rule), source_bboxes=boxes.get(result.field_name) if result.field_name and result.result != NOT_APPLICABLE else None, legal_reference=rule_legal_reference(result.rule), recommended_action=rule_action(result.rule, result.result), fields=[result.field_name] if result.field_name else [], corrected=result.field_name in corrected) for result in results]
    statuses = [finding.status for finding in findings]
    counts = {name: statuses.count(name) for name in ("PASS", "FAIL", "REVIEW", NOT_APPLICABLE)}
    ruleset_id, version, label = results_ruleset(results)
    return ComplianceResponse(inspection_id=inspection_id, overall_status=overall_status(statuses), verdict=verdict(statuses), summary=ComplianceSummary(passed=counts["PASS"], failed=counts["FAIL"], review=counts["REVIEW"], not_applicable=counts[NOT_APPLICABLE]), findings=findings, ruleset_id=ruleset_id, ruleset_version=version, ruleset_label=label)


def persist_compliance(db: Session, engine: ComplianceEngine, inspection_id: UUID, fields: dict[str, FieldEvidence]) -> None:
    """Evaluate and store one result per enabled rule, replacing this inspection's previous results."""
    ruleset = engine.ruleset
    rules = ensure_compliance_rules(db, ruleset)
    existing = {result.rule_id: result for result in db.scalars(select(ComplianceResult).where(ComplianceResult.inspection_id == inspection_id)).all()}
    current = set()
    for finding in engine.evaluate(fields):
        rule = rules.get(finding.rule_id)
        if rule is None or not rule.enabled:
            continue
        result = existing.get(rule.id)
        if result is None:
            result = ComplianceResult(inspection_id=inspection_id, rule_id=rule.id, result=finding.status, explanation=finding.message)
            db.add(result)
        result.result, result.explanation, result.field_name = finding.status, finding.message, finding.field
        result.value, result.confidence, result.evidence = finding.value, finding.confidence, finding.evidence
        result.ruleset_id, result.ruleset_version = ruleset.ruleset_id, ruleset.version
        current.add(rule.id)
    # A result from a rule that is now disabled would mix two rulesets in one run.
    for rule_id, result in existing.items():
        if rule_id not in current:
            db.delete(result)
    db.commit()


def user_names(db: Session, ids: list[UUID | None]) -> dict[UUID, str]:
    wanted = {user_id for user_id in ids if user_id}
    return {user.id: user.full_name for user in db.scalars(select(User).where(User.id.in_(wanted))).all()} if wanted else {}


class WorkflowError(Exception):
    def __init__(self, stage: str, message: str, status_code: int = 422):
        self.stage, self.message, self.status_code = stage, message, status_code
        super().__init__(message)


class InspectionWorkflow:
    def __init__(self, db: Session, ocr: PaddleOcrService, extraction: ExtractionService, compliance: ComplianceEngine, upload_dir: Path, max_upload_bytes: int):
        self.db, self.ocr, self.extraction, self.compliance = db, ocr, extraction, compliance
        self.upload_dir, self.max_upload_bytes = upload_dir, max_upload_bytes

    def _set_status(self, inspection: Inspection, status: str) -> None:
        inspection.status = status
        inspection.failure_stage = None
        inspection.failure_message = None
        self.db.commit()

    def _fail(self, inspection: Inspection, stage: str, error: Exception) -> WorkflowError:
        # Diagnostic detail remains in server logs; the persisted message is safe to show.
        logger.exception("Inspection workflow failed inspection_id=%s stage=%s", inspection.id, stage)
        inspection.status, inspection.failure_stage = "FAILED", stage
        inspection.failure_message = f"{stage.title()} could not be completed. Please retry the inspection."
        self.db.commit()
        return WorkflowError(stage, inspection.failure_message, 503 if isinstance(error, OcrInitializationError) else 422)

    def process(self, inspection_id: UUID) -> InspectionSummary:
        inspection = self.db.get(Inspection, inspection_id)
        if inspection is None:
            raise KeyError("Inspection not found.")
        image = latest_image(self.db, inspection.id)
        if image is None:
            raise WorkflowError("image", "Upload a package-label image before processing.", 409)
        if inspection.status == "COMPLETED":
            return self.summary(inspection.id)

        ocr_result = self.db.scalar(select(OcrResult).where(OcrResult.inspection_id == inspection.id, OcrResult.image_id == image.id).order_by(OcrResult.created_at.desc()))
        if ocr_result is None:
            self._set_status(inspection, "OCR_PROCESSING")
            path = resolve_upload_path(self.upload_dir, image.storage_key)
            try:
                if path is None:
                    raise FileNotFoundError("Uploaded image is unavailable")
                validate_image(path.read_bytes(), image.content_type, self.max_upload_bytes)
                started = perf_counter()
                document = self.ocr.scan(path)
                ocr_result = OcrResult(inspection_id=inspection.id, image_id=image.id, engine=document.engine, languages=document.languages, raw_text=document.raw_text, detections=document.as_database_detections(), duration_ms=round((perf_counter() - started) * 1000))
                self.db.add(ocr_result); self.db.commit(); self.db.refresh(ocr_result)
            except (OcrInitializationError, OcrProcessingError, UploadValidationError, OSError) as error:
                raise self._fail(inspection, "OCR", error) from error
        self._set_status(inspection, "OCR_COMPLETED")

        extracted = self.db.scalars(select(ExtractedField).where(ExtractedField.ocr_result_id == ocr_result.id)).all()
        if not extracted:
            self._set_status(inspection, "EXTRACTION_PROCESSING")
            try:
                extracted = self._extract(inspection, ocr_result)
            except Exception as error:
                self.db.rollback()
                raise self._fail(inspection, "extraction", error) from error
        self._set_status(inspection, "EXTRACTION_COMPLETED")

        persisted = self.db.scalars(select(ComplianceResult).where(ComplianceResult.inspection_id == inspection.id)).all()
        if not persisted:
            self._set_status(inspection, "COMPLIANCE_PROCESSING")
            try:
                self._evaluate(inspection, ocr_result, extracted)
            except Exception as error:
                self.db.rollback()
                raise self._fail(inspection, "compliance", error) from error
        inspection.status, inspection.completed_at = "COMPLETED", datetime.now(timezone.utc)
        inspection.failure_stage = inspection.failure_message = None
        self.db.commit()
        return self.summary(inspection.id)

    def _extract(self, inspection: Inspection, ocr_result: OcrResult) -> list[ExtractedField]:
        values = self.extraction.extract(ocr_result.raw_text, ocr_result.detections)
        logger.info("Workflow extraction inspection_id=%s ocr_result_id=%s fields=%s", inspection.id, ocr_result.id, {name: {"raw_ocr": value.source_text, "parsed": value.value, "normalized": value.normalized_value} for name, value in values.items() if value.value})
        self.db.add_all(build_extracted_fields(inspection.id, ocr_result, values))
        self.db.commit()
        extracted = list(self.db.scalars(select(ExtractedField).where(ExtractedField.ocr_result_id == ocr_result.id)).all())
        # Inspector corrections always sit on top of the latest extraction.
        return apply_corrections(self.db, inspection.id, ocr_result, extracted)

    def _evaluate(self, inspection: Inspection, ocr_result: OcrResult, extracted: list[ExtractedField]) -> None:
        fields = {field.field_name: field_evidence(field) for field in extracted}
        logger.info("Workflow compliance input inspection_id=%s ocr_result_id=%s fields=%s", inspection.id, ocr_result.id, {name: {"raw": item.value, "normalized": self.compliance._normalized("NET_QUANTITY_FORMAT" if name == "net_quantity" else "MRP_FORMAT" if name == "mrp" else "", item.value)} for name, item in fields.items()})
        persist_compliance(self.db, self.compliance, inspection.id, fields)
        sync_label_identity(self.db, inspection, extracted)

    def reanalyse(self, inspection_id: UUID) -> InspectionSummary:
        """Re-run extraction and compliance on the latest stored OCR result, without new OCR.

        Replaces this inspection's ExtractedField and ComplianceResult rows; the
        OCR evidence itself is never modified.
        """
        inspection = self.db.get(Inspection, inspection_id)
        if inspection is None:
            raise KeyError("Inspection not found.")
        ocr_result = self.db.scalar(select(OcrResult).where(OcrResult.inspection_id == inspection.id).order_by(OcrResult.created_at.desc()))
        if ocr_result is None:
            raise WorkflowError("OCR", "OCR must be run before the inspection can be re-analysed.", 409)
        try:
            self.db.execute(delete(ExtractedField).where(ExtractedField.inspection_id == inspection.id))
            self.db.execute(delete(ComplianceResult).where(ComplianceResult.inspection_id == inspection.id))
            extracted = self._extract(inspection, ocr_result)
        except Exception as error:
            self.db.rollback()
            raise self._fail(inspection, "extraction", error) from error
        try:
            self._evaluate(inspection, ocr_result, extracted)
        except Exception as error:
            self.db.rollback()
            raise self._fail(inspection, "compliance", error) from error
        inspection.status, inspection.completed_at = "COMPLETED", inspection.completed_at or datetime.now(timezone.utc)
        inspection.failure_stage = inspection.failure_message = None
        self.db.commit()
        logger.info("Inspection re-analysed inspection_id=%s ocr_result_id=%s fields=%d", inspection.id, ocr_result.id, sum(field.value is not None for field in extracted))
        return self.summary(inspection.id)

    def _backfill_dimensions(self, image: InspectionImage) -> None:
        # Images uploaded before dimensions were recorded are measured once, on first read.
        path = resolve_upload_path(self.upload_dir, image.storage_key)
        size = image_dimensions(path) if path else None
        if size:
            image.width, image.height = size
            self.db.commit()

    def summary(self, inspection_id: UUID) -> InspectionSummary:
        inspection = self.db.get(Inspection, inspection_id)
        if inspection is None:
            raise KeyError("Inspection not found.")
        image = latest_image(self.db, inspection.id)
        if image is not None and image.width is None:
            self._backfill_dimensions(image)
        ocr = self.db.scalar(select(OcrResult).where(OcrResult.inspection_id == inspection.id).order_by(OcrResult.created_at.desc()))
        extracted = self.db.scalars(select(ExtractedField).where(ExtractedField.ocr_result_id == ocr.id)).all() if ocr else []
        results = self.db.scalars(select(ComplianceResult).where(ComplianceResult.inspection_id == inspection.id).join(ComplianceRule).order_by(ComplianceRule.rule_code)).all()
        ocr_response = OcrProcessed(id=ocr.id, inspection_id=ocr.inspection_id, image_id=ocr.image_id, engine=ocr.engine, languages=ocr.languages, processed_at=ocr.created_at, raw_text=ocr.raw_text, results=ocr.detections, duration_ms=ocr.duration_ms) if ocr else None
        extraction_response = ExtractionResponse(inspection_id=inspection.id, ocr_result_id=ocr.id, fields={field.field_name: extraction_field_response(field) for field in extracted}) if ocr and extracted else None
        compliance = compliance_response(inspection.id, results, extracted) if results else None
        quantity_field = next((field for field in extracted if field.field_name == "net_quantity"), None)
        quantity_result = next((result for result in results if result.rule.rule_code == "NET_QUANTITY_FORMAT"), None)
        quantity = QuantityVerificationService().verify(
            quantity_field.value if quantity_field else None,
            status=quantity_result.result if quantity_result else None,
            reason=quantity_result.explanation if quantity_result else None,
            severity=quantity_result.rule.severity if quantity_result else None,
            evidence=quantity_result.evidence if quantity_result else (quantity_field.value if quantity_field else None),
            requirement=quantity_result.rule.description if quantity_result else None,
        ) if quantity_field or quantity_result else None
        corrections = list(self.db.scalars(select(FieldCorrection).where(FieldCorrection.inspection_id == inspection.id).order_by(FieldCorrection.created_at, FieldCorrection.id)).all())
        names = user_names(self.db, [row.corrected_by for row in corrections] + [inspection.reviewed_by])
        correction_responses = [FieldCorrectionResponse(id=row.id, field_name=row.field_name, original_value=row.original_value, corrected_value=row.corrected_value, reason=row.reason, corrected_by=row.corrected_by, corrected_by_name=names.get(row.corrected_by), created_at=row.created_at) for row in corrections]
        review = ReviewResponse(status=inspection.review_status or "UNREVIEWED", by=names.get(inspection.reviewed_by), at=inspection.reviewed_at, remarks=inspection.remarks)
        return InspectionSummary(product_name=display_name(inspection), brand_name=inspection.brand_name, maker_name=inspection.maker_name, corrections=correction_responses, review=review, id=inspection.id, inspection_number=inspection.inspection_number, status=inspection.status, created_at=inspection.created_at, updated_at=inspection.updated_at, completed_at=inspection.completed_at, failure_stage=inspection.failure_stage, failure_message=inspection.failure_message, image=ImageUploaded.model_validate(image) if image else None, ocr=ocr_response, extraction=extraction_response, compliance=compliance, quantity_verification=QuantityVerificationResponse(**quantity.__dict__) if quantity else None)
