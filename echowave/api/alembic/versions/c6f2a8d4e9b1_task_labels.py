"""task labels (TB-3): a task's own words to sort the board by

Revision ID: c6f2a8d4e9b1
Revises: b5e9d3a1c7f4
Create Date: 2026-09-23

Additive and nullable; nothing reads it while TASK_BOARD_2026_09_ENABLED is off.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c6f2a8d4e9b1"
down_revision: Union[str, None] = "b5e9d3a1c7f4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("agent_tasks", sa.Column("labels", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("agent_tasks", "labels")
