"""model_usage: quantity and unit, for vendor spend that is not tokens.

Revision ID: c6f2a4b8d1e3
Revises: b5e1f3a9c7d2
Create Date: 2026-09-30
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c6f2a4b8d1e3"
down_revision: Union[str, None] = "b5e1f3a9c7d2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "model_usage",
        sa.Column("quantity", sa.Float(), nullable=False, server_default="0"),
    )
    op.add_column(
        "model_usage",
        sa.Column("unit", sa.String(length=16), nullable=False, server_default=""),
    )


def downgrade() -> None:
    op.drop_column("model_usage", "unit")
    op.drop_column("model_usage", "quantity")
