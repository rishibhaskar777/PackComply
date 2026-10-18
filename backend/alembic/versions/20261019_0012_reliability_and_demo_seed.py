"""Per-photo read failures, and a tag for demo data.

``inspection_images.read_error`` holds the reason a photo could not be read (cleared when a later attempt works), so an
inspection can finish with its other photos and say plainly which one is missing. ``demo_seed`` marks users and inspections
created by ``python -m backend.scripts.seed_demo`` so ``--reset-demo`` removes those and nothing else.

Revision ID: 20261019_0012
Revises: 20261018_0011
"""
from alembic import op
import sqlalchemy as sa

revision = "20261019_0012"
down_revision = "20261018_0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("inspection_images", sa.Column("read_error", sa.String(length=255), nullable=True))
    op.add_column("inspections", sa.Column("demo_seed", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("users", sa.Column("demo_seed", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    op.drop_column("users", "demo_seed")
    op.drop_column("inspections", "demo_seed")
    op.drop_column("inspection_images", "read_error")
