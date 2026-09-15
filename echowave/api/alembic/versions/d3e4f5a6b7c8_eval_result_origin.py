"""Where an eval result was asked for (KAN-140 P1): Decibyl's thread posts
the verdict back as a Result card.

Revision ID: d3e4f5a6b7c8
Revises: c2d3e4f5a6b7
Create Date: 2026-09-15
"""

import sqlalchemy as sa
from alembic import op

revision = "d3e4f5a6b7c8"
down_revision = "c2d3e4f5a6b7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("eval_results", sa.Column("origin", sa.String(16), nullable=True))


def downgrade() -> None:
    op.drop_column("eval_results", "origin")
