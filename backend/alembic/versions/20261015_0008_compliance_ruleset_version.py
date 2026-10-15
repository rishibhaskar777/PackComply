"""Record the ruleset id and version on every compliance result.

Columns on compliance_results rather than a separate compliance_runs table:
results are unique per (inspection, rule) and every evaluation replaces the
whole set, so an inspection only ever has one run.  A runs table would add a
second lifecycle that the three evaluation paths (process, re-analyse, the
compliance endpoint) must keep in step, for no extra history.  Existing rows
keep NULL, meaning "checked before versioned rulesets".

The NOT_APPLICABLE result value needs no schema change (result is String(30)).

Revision ID: 20261015_0008
Revises: 20261015_0007
"""
from alembic import op
import sqlalchemy as sa

revision = "20261015_0008"
down_revision = "20261015_0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("compliance_results", sa.Column("ruleset_id", sa.String(length=50), nullable=True))
    op.add_column("compliance_results", sa.Column("ruleset_version", sa.String(length=20), nullable=True))


def downgrade() -> None:
    op.drop_column("compliance_results", "ruleset_version")
    op.drop_column("compliance_results", "ruleset_id")
