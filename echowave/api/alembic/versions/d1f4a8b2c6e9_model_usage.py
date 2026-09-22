"""model usage outside a pipeline (M-1)

Revision ID: d1f4a8b2c6e9
Revises: c8e2a5d7f1b3
Create Date: 2026-09-22

Every model call the builder client makes -- Decibyl's assistant, the
builder, triggers, document fields, the graph reviews -- written down with
the vendor's own token counts, so usage can be measured before deciding how
credits charge for models. Additive; nothing reads it but the report.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d1f4a8b2c6e9"
down_revision: str | None = "c8e2a5d7f1b3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "model_usage",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "organization_id",
            sa.Integer(),
            sa.ForeignKey("organizations.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "feature",
            sa.String(64),
            nullable=False,
            server_default="unattributed",
        ),
        sa.Column("provider", sa.String(64), nullable=False),
        sa.Column("model", sa.String(128), nullable=False, server_default=""),
        sa.Column("prompt_tokens", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column(
            "completion_tokens", sa.BigInteger(), nullable=False, server_default="0"
        ),
        sa.Column(
            "cache_read_input_tokens",
            sa.BigInteger(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "cache_creation_input_tokens",
            sa.BigInteger(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_model_usage_id", "model_usage", ["id"])
    op.create_index("ix_model_usage_created_at", "model_usage", ["created_at"])
    op.create_index(
        "ix_model_usage_org_created_at",
        "model_usage",
        ["organization_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_model_usage_org_created_at", table_name="model_usage")
    op.drop_index("ix_model_usage_created_at", table_name="model_usage")
    op.drop_index("ix_model_usage_id", table_name="model_usage")
    op.drop_table("model_usage")
