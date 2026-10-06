"""workflows.avatar: the agent's face (shape, colour, resting expression).

Revision ID: 202610051800avatar
Revises: 202610031200studio
Create Date: 2026-10-05
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "202610051800avatar"
down_revision: Union[str, None] = "202610031200studio"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("workflows", sa.Column("avatar", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("workflows", "avatar")
