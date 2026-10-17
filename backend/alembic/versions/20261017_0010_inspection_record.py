"""Inspection record: label identity, inspector corrections, review and OCR duration.

Adds product_name / brand_name / maker_name to inspections (lists and search),
review_status / reviewed_by / reviewed_at / remarks, the field_corrections
and inspection_events tables, and ocr_results.duration_ms. Run
``python -m backend.scripts.backfill_product_names`` afterwards to fill the
label identity of inspections completed before this migration.

Revision ID: 20261017_0010
Revises: 20261016_0009
"""
from alembic import op
import sqlalchemy as sa

revision = "20261017_0010"
down_revision = "20261016_0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("inspections", sa.Column("product_name", sa.String(length=255), nullable=True))
    op.add_column("inspections", sa.Column("brand_name", sa.String(length=255), nullable=True))
    op.add_column("inspections", sa.Column("maker_name", sa.String(length=255), nullable=True))
    op.add_column("inspections", sa.Column("review_status", sa.String(length=20), nullable=False, server_default="UNREVIEWED"))
    op.add_column("inspections", sa.Column("reviewed_by", sa.Uuid(), nullable=True))
    op.add_column("inspections", sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("inspections", sa.Column("remarks", sa.Text(), nullable=True))
    op.create_index("ix_inspections_product_name", "inspections", ["product_name"])
    op.create_foreign_key("fk_inspections_reviewed_by_users", "inspections", "users", ["reviewed_by"], ["id"], ondelete="SET NULL")
    op.add_column("ocr_results", sa.Column("duration_ms", sa.Integer(), nullable=True))
    op.create_table(
        "field_corrections",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("inspection_id", sa.Uuid(), nullable=False),
        sa.Column("field_name", sa.String(length=100), nullable=False),
        sa.Column("original_value", sa.Text(), nullable=True),
        sa.Column("corrected_value", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("corrected_by", sa.Uuid(), nullable=True),
        sa.ForeignKeyConstraint(["inspection_id"], ["inspections.id"], name="fk_field_corrections_inspection_id_inspections", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["corrected_by"], ["users.id"], name="fk_field_corrections_corrected_by_users", ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id", name="pk_field_corrections"),
    )
    op.create_index("ix_field_corrections_inspection_field", "field_corrections", ["inspection_id", "field_name"])
    op.create_table(
        "inspection_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("inspection_id", sa.Uuid(), nullable=False),
        sa.Column("event_type", sa.String(length=40), nullable=False),
        sa.Column("actor_id", sa.Uuid(), nullable=True),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["inspection_id"], ["inspections.id"], name="fk_inspection_events_inspection_id_inspections", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["actor_id"], ["users.id"], name="fk_inspection_events_actor_id_users", ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id", name="pk_inspection_events"),
    )
    op.create_index("ix_inspection_events_inspection_id", "inspection_events", ["inspection_id"])


def downgrade() -> None:
    op.drop_index("ix_inspection_events_inspection_id", table_name="inspection_events")
    op.drop_table("inspection_events")
    op.drop_index("ix_field_corrections_inspection_field", table_name="field_corrections")
    op.drop_table("field_corrections")
    op.drop_column("ocr_results", "duration_ms")
    op.drop_constraint("fk_inspections_reviewed_by_users", "inspections", type_="foreignkey")
    op.drop_index("ix_inspections_product_name", table_name="inspections")
    for column in ("remarks", "reviewed_at", "reviewed_by", "review_status", "maker_name", "brand_name", "product_name"):
        op.drop_column("inspections", column)
