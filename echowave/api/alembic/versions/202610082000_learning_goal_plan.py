"""A learning goal's plan (stream `learning`, the tutor journey)

One nullable JSON column on ``learning_goals``: the ordered lesson names the
teacher wrote at placement. Additive; a downgrade drops only the column.

Revision ID: 20261008learnplan
Revises: 20261008voice
"""

import sqlalchemy as sa
from alembic import op

revision = "20261008learnplan"
down_revision = "20261008voice"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("learning_goals", sa.Column("plan", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("learning_goals", "plan")
