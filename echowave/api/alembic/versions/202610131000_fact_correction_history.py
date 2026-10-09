"""Confirmed memory keeps the value a correction replaced

Additive only: two nullable columns on ``organisation_facts``. A correction
used to overwrite a fact's value in place, so "the delivery radius was 5 km
until Tuesday" could not be answered and a wrong correction could not be
undone. ``previous_value`` is the value the latest correction replaced and
``superseded_at`` when it did; both stay NULL for a fact never corrected.

Downgrading drops both columns.

Revision ID: 20261013facthistory
Revises: 20261011escalations
"""

import sqlalchemy as sa
from alembic import op

revision = "20261013facthistory"
down_revision = "20261011escalations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "organisation_facts", sa.Column("previous_value", sa.Text(), nullable=True)
    )
    op.add_column(
        "organisation_facts",
        sa.Column("superseded_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("organisation_facts", "superseded_at")
    op.drop_column("organisation_facts", "previous_value")
