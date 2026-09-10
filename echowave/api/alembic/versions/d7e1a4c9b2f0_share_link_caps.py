"""A daily minutes cap on embed tokens, for share links.

Revision ID: d7e1a4c9b2f0
Revises: c5d7e9a1b3f2
"""

import sqlalchemy as sa
from alembic import op

revision = "d7e1a4c9b2f0"
down_revision = "c5d7e9a1b3f2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "embed_tokens",
        sa.Column("daily_minutes_cap", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("embed_tokens", "daily_minutes_cap")
