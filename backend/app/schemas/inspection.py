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
    compliance_status: str | None = None
    image_filename: str | None = None

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


class OcrDetectionResponse(BaseModel):
    text: str
    confidence: float | None
    bbox: list[list[float]] | None


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


class ExtractionFieldResponse(BaseModel):
    value: str | None = None
    confidence: float = Field(ge=0.0, le=1.0)
    needs_review: bool
    source_text: str | None = None
    matched_pattern: str | None = None
    normalized_value: str | None = None
    # OCR boxes (lists of [x, y] points in original image pixels) for source_text.
    source_bboxes: list[list[list[float]]] | None = None


class ExtractionResponse(BaseModel):
    inspection_id: UUID
    ocr_result_id: UUID
    fields: dict[str, ExtractionFieldResponse]


class ComplianceSummary(BaseModel):
    passed: int
    failed: int
    review: int


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


class ComplianceResponse(BaseModel):
    inspection_id: UUID
    overall_status: str
    summary: ComplianceSummary
    findings: list[ComplianceFindingResponse]


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
    ocr: OcrProcessed | None = None
    extraction: ExtractionResponse | None = None
    compliance: ComplianceResponse | None = None
    quantity_verification: QuantityVerificationResponse | None = None
    risk: RiskAssessmentResponse | None = None


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
