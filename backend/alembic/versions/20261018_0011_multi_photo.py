"""Multi-photo inspections: image panel, and which photo each extracted field came from.

Existing images become BACK panels. ``extracted_fields.image_id`` records the
photo a value was read from (its ``source_bboxes`` are in that photo's pixel
space) and ``alternatives`` the conflicting values read from other photos.

Revision ID: 20261018_0011
Revises: 20261017_0010
"""
from alembic import op
import sqlalchemy as sa

revision = "20261018_0011"
down_revision = "20261017_0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("inspection_images", sa.Column("panel", sa.String(length=20), nullable=False, server_default="BACK"))
    op.add_column("extracted_fields", sa.Column("image_id", sa.Uuid(), nullable=True))
    op.add_column("extracted_fields", sa.Column("alternatives", sa.JSON(), nullable=True))
    op.create_index("ix_extracted_fields_image_id", "extracted_fields", ["image_id"])
    op.create_foreign_key("fk_extracted_fields_image_id_inspection_images", "extracted_fields", "inspection_images", ["image_id"], ["id"], ondelete="SET NULL")


def downgrade() -> None:
    op.drop_constraint("fk_extracted_fields_image_id_inspection_images", "extracted_fields", type_="foreignkey")
    op.drop_index("ix_extracted_fields_image_id", table_name="extracted_fields")
    op.drop_column("extracted_fields", "alternatives")
    op.drop_column("extracted_fields", "image_id")
    op.drop_column("inspection_images", "panel")
