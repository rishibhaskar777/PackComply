"""Link a corrected self-check to the check it replaces.

``inspections.previous_inspection_id`` points at the earlier self-check of the same product and ``product_key`` carries the
product identity forward, so a manufacturer's product card shows its history and "improved from 3 fixes to 0".

Revision ID: 20261020_0013
Revises: 20261019_0012
"""
from alembic import op
import sqlalchemy as sa

revision = "20261020_0013"
down_revision = "20261019_0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("inspections", sa.Column("previous_inspection_id", sa.Uuid(), nullable=True))
    op.add_column("inspections", sa.Column("product_key", sa.String(length=255), nullable=True))
    op.create_foreign_key("fk_inspections_previous_inspection", "inspections", "inspections", ["previous_inspection_id"], ["id"], ondelete="SET NULL")
    op.create_index(op.f("ix_inspections_previous_inspection_id"), "inspections", ["previous_inspection_id"])
    op.create_index(op.f("ix_inspections_product_key"), "inspections", ["product_key"])


def downgrade() -> None:
    op.drop_index(op.f("ix_inspections_product_key"), table_name="inspections")
    op.drop_index(op.f("ix_inspections_previous_inspection_id"), table_name="inspections")
    op.drop_constraint("fk_inspections_previous_inspection", "inspections", type_="foreignkey")
    op.drop_column("inspections", "product_key")
    op.drop_column("inspections", "previous_inspection_id")
