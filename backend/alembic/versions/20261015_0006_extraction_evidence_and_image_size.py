"""Store extraction evidence (source OCR line, pattern, boxes) and image dimensions.

Revision ID: 20261015_0006
Revises: 20260908_0005
"""
from alembic import op
import sqlalchemy as sa

revision = "20261015_0006"
down_revision = "20260908_0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("extracted_fields", sa.Column("source_text", sa.Text(), nullable=True))
    op.add_column("extracted_fields", sa.Column("matched_pattern", sa.String(100), nullable=True))
    op.add_column("extracted_fields", sa.Column("normalized_value", sa.Text(), nullable=True))
    op.add_column("extracted_fields", sa.Column("source_bboxes", sa.JSON(), nullable=True))
    op.add_column("inspection_images", sa.Column("width", sa.Integer(), nullable=True))
    op.add_column("inspection_images", sa.Column("height", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("inspection_images", "height")
    op.drop_column("inspection_images", "width")
    op.drop_column("extracted_fields", "source_bboxes")
    op.drop_column("extracted_fields", "normalized_value")
    op.drop_column("extracted_fields", "matched_pattern")
    op.drop_column("extracted_fields", "source_text")
