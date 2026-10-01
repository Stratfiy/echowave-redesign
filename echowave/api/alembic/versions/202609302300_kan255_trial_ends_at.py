"""organizations.trial_ends_at: staff override of the trial window (PLAN-1, KAN-255).

Additive and nullable; NULL keeps the computed window.

Revision ID: 202609302300kan255
Revises: 202609302200kan273
Create Date: 2026-09-30
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "202609302300kan255"
down_revision: Union[str, None] = "202609302200kan273"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "organizations",
        sa.Column("trial_ends_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("organizations", "trial_ends_at")
