"""Decibyl keeps working: a task carries the turn it continues (D-1a)

A reply that hits its tool-round cap used to stop with "ask again from
there". Now it can hand the rest to a task on the board, and the task needs
to carry the transcript and tool state it continues from -- that is this
column. NULL on every task a bot or a person does.

Revision ID: b2d5f8a1c3e7
Revises: a1c4e7b9d2f6
"""

import sqlalchemy as sa
from alembic import op

revision = "b2d5f8a1c3e7"
down_revision = "a1c4e7b9d2f6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("agent_tasks", sa.Column("continuation", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("agent_tasks", "continuation")
