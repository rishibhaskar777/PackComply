from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID

from sqlalchemy import Boolean, CheckConstraint, Date, DateTime, ForeignKey, Index, Integer, JSON, Numeric, String, Text, UniqueConstraint, func, select
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.models.base import Base, IdTimestampMixin


class User(IdTimestampMixin, Base):
    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(320), unique=True, index=True, nullable=False)
    username: Mapped[str | None] = mapped_column(String(100), unique=True, index=True)
    full_name: Mapped[str] = mapped_column(String(200), nullable=False)
    password_hash: Mapped[str | None] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(50), default="OFFICIAL", nullable=False)
    organization: Mapped[str | None] = mapped_column(String(200))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class AuthSession(IdTimestampMixin, Base):
    __tablename__ = "auth_sessions"
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class PasswordResetCode(IdTimestampMixin, Base):
    """A one-time password-reset code. Only a scrypt hash of the code is stored."""
    __tablename__ = "password_reset_codes"
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)
    code_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # Set when the code is redeemed, superseded, or burned by too many wrong guesses.
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    requested_ip: Mapped[str | None] = mapped_column(String(64))


class Manufacturer(IdTimestampMixin, Base):
    __tablename__ = "manufacturers"

    name: Mapped[str] = mapped_column(String(255), index=True, nullable=False)
    normalized_name: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    address: Mapped[str | None] = mapped_column(Text)
    contact_details: Mapped[str | None] = mapped_column(Text)


