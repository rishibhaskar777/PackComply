"""Reliable orchestration for the persisted inspection lifecycle.

An inspection holds up to four photos (one per panel). Each photo is read once
(OCR is stored per image and never repeated for the same checksum), extracted on
its own, then the photos are merged field by field; compliance and risk run once
on the merged extraction, with inspector corrections applied on top.
"""

from __future__ import annotations

import inspect
import logging
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from backend.app.models import ComplianceResult, ComplianceRule, ExtractedField, Inspection, FieldCorrection, InspectionImage, OcrResult, User
from backend.app.schemas.inspection import ComplianceFindingResponse, ComplianceResponse, ComplianceSummary, ExtractionFieldResponse, ExtractionResponse, FieldCorrectionResponse, ImageInfo, ImageUploaded, InspectionSummary, OcrProcessed, QuantityVerificationResponse, ReviewResponse
from backend.app.services.compliance.remediation import remediation_for, rule_chip
from backend.app.services.compliance.remediation import remediation_for, rule_chip
from backend.app.services.compliance.registry import ensure_compliance_rules, results_ruleset, rule_action, rule_expected, rule_legal_reference, rule_title
from backend.app.services.compliance.rule_engine import NOT_APPLICABLE, REVIEW, ComplianceEngine, FieldEvidence, overall_status, verdict
from backend.app.services.corrections import INSPECTOR_PATTERN, SOURCE_INSPECTOR, SOURCE_OCR, apply_corrections, display_name, sync_label_identity
from backend.app.services.extraction.evidence import field_evidence
from backend.app.services.extraction.extraction_service import ExtractionService
from backend.app.services.merge import DEFAULT_PANEL, PhotoExtraction, build_merged_fields, merge_photos
from backend.app.core.config import get_ocr_panel_languages, get_settings
from backend.app.services.progress import DONE, NullReporter, Reporter, Step
from backend.app.services.quantity_verification import QuantityVerificationService
from backend.app.services.ocr.ocr_service import OcrInitializationError, OcrProcessingError, PaddleOcrService
from backend.app.services.risk import RiskService
from backend.app.services.uploads import UploadValidationError, image_dimensions, validate_image

logger = logging.getLogger("uvicorn.error")

MAX_ACTIVE_IMAGES = 4
DEFAULT_OCR_LANGUAGES = ("en", "hi")


def resolve_upload_path(upload_dir: Path, storage_key: str) -> Path | None:
    """Resolve a stored image strictly below the upload root; None if unsafe or missing."""
    root = upload_dir.resolve()
    path = (root / storage_key).resolve()
    return path if path.is_relative_to(root) and path.is_file() else None


# Newest first, deterministic even when two uploads share a created_at.
LATEST_IMAGE_ORDER = (InspectionImage.created_at.desc(), InspectionImage.upload_sequence.desc(), InspectionImage.id.desc())


def latest_image(db: Session, inspection_id: UUID) -> InspectionImage | None:
    return db.scalar(select(InspectionImage).where(InspectionImage.inspection_id == inspection_id).order_by(*LATEST_IMAGE_ORDER).limit(1))


def all_images(db: Session, inspection_id: UUID) -> list[InspectionImage]:
    """Every stored photo, oldest first; replaced photos stay as evidence."""
    return list(db.scalars(select(InspectionImage).where(InspectionImage.inspection_id == inspection_id).order_by(InspectionImage.upload_sequence, InspectionImage.created_at, InspectionImage.id)).all())


def active_images(db: Session, inspection_id: UUID) -> list[InspectionImage]:
    """The photos that are processed: the newest per panel (every OTHER photo counts), in upload order."""
    newest: dict[str, InspectionImage] = {}
    others: list[InspectionImage] = []
    for image in all_images(db, inspection_id):
        if image.panel == "OTHER":
            others.append(image)
        else:
            newest[image.panel] = image
    return sorted([*newest.values(), *others], key=lambda image: (image.upload_sequence, image.created_at))


def primary_image(db: Session, inspection_id: UUID) -> InspectionImage | None:
    """The photo older clients know as "the image": the newest BACK panel, else the newest photo."""
    active = active_images(db, inspection_id)
    back = [image for image in active if image.panel == DEFAULT_PANEL]
    return back[-1] if back else (active[-1] if active else None)


