"""Create the PackComply foundation schema.

Revision ID: 20260903_0001
Revises:
Create Date: 2026-09-03
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "20260903_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def audit_columns() -> list[sa.Column]:
    return [
        sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    ]


def upgrade() -> None:
    op.create_table(
        "users", *audit_columns(),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("full_name", sa.String(200), nullable=False),
        sa.Column("password_hash", sa.String(255)),
        sa.Column("role", sa.String(50), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.UniqueConstraint("email"),
    )
    op.create_index("ix_users_email", "users", ["email"])

    op.create_table(
        "manufacturers", *audit_columns(),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("normalized_name", sa.String(255), nullable=False),
        sa.Column("address", sa.Text()), sa.Column("contact_details", sa.Text()),
        sa.UniqueConstraint("normalized_name"),
    )
    op.create_index("ix_manufacturers_name", "manufacturers", ["name"])
    op.create_index("ix_manufacturers_normalized_name", "manufacturers", ["normalized_name"])

    op.create_table(
        "products", *audit_columns(),
        sa.Column("manufacturer_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(255), nullable=False), sa.Column("category", sa.String(100)),
        sa.Column("barcode", sa.String(100)),
        sa.ForeignKeyConstraint(["manufacturer_id"], ["manufacturers.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("manufacturer_id", "name", name="uq_products_manufacturer_product_name"),
        sa.UniqueConstraint("barcode"),
    )
    op.create_index("ix_products_manufacturer_id", "products", ["manufacturer_id"])
    op.create_index("ix_products_category", "products", ["category"])

    op.create_table(
        "inspections", *audit_columns(),
        sa.Column("inspection_number", sa.String(40), nullable=False),
        sa.Column("inspector_id", sa.Uuid()), sa.Column("manufacturer_id", sa.Uuid()), sa.Column("product_id", sa.Uuid()),
        sa.Column("status", sa.String(30), nullable=False), sa.Column("inspected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("notes", sa.Text()),
        sa.ForeignKeyConstraint(["inspector_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["manufacturer_id"], ["manufacturers.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("inspection_number"),
    )
    for column in ("inspection_number", "inspector_id", "manufacturer_id", "product_id", "status"):
        op.create_index(f"ix_inspections_{column}", "inspections", [column])
    op.create_index("ix_inspections_manufacturer_created", "inspections", ["manufacturer_id", "created_at"])

    op.create_table(
        "compliance_rules", *audit_columns(),
        sa.Column("rule_code", sa.String(100), nullable=False), sa.Column("description", sa.Text(), nullable=False),
        sa.Column("target_field", sa.String(100), nullable=False), sa.Column("severity", sa.String(30), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False), sa.Column("configuration", sa.JSON(), nullable=False),
        sa.UniqueConstraint("rule_code"),
    )
    op.create_index("ix_compliance_rules_rule_code", "compliance_rules", ["rule_code"])

    op.create_table(
        "inspection_images", *audit_columns(),
        sa.Column("inspection_id", sa.Uuid(), nullable=False), sa.Column("storage_key", sa.String(500), nullable=False),
        sa.Column("original_filename", sa.String(255), nullable=False), sa.Column("content_type", sa.String(100), nullable=False),
        sa.Column("byte_size", sa.Integer(), nullable=False), sa.Column("checksum", sa.String(128)),
        sa.ForeignKeyConstraint(["inspection_id"], ["inspections.id"], ondelete="CASCADE"), sa.UniqueConstraint("storage_key"),
    )
    op.create_index("ix_inspection_images_inspection_id", "inspection_images", ["inspection_id"])
    op.create_index("ix_inspection_images_checksum", "inspection_images", ["checksum"])

    op.create_table(
        "ocr_results", *audit_columns(),
        sa.Column("inspection_id", sa.Uuid(), nullable=False), sa.Column("image_id", sa.Uuid()),
        sa.Column("engine", sa.String(100), nullable=False), sa.Column("languages", sa.JSON(), nullable=False),
        sa.Column("raw_text", sa.Text(), nullable=False), sa.Column("detections", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["inspection_id"], ["inspections.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["image_id"], ["inspection_images.id"], ondelete="SET NULL"),
    )
    op.create_index("ix_ocr_results_inspection_id", "ocr_results", ["inspection_id"])
    op.create_index("ix_ocr_results_image_id", "ocr_results", ["image_id"])

    op.create_table(
        "extracted_fields", *audit_columns(),
        sa.Column("inspection_id", sa.Uuid(), nullable=False), sa.Column("ocr_result_id", sa.Uuid()),
        sa.Column("field_name", sa.String(100), nullable=False), sa.Column("value", sa.Text()),
        sa.Column("confidence", sa.Numeric(5, 4)), sa.Column("review_status", sa.String(30), nullable=False),
        sa.ForeignKeyConstraint(["inspection_id"], ["inspections.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["ocr_result_id"], ["ocr_results.id"], ondelete="SET NULL"),
    )
    op.create_index("ix_extracted_fields_ocr_result_id", "extracted_fields", ["ocr_result_id"])
    op.create_index("ix_extracted_fields_inspection_field", "extracted_fields", ["inspection_id", "field_name"])

    op.create_table(
        "compliance_results", *audit_columns(),
        sa.Column("inspection_id", sa.Uuid(), nullable=False), sa.Column("rule_id", sa.Uuid(), nullable=False),
        sa.Column("result", sa.String(30), nullable=False), sa.Column("explanation", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["inspection_id"], ["inspections.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["rule_id"], ["compliance_rules.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("inspection_id", "rule_id", name="uq_compliance_results_inspection_rule_result"),
    )

    op.create_table(
        "inspection_issues", *audit_columns(),
        sa.Column("inspection_id", sa.Uuid(), nullable=False), sa.Column("manufacturer_id", sa.Uuid()),
        sa.Column("compliance_result_id", sa.Uuid()), sa.Column("field_name", sa.String(100), nullable=False),
        sa.Column("severity", sa.String(30), nullable=False), sa.Column("status", sa.String(30), nullable=False),
        sa.Column("details", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["inspection_id"], ["inspections.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["manufacturer_id"], ["manufacturers.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["compliance_result_id"], ["compliance_results.id"], ondelete="SET NULL"),
    )
    op.create_index("ix_inspection_issues_inspection_id", "inspection_issues", ["inspection_id"])
    op.create_index("ix_inspection_issues_manufacturer_field", "inspection_issues", ["manufacturer_id", "field_name"])

    op.create_table(
        "risk_scores", *audit_columns(),
        sa.Column("manufacturer_id", sa.Uuid(), nullable=False), sa.Column("score", sa.Numeric(5, 2), nullable=False),
        sa.Column("priority", sa.String(30), nullable=False), sa.Column("explanation", sa.Text(), nullable=False),
        sa.Column("factors", sa.JSON(), nullable=False), sa.Column("computed_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["manufacturer_id"], ["manufacturers.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_risk_scores_manufacturer_computed", "risk_scores", ["manufacturer_id", "computed_at"])

    op.create_table(
        "manufacturer_history", *audit_columns(),
        sa.Column("manufacturer_id", sa.Uuid(), nullable=False), sa.Column("inspection_id", sa.Uuid()),
        sa.Column("event_type", sa.String(100), nullable=False), sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["manufacturer_id"], ["manufacturers.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["inspection_id"], ["inspections.id"], ondelete="SET NULL"),
    )
    op.create_index("ix_manufacturer_history_inspection_id", "manufacturer_history", ["inspection_id"])
    op.create_index("ix_manufacturer_history_manufacturer_occurred", "manufacturer_history", ["manufacturer_id", "occurred_at"])

    op.create_table(
        "sync_records", *audit_columns(),
        sa.Column("client_sync_id", sa.String(100), nullable=False), sa.Column("inspection_id", sa.Uuid()),
        sa.Column("status", sa.String(30), nullable=False), sa.Column("payload_checksum", sa.String(128), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False), sa.Column("last_error", sa.Text()), sa.Column("synced_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("attempt_count >= 0", name="ck_sync_records_non_negative_attempt_count"),
        sa.ForeignKeyConstraint(["inspection_id"], ["inspections.id"], ondelete="SET NULL"), sa.UniqueConstraint("client_sync_id"),
    )
    op.create_index("ix_sync_records_client_sync_id", "sync_records", ["client_sync_id"])
    op.create_index("ix_sync_records_inspection_id", "sync_records", ["inspection_id"])


def downgrade() -> None:
    for table in (
        "sync_records", "manufacturer_history", "risk_scores", "inspection_issues", "compliance_results",
        "extracted_fields", "ocr_results", "inspection_images", "compliance_rules", "inspections",
        "products", "manufacturers", "users",
    ):
        op.drop_table(table)