class Product(IdTimestampMixin, Base):
    __tablename__ = "products"
    __table_args__ = (UniqueConstraint("manufacturer_id", "name", name="manufacturer_product_name"),)

    manufacturer_id: Mapped[UUID] = mapped_column(ForeignKey("manufacturers.id", ondelete="RESTRICT"), index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    category: Mapped[str | None] = mapped_column(String(100), index=True)
    barcode: Mapped[str | None] = mapped_column(String(100), unique=True)


class Inspection(IdTimestampMixin, Base):
    __tablename__ = "inspections"
    __table_args__ = (Index("ix_inspections_manufacturer_created", "manufacturer_id", "created_at"),)

    inspection_number: Mapped[str] = mapped_column(String(40), unique=True, index=True, nullable=False)
    inspector_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    user_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    manufacturer_id: Mapped[UUID | None] = mapped_column(ForeignKey("manufacturers.id", ondelete="SET NULL"), index=True)
    product_id: Mapped[UUID | None] = mapped_column(ForeignKey("products.id", ondelete="SET NULL"), index=True)
    status: Mapped[str] = mapped_column(String(30), default="CREATED", index=True, nullable=False)
    inspected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failure_stage: Mapped[str | None] = mapped_column(String(30))
    failure_message: Mapped[str | None] = mapped_column(Text)
    # Label identity as extracted (and corrected); kept here so lists and search need no join through extracted_fields.
    product_name: Mapped[str | None] = mapped_column(String(255), index=True)
    brand_name: Mapped[str | None] = mapped_column(String(255))
    maker_name: Mapped[str | None] = mapped_column(String(255))
    # Inspector review: UNREVIEWED until an official verifies; any new correction resets it.
    review_status: Mapped[str] = mapped_column(String(20), default="UNREVIEWED", server_default="UNREVIEWED", nullable=False)
    reviewed_by: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    remarks: Mapped[str | None] = mapped_column(Text)


def _next_upload_sequence(context) -> int:
    """1, 2, 3… per inspection, assigned in the same INSERT transaction."""
    inspection_id = context.get_current_parameters()["inspection_id"]
    current = context.connection.execute(
        select(func.max(InspectionImage.upload_sequence)).where(InspectionImage.inspection_id == inspection_id)
    ).scalar()
    return (current or 0) + 1


class InspectionImage(IdTimestampMixin, Base):
    __tablename__ = "inspection_images"
    __table_args__ = (UniqueConstraint("inspection_id", "upload_sequence", name="inspection_image_upload_sequence"),)

    inspection_id: Mapped[UUID] = mapped_column(ForeignKey("inspections.id", ondelete="CASCADE"), index=True, nullable=False)
    # Upload order within the inspection. created_at can tie for back-to-back
    # uploads (coarse clocks) and ids are random, so this breaks ties.
    upload_sequence: Mapped[int] = mapped_column(Integer, default=_next_upload_sequence, nullable=False)
    storage_key: Mapped[str] = mapped_column(String(500), unique=True, nullable=False)
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    content_type: Mapped[str] = mapped_column(String(100), nullable=False)
    byte_size: Mapped[int] = mapped_column(Integer, nullable=False)
    checksum: Mapped[str | None] = mapped_column(String(128), index=True)
    # Displayed (EXIF-oriented) pixel size; null for rows uploaded before it was recorded.
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    # Which part of the pack this photo shows: BACK | FRONT | DATE_STAMP | OTHER.
    # The newest upload per panel is the one processed; older ones stay as evidence.
    panel: Mapped[str] = mapped_column(String(20), default="BACK", server_default="BACK", nullable=False)


class OcrResult(IdTimestampMixin, Base):
    __tablename__ = "ocr_results"

    inspection_id: Mapped[UUID] = mapped_column(ForeignKey("inspections.id", ondelete="CASCADE"), index=True, nullable=False)
    image_id: Mapped[UUID | None] = mapped_column(ForeignKey("inspection_images.id", ondelete="SET NULL"), index=True)
    engine: Mapped[str] = mapped_column(String(100), default="PaddleOCR", nullable=False)
    languages: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    raw_text: Mapped[str] = mapped_column(Text, nullable=False)
    detections: Mapped[list[dict]] = mapped_column(JSON, default=list, nullable=False)
    # Wall-clock time of the scan; null for results stored before it was recorded.
    duration_ms: Mapped[int | None] = mapped_column(Integer)


class ExtractedField(IdTimestampMixin, Base):
    __tablename__ = "extracted_fields"
    __table_args__ = (Index("ix_extracted_fields_inspection_field", "inspection_id", "field_name"),)

    inspection_id: Mapped[UUID] = mapped_column(ForeignKey("inspections.id", ondelete="CASCADE"), nullable=False)
    ocr_result_id: Mapped[UUID | None] = mapped_column(ForeignKey("ocr_results.id", ondelete="SET NULL"), index=True)
    field_name: Mapped[str] = mapped_column(String(100), nullable=False)
    value: Mapped[str | None] = mapped_column(Text)
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(5, 4))
    review_status: Mapped[str] = mapped_column(String(30), default="REVIEW", nullable=False)
    # Evidence: the OCR line the value came from and where it sits on the photo.
    source_text: Mapped[str | None] = mapped_column(Text)
    matched_pattern: Mapped[str | None] = mapped_column(String(100))
    normalized_value: Mapped[str | None] = mapped_column(Text)
    source_bboxes: Mapped[list[list[list[float]]] | None] = mapped_column(JSON)
    # The photo this value (and source_bboxes, in that photo's pixel space) was read from.
    image_id: Mapped[UUID | None] = mapped_column(ForeignKey("inspection_images.id", ondelete="SET NULL"), index=True)
    # Different values for the same field read from other photos: [{image_id, panel, value, confidence}].
    alternatives: Mapped[list[dict] | None] = mapped_column(JSON)


class FieldCorrection(IdTimestampMixin, Base):
    """An inspector's correction of one extracted field; re-applied on top of every fresh extraction."""
    __tablename__ = "field_corrections"
    __table_args__ = (Index("ix_field_corrections_inspection_field", "inspection_id", "field_name"),)

    inspection_id: Mapped[UUID] = mapped_column(ForeignKey("inspections.id", ondelete="CASCADE"), nullable=False)
    field_name: Mapped[str] = mapped_column(String(100), nullable=False)
    original_value: Mapped[str | None] = mapped_column(Text)
    corrected_value: Mapped[str] = mapped_column(Text, nullable=False)
    reason: Mapped[str | None] = mapped_column(Text)
    corrected_by: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))


class InspectionEvent(IdTimestampMixin, Base):
    """Audit events that cannot be derived from other rows: re-analysis, verification, undone corrections."""
    __tablename__ = "inspection_events"

    inspection_id: Mapped[UUID] = mapped_column(ForeignKey("inspections.id", ondelete="CASCADE"), index=True, nullable=False)
    event_type: Mapped[str] = mapped_column(String(40), nullable=False)
    actor_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    details: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)


