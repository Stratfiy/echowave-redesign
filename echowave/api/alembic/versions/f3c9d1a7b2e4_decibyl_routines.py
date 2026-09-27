"""a routine may be Decibyl's own: workflow_id becomes nullable (KAN-156)

Revision ID: f3c9d1a7b2e4
Revises: e8b4c2f7a1d9
Create Date: 2026-09-28

A routine with no workflow is run by the workspace assistant's own turn
rather than a bot's engine. Every existing row keeps its bot; nothing
reads a null until the runner and the thread tool ship with it.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f3c9d1a7b2e4"
down_revision: Union[str, None] = "e8b4c2f7a1d9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column(
        "agent_routines",
        "workflow_id",
        existing_type=sa.Integer(),
        nullable=True,
    )


def downgrade() -> None:
    # A Decibyl routine has no bot to give back; it is removed rather than
    # left to violate the constraint the downgrade restores.
    op.execute("DELETE FROM agent_routines WHERE workflow_id IS NULL")
    op.alter_column(
        "agent_routines",
        "workflow_id",
        existing_type=sa.Integer(),
        nullable=False,
    )