def ocr_of_image(db: Session, image_id: UUID) -> OcrResult | None:
    return db.scalar(select(OcrResult).where(OcrResult.image_id == image_id).order_by(OcrResult.created_at.desc()))


def photo_ocr_pairs(db: Session, inspection_id: UUID) -> list[tuple[InspectionImage | None, OcrResult]]:
    """(photo, its newest OCR) for every active photo that has been read, in upload order.

    OCR stored before photos were linked to it (no image_id) belongs to the primary photo.
    """
    pairs = [(image, ocr) for image in active_images(db, inspection_id) if (ocr := ocr_of_image(db, image.id)) is not None]
    orphan = db.scalar(select(OcrResult).where(OcrResult.inspection_id == inspection_id, OcrResult.image_id.is_(None)).order_by(OcrResult.created_at.desc()))
    if orphan is not None:
        primary = primary_image(db, inspection_id)
        if primary is None or all(image.id != primary.id for image, _ in pairs):
            pairs.insert(0, (primary, orphan))
    return pairs


def latest_extracted_fields(db: Session, inspection_id: UUID) -> list[ExtractedField]:
    """The current merged fields; if older runs left duplicates, the newest row per field wins."""
    rows = db.scalars(select(ExtractedField).where(ExtractedField.inspection_id == inspection_id).order_by(ExtractedField.created_at, ExtractedField.id)).all()
    return list({row.field_name: row for row in rows}.values())


def extraction_field_response(field: ExtractedField, panels: dict[UUID, str] | None = None) -> ExtractionFieldResponse:
    return ExtractionFieldResponse(
        value=field.value, confidence=float(field.confidence or 0), needs_review=field.review_status == "REVIEW", source_text=field.source_text, matched_pattern=field.matched_pattern,
        normalized_value=field.normalized_value, source_bboxes=field.source_bboxes, source=SOURCE_INSPECTOR if field.matched_pattern == INSPECTOR_PATTERN else SOURCE_OCR,
        image_id=field.image_id, panel=(panels or {}).get(field.image_id) if field.image_id else None, alternatives=field.alternatives or [],
    )


def _with_remediation(finding: ComplianceFindingResponse) -> ComplianceFindingResponse:
    """Attach the plain-language fix text for the rule (both roles; officials show it as a hint)."""
    entry = remediation_for(finding.rule_id)
    finding.rule_chip = rule_chip(finding.legal_reference)
    if entry is not None:
        finding.fix_title, finding.fix_steps, finding.example, finding.pass_text = entry.problem_title, list(entry.fix_steps), entry.example, entry.pass_text
    return finding


def _with_remediation(finding: ComplianceFindingResponse) -> ComplianceFindingResponse:
    """Attach the plain-language fix text for the rule (both roles; officials show it as a hint)."""
    entry = remediation_for(finding.rule_id)
    finding.rule_chip = rule_chip(finding.legal_reference)
    if entry is not None:
        finding.fix_title, finding.fix_steps, finding.example, finding.pass_text = entry.problem_title, list(entry.fix_steps), entry.example, entry.pass_text
    return finding


def compliance_response(inspection_id: UUID, results: list[ComplianceResult], extracted: list[ExtractedField]) -> ComplianceResponse:
    boxes = {field.field_name: field.source_bboxes for field in extracted}
    image_ids = {field.field_name: field.image_id for field in extracted}
    corrected = {field.field_name for field in extracted if field.matched_pattern == INSPECTOR_PATTERN}
    findings = [_with_remediation(ComplianceFindingResponse(rule_id=result.rule.rule_code, rule_name=rule_title(result.rule), status=result.result, severity=result.rule.severity, message=result.explanation, field=result.field_name, value=result.value, confidence=float(result.confidence) if result.confidence is not None else None, evidence=result.evidence, expected=rule_expected(result.rule), source_bboxes=boxes.get(result.field_name) if result.field_name and result.result != NOT_APPLICABLE else None, image_id=image_ids.get(result.field_name) if result.field_name and result.result != NOT_APPLICABLE else None, legal_reference=rule_legal_reference(result.rule), recommended_action=rule_action(result.rule, result.result), fields=[result.field_name] if result.field_name else [], corrected=result.field_name in corrected)) for result in results]
    statuses = [finding.status for finding in findings]
    counts = {name: statuses.count(name) for name in ("PASS", "FAIL", "REVIEW", NOT_APPLICABLE)}
    ruleset_id, version, label = results_ruleset(results)
    return ComplianceResponse(inspection_id=inspection_id, overall_status=overall_status(statuses), verdict=verdict(statuses), summary=ComplianceSummary(passed=counts["PASS"], failed=counts["FAIL"], review=counts["REVIEW"], not_applicable=counts[NOT_APPLICABLE]), findings=findings, ruleset_id=ruleset_id, ruleset_version=version, ruleset_label=label)