class ComplianceRule(IdTimestampMixin, Base):
    __tablename__ = "compliance_rules"

    rule_code: Mapped[str] = mapped_column(String(100), unique=True, index=True, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    target_field: Mapped[str] = mapped_column(String(100), nullable=False)
    severity: Mapped[str] = mapped_column(String(30), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    configuration: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)


class ComplianceResult(IdTimestampMixin, Base):
    __tablename__ = "compliance_results"
    __table_args__ = (UniqueConstraint("inspection_id", "rule_id", name="inspection_rule_result"),)

    inspection_id: Mapped[UUID] = mapped_column(ForeignKey("inspections.id", ondelete="CASCADE"), nullable=False)
    rule_id: Mapped[UUID] = mapped_column(ForeignKey("compliance_rules.id", ondelete="RESTRICT"), nullable=False)
    result: Mapped[str] = mapped_column(String(30), nullable=False)
    explanation: Mapped[str] = mapped_column(Text, nullable=False)
    field_name: Mapped[str | None] = mapped_column(String(100))
    value: Mapped[str | None] = mapped_column(Text)
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(5, 4))
    evidence: Mapped[str | None] = mapped_column(Text)
    # Which versioned ruleset produced this result; null for results stored before rulesets.
    ruleset_id: Mapped[str | None] = mapped_column(String(50))
    ruleset_version: Mapped[str | None] = mapped_column(String(20))
    rule: Mapped[ComplianceRule] = relationship()


class InspectionIssue(IdTimestampMixin, Base):
    __tablename__ = "inspection_issues"
    __table_args__ = (Index("ix_inspection_issues_manufacturer_field", "manufacturer_id", "field_name"),)

    inspection_id: Mapped[UUID] = mapped_column(ForeignKey("inspections.id", ondelete="CASCADE"), index=True, nullable=False)
    manufacturer_id: Mapped[UUID | None] = mapped_column(ForeignKey("manufacturers.id", ondelete="SET NULL"))
    compliance_result_id: Mapped[UUID | None] = mapped_column(ForeignKey("compliance_results.id", ondelete="SET NULL"))
    field_name: Mapped[str] = mapped_column(String(100), nullable=False)
    severity: Mapped[str] = mapped_column(String(30), nullable=False)
    status: Mapped[str] = mapped_column(String(30), default="OPEN", nullable=False)
    details: Mapped[str] = mapped_column(Text, nullable=False)


class RiskScore(IdTimestampMixin, Base):
    __tablename__ = "risk_scores"
    __table_args__ = (Index("ix_risk_scores_manufacturer_computed", "manufacturer_id", "computed_at"),)

    manufacturer_id: Mapped[UUID] = mapped_column(ForeignKey("manufacturers.id", ondelete="CASCADE"), nullable=False)
    score: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False)
    priority: Mapped[str] = mapped_column(String(30), nullable=False)
    explanation: Mapped[str] = mapped_column(Text, nullable=False)
    factors: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)


class ManufacturerHistory(IdTimestampMixin, Base):
    __tablename__ = "manufacturer_history"
    __table_args__ = (Index("ix_manufacturer_history_manufacturer_occurred", "manufacturer_id", "occurred_at"),)

    manufacturer_id: Mapped[UUID] = mapped_column(ForeignKey("manufacturers.id", ondelete="CASCADE"), nullable=False)
    inspection_id: Mapped[UUID | None] = mapped_column(ForeignKey("inspections.id", ondelete="SET NULL"), index=True)
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    details: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)


class SyncRecord(IdTimestampMixin, Base):
    __tablename__ = "sync_records"
    __table_args__ = (CheckConstraint("attempt_count >= 0", name="non_negative_attempt_count"),)

    client_sync_id: Mapped[str] = mapped_column(String(100), unique=True, index=True, nullable=False)
    inspection_id: Mapped[UUID | None] = mapped_column(ForeignKey("inspections.id", ondelete="SET NULL"), index=True)
    status: Mapped[str] = mapped_column(String(30), default="PENDING_SYNC", nullable=False)
    payload_checksum: Mapped[str] = mapped_column(String(128), nullable=False)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_error: Mapped[str | None] = mapped_column(Text)
    synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
