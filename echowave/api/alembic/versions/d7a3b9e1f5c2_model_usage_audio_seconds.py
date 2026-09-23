"""model_usage.audio_seconds: transcription measured beside tokens

Revision ID: d7a3b9e1f5c2
Revises: c6f2a8d4e9b1
Create Date: 2026-09-23

Additive, default 0. Every existing row is a token row and stays one.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "d7a3b9e1f5c2"
down_revision: Union[str, None] = "c6f2a8d4e9b1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "model_usage",
        sa.Column("audio_seconds", sa.Float(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("model_usage", "audio_seconds")