def _conflict_note(alternatives: list[dict], extracted_value: str | None, panel: str | None) -> str:
    seen = [f"{extracted_value} ({_panel_word(panel)})"] + [f"{item.get('value')} ({_panel_word(item.get('panel'))})" for item in alternatives]
    return "Different photos read different values: " + "; ".join(seen) + ". Confirm on the pack."


def _panel_word(panel: str | None) -> str:
    return {"BACK": "back", "FRONT": "front", "DATE_STAMP": "date stamp", "OTHER": "other photo"}.get(panel or "", "photo")


def persist_compliance(db: Session, engine: ComplianceEngine, inspection_id: UUID, fields: dict[str, FieldEvidence], conflicts: dict[str, str] | None = None, unread: list[str] | None = None) -> None:
    """Evaluate and store one result per enabled rule, replacing this inspection's previous results.

    ``conflicts`` maps a field read differently on different photos to a note: a rule that depends on
    it never passes silently (PASS becomes REVIEW); a FAIL or REVIEW keeps its status and gains the note.
    ``unread`` names photos whose text could not be read. A declaration missing from the photos that were read may be
    on one of them, so a FAIL for "not found" becomes REVIEW; a violation visible in a value that was read stays FAIL.
    """
    ruleset = engine.ruleset
    rules = ensure_compliance_rules(db, ruleset)
    existing = {result.rule_id: result for result in db.scalars(select(ComplianceResult).where(ComplianceResult.inspection_id == inspection_id)).all()}
    targets = {rule.code: set(rule.target_fields) for rule in ruleset.rules}
    current = set()
    for finding in engine.evaluate(fields):
        rule = rules.get(finding.rule_id)
        if rule is None or not rule.enabled:
            continue
        result = existing.get(rule.id)
        if result is None:
            result = ComplianceResult(inspection_id=inspection_id, rule_id=rule.id, result=finding.status, explanation=finding.message)
            db.add(result)
        status, message = finding.status, finding.message
        notes = [note for name, note in (conflicts or {}).items() if name in targets.get(finding.rule_id, ()) and status != NOT_APPLICABLE]
        if notes:
            status = REVIEW if status == "PASS" else status
            message = f"{message} {' '.join(notes)}".strip()
        if unread and status != NOT_APPLICABLE:
            note = f"The {' and '.join(unread)} photo could not be read, so this could not be confirmed. Retry that photo."
            missing = all(not (fields.get(name) and fields[name].value) for name in targets.get(finding.rule_id, ()))
            if status == "FAIL" and missing:
                status, message = REVIEW, f"{message} {note}".strip()
            elif status == REVIEW:
                message = f"{message} {note}".strip()
        result.result, result.explanation, result.field_name = status, message, finding.field
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
    def __init__(self, stage: str, message: str, status_code: int = 422, step_id: str | None = None):
        self.stage, self.message, self.status_code, self.step_id = stage, message, status_code, step_id
        super().__init__(message)


_FRIENDLY = {
    "OCR": "We couldn't read the text on this photo. Check it is sharp and well lit, then try again.",
    "extraction": "We couldn't link the declarations on this label. Try again.",
    "compliance": "We couldn't finish checking the rules. Try again.",
}


def friendly_message(error: WorkflowError) -> str:
    if error.stage == "OCR" and error.status_code == 503:
        return "The text-reading engine isn't ready yet. Wait a moment, then try again."
    return _FRIENDLY.get(error.stage, error.message)


