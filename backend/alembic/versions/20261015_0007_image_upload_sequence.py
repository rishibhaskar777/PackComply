"""Per-inspection upload sequence so "latest image" is deterministic on timestamp ties.

Revision ID: 20261015_0007
Revises: 20261015_0006
"""
from alembic import op
import sqlalchemy as sa

revision = "20261015_0007"
down_revision = "20261015_0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("inspection_images", sa.Column("upload_sequence", sa.Integer(), nullable=True))
    # Existing rows: number them in their recorded order; id only separates exact ties.
    op.execute(
        """
        UPDATE inspection_images SET upload_sequence = ordered.sequence
        FROM (
            SELECT id, ROW_NUMBER() OVER (PARTITION BY inspection_id ORDER BY created_at, id) AS sequence
            FROM inspection_images
        ) AS ordered
        WHERE inspection_images.id = ordered.id
        """
    )
    op.alter_column("inspection_images", "upload_sequence", nullable=False)
    op.create_unique_constraint("inspection_image_upload_sequence", "inspection_images", ["inspection_id", "upload_sequence"])


def downgrade() -> None:
    op.drop_constraint("inspection_image_upload_sequence", "inspection_images", type_="unique")
    op.drop_column("inspection_images", "upload_sequence")
