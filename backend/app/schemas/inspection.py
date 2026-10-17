from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class InspectionCreate(BaseModel):
    manufacturer_id: UUID | None = None
    product_id: UUID | None = None
    notes: str | None = Field(default=None, max_length=5000)


class InspectionCreated(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    inspection_number: str
    status: str
    inspected_at: datetime

class InspectionHistoryItem(BaseModel):
    id: UUID
    inspection_number: str
    status: str
    created_at: datetime
    updated_at: datetime
    product_name: str | None = None
    brand_name: str | None = None
    maker_name: str | None = None
    review_status: str = "UNREVIEWED"
    compliance_status: str | None = None
    image_filename: str | None = None
    # NON_COMPLIANT / NEEDS_REVIEW / COMPLIANT, derived from compliance_status.
    verdict: str | None = None

class InspectionHistoryPage(BaseModel):
    items: list[InspectionHistoryItem]
    page: int
    page_size: int
    total: int
    total_pages: int


class PatternResponse(BaseModel):
    pattern_key: str
    pattern_type: str
    title: str
    description: str
    occurrence_count: int
    first_seen: datetime
    last_seen: datetime
    severity: str
    inspection_ids: list[UUID]
    affected_field: str | None = None
    rule_code: str | None = None


class ImageUploaded(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    inspection_id: UUID
    original_filename: str
    content_type: str
    byte_size: int
    created_at: datetime
    width: int | None = None
    height: int | None = None
    # SHA-256 of the stored original, recorded as evidence.
    checksum: str | None = None
    # BACK | FRONT | DATE_STAMP | OTHER
    panel: str = "BACK"


class ImageInfo(BaseModel):
    """One stored photo. Photos replaced for their panel stay listed (active=False) as evidence."""
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    panel: str
    width: int | None = None
    height: int | None = None
    checksum: str | None = None
    uploaded_at: datetime
    active: bool = True
    original_filename: str | None = None
    byte_size: int | None = None
    content_type: str | None = None


class OcrDetectionResponse(BaseModel):
    text: str
    confidence: float | None
    bbox: list[list[float]] | None
    language: str | None = None


class OcrProcessed(BaseModel):
    id: UUID
    inspection_id: UUID
    image_id: UUID | None
    engine: str
    languages: list[str]
    skipped_languages: list[str] = []
    processed_at: datetime
    raw_text: str
    results: list[OcrDetectionResponse]
    duration_ms: int | None = None


class ExtractionFieldResponse(BaseModel):
    value: str | None = None
    confidence: float = Field(ge=0.0, le=1.0)
    needs_review: bool
    source_text: str | None = None
    matched_pattern: str | None = None
    normalized_value: str | None = None
    # OCR boxes (lists of [x, y] points in original image pixels) for source_text.
    source_bboxes: list[list[list[float]]] | None = None
    # OCR, or INSPECTOR when an inspector's correction overrides the read value.
    source: str = "OCR"
    # The photo the value (and source_bboxes, in that photo's pixels) was read from.
    image_id: UUID | None = None
    panel: str | None = None
    # Other values read for this field on other photos: [{image_id, panel, value, confidence}].
    alternatives: list[dict] = []


class ExtractionResponse(BaseModel):
    inspection_id: UUID
    ocr_result_id: UUID
    fields: dict[str, ExtractionFieldResponse]


class ComplianceSummary(BaseModel):
    passed: int
    failed: int
    review: int
    not_applicable: int = 0


class ComplianceFindingResponse(BaseModel):
    rule_id: str
    rule_name: str
    status: str
    severity: str
    message: str
    field: str | None = None
    value: str | None = None
    confidence: float | None = None
    evidence: str | None = None
    category: str = "LEGAL_METROLOGY"
    expected: str | None = None
    source_bboxes: list[list[list[float]]] | None = None
    # The photo source_bboxes lie on.
    image_id: UUID | None = None
    legal_reference: str | None = None
    recommended_action: str | None = None
    # Fields whose values this finding rests on (their boxes locate the finding on the photo).
    fields: list[str] = []
    # True when the value used was entered by an inspector.
    corrected: bool = False


class ComplianceResponse(BaseModel):
    inspection_id: UUID
    # FAIL / REVIEW / PASS, kept for compatibility; NOT_APPLICABLE never changes it.
    overall_status: str
    summary: ComplianceSummary
    findings: list[ComplianceFindingResponse]
    # NON_COMPLIANT (any FAIL) / NEEDS_REVIEW (any REVIEW) / COMPLIANT.
    verdict: str | None = None
    ruleset_id: str | None = None
    ruleset_version: str | None = None
    ruleset_label: str | None = None


class ComplianceRuleResponse(BaseModel):
    code: str
    title: str
    description: str
    legal_reference: str | None = None
    severity: str
    applies_when: str | None = None


class ComplianceRulesetResponse(BaseModel):
    ruleset_id: str
    version: str
    label: str
    title: str
    disclaimer: str
    rules: list[ComplianceRuleResponse]


class QuantityVerificationResponse(BaseModel):
    """Declared-label quantity only; no physical measurement is implied."""
    declared_quantity: str | None = None
    normalized_quantity: str | None = None
    unit: str | None = None
    status: str
    reason: str
    rule_code: str
    severity: str
    evidence: str | None = None
    requirement: str | None = None


class FieldCorrectionCreate(BaseModel):
    field_name: str = Field(min_length=1, max_length=100)
    corrected_value: str = Field(max_length=1000)
    reason: str | None = Field(default=None, max_length=500)


class VerifyRequest(BaseModel):
    remarks: str | None = Field(default=None, max_length=2000)


class FieldCorrectionResponse(BaseModel):
    id: UUID
    field_name: str
    original_value: str | None = None
    corrected_value: str
    reason: str | None = None
    corrected_by: UUID | None = None
    corrected_by_name: str | None = None
    created_at: datetime


class ReviewResponse(BaseModel):
    status: str = "UNREVIEWED"
    by: str | None = None
    at: datetime | None = None
    remarks: str | None = None


class AuditEvent(BaseModel):
    type: str
    at: datetime
    actor: str | None = None
    details: dict = {}


class InspectionSummary(BaseModel):
    """The persisted, user-facing state of an inspection workflow."""
    id: UUID
    inspection_number: str
    status: str
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None = None
    failure_stage: str | None = None
    failure_message: str | None = None
    image: ImageUploaded | None = None
    # Every stored photo, oldest first (replaced ones have active=False).
    images: list[ImageInfo] = []
    ocr: OcrProcessed | None = None
    extraction: ExtractionResponse | None = None
    compliance: ComplianceResponse | None = None
    quantity_verification: QuantityVerificationResponse | None = None
    risk: RiskAssessmentResponse | None = None
    # Extracted (or corrected) label identity; product_name falls back to "Untitled label".
    product_name: str = "Untitled label"
    brand_name: str | None = None
    maker_name: str | None = None
    corrections: list[FieldCorrectionResponse] = []
    review: ReviewResponse = ReviewResponse()


class RiskReasonResponse(BaseModel):
    type: str
    points: int
    description: str
    rule_code: str | None = None
    severity: str | None = None
    field: str | None = None
    occurrences: int | None = None


class RiskAssessmentResponse(BaseModel):
    risk_score: int = Field(ge=0, le=100)
    risk_level: str
    reasons: list[RiskReasonResponse]


class RiskTargetingItem(BaseModel):
    inspection_id: UUID
    product_name: str | None = None
    risk_score: int = Field(ge=0, le=100)
    risk_level: str
    compliance_status: str
    primary_reason: str
    completed_at: datetime
    priority: int


class RiskTargetingPage(BaseModel):
    items: list[RiskTargetingItem]
    total: int
    page: int
    page_size: int
    total_pages: int
    summary: dict[str, int]


class DashboardRiskItem(BaseModel):
    id: UUID
    inspection_number: str
    product_name: str | None = None
    maker: str | None = None
    verdict: str
    # Numerical risk is an official view; manufacturers receive null here.
    risk_score: int | None = Field(default=None, ge=0, le=100)
    risk_level: str | None = None


class DashboardRepeatIssue(BaseModel):
    rule_id: str
    title: str
    legal_reference: str | None = None
    label_count: int


class DashboardSummary(BaseModel):
    period: str
    total_inspections: int
    verdict_counts: dict[str, int]
    top_risk: list[DashboardRiskItem]
    repeat_issues: list[DashboardRepeatIssue]
    median_processing_seconds: float | None = None
    ruleset_version: str