class InspectionWorkflow:
    def __init__(self, db: Session, ocr: PaddleOcrService, extraction: ExtractionService, compliance: ComplianceEngine, upload_dir: Path, max_upload_bytes: int, pattern_min_occurrences: int = 2):
        self.db, self.ocr, self.extraction, self.compliance = db, ocr, extraction, compliance
        self.upload_dir, self.max_upload_bytes, self.pattern_min_occurrences = upload_dir, max_upload_bytes, pattern_min_occurrences

    # ------------------------------------------------------------ planning

    @property
    def languages(self) -> tuple[str, ...]:
        return tuple(getattr(self.ocr, "languages", None) or DEFAULT_OCR_LANGUAGES)

    def pending_images(self, inspection_id: UUID) -> list[InspectionImage]:
        """Active photos that still need reading."""
        return [image for image in active_images(self.db, inspection_id) if ocr_of_image(self.db, image.id) is None and not self._legacy_ocr_covers(inspection_id, image)]

    def _legacy_ocr_covers(self, inspection_id: UUID, image: InspectionImage) -> bool:
        primary = primary_image(self.db, inspection_id)
        return primary is not None and primary.id == image.id and self.db.scalar(select(OcrResult.id).where(OcrResult.inspection_id == inspection_id, OcrResult.image_id.is_(None)).limit(1)) is not None

    def languages_for(self, panel: str | None) -> tuple[str, ...]:
        """The languages read on a photo of this panel (OCR_PANEL_LANGUAGES), limited to what the engine has."""
        try:
            wanted = get_ocr_panel_languages(get_settings()).get(panel or "")
        except ValueError:
            wanted = None
        available = self.languages
        chosen = tuple(code for code in (wanted or available) if code in available)
        return chosen or available

    def plan(self, inspection_id: UUID) -> list[Step]:
        """The steps this run will go through, known before it starts.

        A photo is one "read" step however many languages run on it (they run in parallel). A photo
        already read while the inspector was capturing the next panel appears as a finished step.
        """
        steps: list[Step] = []
        pending = {image.id for image in self.pending_images(inspection_id)}
        for image in active_images(self.db, inspection_id):
            key = str(image.id)
            languages = list(self.languages_for(image.panel))
            if image.id in pending:
                steps.append(Step(f"{key}:prepare", "prepare", f"Preparing photo ({_panel_word(image.panel)})", panel=image.panel, image_id=key))
                steps.append(Step(f"{key}:read", "read", "Reading text", panel=image.panel, languages=languages, image_id=key))
                continue
            ocr = ocr_of_image(self.db, image.id)
            steps.append(Step(f"{key}:read", "read", "Reading text", status=DONE, panel=image.panel, languages=list(ocr.languages or languages) if ocr else languages, image_id=key, cached=not (ocr and ocr.duration_ms), duration_ms=ocr.duration_ms if ocr else None))
        rules = len(self.compliance.ruleset.enabled_rules)
        return steps + [
            Step("link", "link", "Linking declarations"),
            Step("rules", "rules", f"Checking {rules} rules", count=rules),
            Step("risk", "risk", "Scoring risk"),
        ]

    # ----------------------------------------------------------- lifecycle

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

    def process(self, inspection_id: UUID, reporter: Reporter | None = None) -> InspectionSummary:
        """Read every unread photo, merge, check the rules and score risk; safe to call again after success or failure."""
        reporter = reporter or NullReporter()
        inspection = self.db.get(Inspection, inspection_id)
        if inspection is None:
            raise KeyError("Inspection not found.")
        images = active_images(self.db, inspection.id)
        if not images:
            raise WorkflowError("image", "Upload a package-label image before processing.", 409)
        pending = self.pending_images(inspection.id)
        if inspection.status == "COMPLETED" and not pending:
            return self.summary(inspection.id)

        if pending:
            self._set_status(inspection, "OCR_PROCESSING")
        unreadable: list[tuple[InspectionImage, Exception, str]] = []
        for image in pending:
            try:
                self._read_image(inspection, image, reporter, persist_failure=False)
            except Exception as error:  # noqa: BLE001 - one photo that cannot be read must not stop the others
                self.db.rollback()
                message = "The text-reading engine was not ready." if isinstance(error, OcrInitializationError) or getattr(error, "status_code", None) == 503 else "The text on this photo could not be read."
                if not isinstance(error, WorkflowError):
                    logger.exception("Reading a photo failed inspection_id=%s image_id=%s", inspection.id, image.id)
                image.read_error = message
                self.db.commit()
                for step_id in (f"{image.id}:prepare", f"{image.id}:read"):
                    reporter.skip(step_id, message)
                unreadable.append((image, error, message))
            else:
                if image.read_error:
                    image.read_error = None
                    self.db.commit()
        reporter.current_image(None)
        if unreadable and not photo_ocr_pairs(self.db, inspection.id):
            # Nothing at all could be read: there is no honest result to show, so the run stops here and can be retried.
            image, error, _ = unreadable[-1]
            failed = self._fail(inspection, "OCR", error)
            failed.step_id = f"{image.id}:read"
            if getattr(error, "status_code", None) == 503:
                failed.status_code = 503
            raise failed
        self._set_status(inspection, "OCR_COMPLETED")

        reporter.start("link")
        self._set_status(inspection, "EXTRACTION_PROCESSING")
        pairs = photo_ocr_pairs(self.db, inspection.id)
        try:
            self.db.execute(delete(ExtractedField).where(ExtractedField.inspection_id == inspection.id))
            self.db.execute(delete(ComplianceResult).where(ComplianceResult.inspection_id == inspection.id))
            extracted = self._extract(inspection, pairs)
        except Exception as error:
            self.db.rollback()
            raise self._fail(inspection, "extraction", error) from error
        self._set_status(inspection, "EXTRACTION_COMPLETED")
        reporter.done("link")

        reporter.start("rules")
        self._set_status(inspection, "COMPLIANCE_PROCESSING")
        try:
            self._evaluate(inspection, extracted)
        except Exception as error:
            self.db.rollback()
            raise self._fail(inspection, "compliance", error) from error
        inspection.status, inspection.completed_at = "COMPLETED", datetime.now(timezone.utc)
        inspection.failure_stage = inspection.failure_message = None
        self.db.commit()
        reporter.done("rules")

        reporter.start("risk")
        try:
            RiskService(self.db, self.pattern_min_occurrences).for_inspection(inspection)
        except Exception as error:
            self.db.rollback()
            logger.exception("Risk scoring failed inspection_id=%s", inspection.id)
            raise WorkflowError("risk", "Risk could not be scored. Try again.", 422) from error
        reporter.done("risk")
        return self.summary(inspection.id)

    def _scan(self, path: Path, on_language, languages: tuple[str, ...]) -> object:
        parameters = inspect.signature(self.ocr.scan).parameters
        kwargs = {}
        if "progress" in parameters:
            kwargs["progress"] = on_language
        if "languages" in parameters and tuple(languages) != self.languages:
            kwargs["languages"] = languages
        return self.ocr.scan(path, **kwargs)

    def read_ahead(self, inspection_id: UUID, image_id: UUID) -> bool:
        """Read one photo now, before the inspection is processed. Returns True if OCR was run.

        Only the OCR result is stored: the inspection's status and failure fields are left alone, and a
        failure here is not final (the later process call reads the photo again).
        """
        inspection = self.db.get(Inspection, inspection_id)
        image = self.db.get(InspectionImage, image_id)
        if inspection is None or image is None or image.inspection_id != inspection.id:
            return False
        if ocr_of_image(self.db, image.id) is not None:
            return False
        self._read_image(inspection, image, NullReporter(), persist_failure=False)
        return True

    def _read_image(self, inspection: Inspection, image: InspectionImage, reporter: Reporter, persist_failure: bool = True) -> None:
        """OCR one photo (or reuse the reading of an identical file), and store it against the photo."""
        key = str(image.id)
        languages = self.languages_for(image.panel)

        def failure(error: Exception) -> WorkflowError:
            if persist_failure:
                return self._fail(inspection, "OCR", error)
            self.db.rollback()
            return WorkflowError("OCR", "We couldn't read the text on this photo.", 503 if isinstance(error, OcrInitializationError) else 422)

        reporter.current_image(key)
        reporter.start(f"{key}:prepare")
        path = resolve_upload_path(self.upload_dir, image.storage_key)
        try:
            if path is None:
                raise FileNotFoundError("Uploaded image is unavailable")
            validate_image(path.read_bytes(), image.content_type, self.max_upload_bytes)
            if image.width is None:
                self._backfill_dimensions(image)
        except (UploadValidationError, OSError) as error:
            raise failure(error) from error
        reporter.done(f"{key}:prepare")

        earlier = self._same_checksum_ocr(inspection.id, image)
        if earlier is not None and set(languages) <= set(earlier.languages or languages):
            self.db.add(OcrResult(inspection_id=inspection.id, image_id=image.id, engine=earlier.engine, languages=earlier.languages, raw_text=earlier.raw_text, detections=earlier.detections, duration_ms=0))
            self.db.commit()
            reporter.done(f"{key}:read", cached=True)
            return

        # English and Hindi run in parallel: one visible step spans them all.
        remaining = set(languages)

        def on_language(code: str, state: str) -> None:
            if state == "start":
                reporter.start(f"{key}:read")
            else:
                remaining.discard(code)
                if not remaining:
                    reporter.done(f"{key}:read")

        try:
            started = perf_counter()
            document = self._scan(path, on_language, languages)
            ocr_result = OcrResult(inspection_id=inspection.id, image_id=image.id, engine=document.engine, languages=document.languages, raw_text=document.raw_text, detections=document.as_database_detections(), duration_ms=round((perf_counter() - started) * 1000))
            self.db.add(ocr_result)
            self.db.commit()
        except (OcrInitializationError, OcrProcessingError, UploadValidationError, OSError) as error:
            self.db.rollback()
            raise failure(error) from error
        reporter.done(f"{key}:read")

    def _same_checksum_ocr(self, inspection_id: UUID, image: InspectionImage) -> OcrResult | None:
        if not image.checksum:
            return None
        return self.db.scalar(
            select(OcrResult).join(InspectionImage, InspectionImage.id == OcrResult.image_id)
            .where(OcrResult.inspection_id == inspection_id, InspectionImage.checksum == image.checksum, InspectionImage.id != image.id)
            .order_by(OcrResult.created_at.desc())
        )

    def _extract(self, inspection: Inspection, pairs: list[tuple[InspectionImage | None, OcrResult]]) -> list[ExtractedField]:
        """Extract each photo, merge the fields, store them and lay inspector corrections on top."""
        photos = []
        for image, ocr in pairs:
            values = self.extraction.extract(ocr.raw_text, ocr.detections)
            logger.info("Workflow extraction inspection_id=%s ocr_result_id=%s fields=%s", inspection.id, ocr.id, {name: {"raw_ocr": value.source_text, "parsed": value.value, "normalized": value.normalized_value} for name, value in values.items() if value.value})
            photos.append(PhotoExtraction(image.id if image else None, image.panel if image else DEFAULT_PANEL, ocr, values))
        rows = build_merged_fields(inspection.id, merge_photos(photos))
        self.db.add_all(rows)
        self.db.commit()
        extracted = list(self.db.scalars(select(ExtractedField).where(ExtractedField.inspection_id == inspection.id)).all())
        # Inspector corrections always sit on top of the latest extraction.
        return apply_corrections(self.db, inspection.id, pairs[0][1] if pairs else None, extracted)

    def _evaluate(self, inspection: Inspection, extracted: list[ExtractedField]) -> None:
        fields = {field.field_name: field_evidence(field) for field in extracted}
        logger.info("Workflow compliance input inspection_id=%s fields=%s", inspection.id, {name: {"raw": item.value, "normalized": self.compliance._normalized("NET_QUANTITY_FORMAT" if name == "net_quantity" else "MRP_FORMAT" if name == "mrp" else "", item.value)} for name, item in fields.items()})
        panels = {image.id: image.panel for image in all_images(self.db, inspection.id)}
        conflicts = {field.field_name: _conflict_note(field.alternatives, field.value, panels.get(field.image_id)) for field in extracted if field.alternatives and field.matched_pattern != INSPECTOR_PATTERN}
        unread = [_panel_word(image.panel) for image in active_images(self.db, inspection.id) if image.read_error and ocr_of_image(self.db, image.id) is None]
        persist_compliance(self.db, self.compliance, inspection.id, fields, conflicts, unread)
        sync_label_identity(self.db, inspection, extracted)

    def reanalyse(self, inspection_id: UUID) -> InspectionSummary:
        """Re-merge and re-check every photo's stored OCR, without new OCR.

        Replaces this inspection's ExtractedField and ComplianceResult rows; the
        OCR evidence itself is never modified.
        """
        inspection = self.db.get(Inspection, inspection_id)
        if inspection is None:
            raise KeyError("Inspection not found.")
        pairs = photo_ocr_pairs(self.db, inspection.id)
        if not pairs:
            raise WorkflowError("OCR", "OCR must be run before the inspection can be re-analysed.", 409)
        try:
            self.db.execute(delete(ExtractedField).where(ExtractedField.inspection_id == inspection.id))
            self.db.execute(delete(ComplianceResult).where(ComplianceResult.inspection_id == inspection.id))
            extracted = self._extract(inspection, pairs)
        except Exception as error:
            self.db.rollback()
            raise self._fail(inspection, "extraction", error) from error
        try:
            self._evaluate(inspection, extracted)
        except Exception as error:
            self.db.rollback()
            raise self._fail(inspection, "compliance", error) from error
        inspection.status, inspection.completed_at = "COMPLETED", inspection.completed_at or datetime.now(timezone.utc)
        inspection.failure_stage = inspection.failure_message = None
        self.db.commit()
        logger.info("Inspection re-analysed inspection_id=%s photos=%d fields=%d", inspection.id, len(pairs), sum(field.value is not None for field in extracted))
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
        stored = all_images(self.db, inspection.id)
        active = {image.id for image in active_images(self.db, inspection.id)}
        image = primary_image(self.db, inspection.id)
        if image is not None and image.width is None:
            self._backfill_dimensions(image)
        panels = {row.id: row.panel for row in stored}
        pairs = photo_ocr_pairs(self.db, inspection.id)
        ocr = next((ocr for photo, ocr in pairs if image is not None and photo is not None and photo.id == image.id), None) or (pairs[-1][1] if pairs else None)
        extracted = latest_extracted_fields(self.db, inspection.id) if pairs else []
        results = self.db.scalars(select(ComplianceResult).where(ComplianceResult.inspection_id == inspection.id).join(ComplianceRule).order_by(ComplianceRule.rule_code)).all()
        ocr_response = OcrProcessed(id=ocr.id, inspection_id=ocr.inspection_id, image_id=ocr.image_id, engine=ocr.engine, languages=ocr.languages, processed_at=ocr.created_at, raw_text=ocr.raw_text, results=ocr.detections, duration_ms=ocr.duration_ms) if ocr else None
        extraction_response = ExtractionResponse(inspection_id=inspection.id, ocr_result_id=ocr.id, fields={field.field_name: extraction_field_response(field, panels) for field in extracted}) if ocr and extracted else None
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
        images = [ImageInfo(id=row.id, panel=row.panel, width=row.width, height=row.height, checksum=row.checksum, uploaded_at=row.created_at, active=row.id in active, original_filename=row.original_filename, byte_size=row.byte_size, content_type=row.content_type, read_error=row.read_error) for row in stored]
        return InspectionSummary(product_name=display_name(inspection), brand_name=inspection.brand_name, maker_name=inspection.maker_name, corrections=correction_responses, review=review, previous_inspection_id=inspection.previous_inspection_id, id=inspection.id, inspection_number=inspection.inspection_number, status=inspection.status, created_at=inspection.created_at, updated_at=inspection.updated_at, completed_at=inspection.completed_at, failure_stage=inspection.failure_stage, failure_message=inspection.failure_message, image=ImageUploaded.model_validate(image) if image else None, images=images, ocr=ocr_response, extraction=extraction_response, compliance=compliance, quantity_verification=QuantityVerificationResponse(**quantity.__dict__) if quantity else None)
