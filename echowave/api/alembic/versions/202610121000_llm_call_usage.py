"""Prompt cache measurement: one row per model call

Additive only: one table (``llm_call_usage``, services/billing/cache_metrics).
Every model call -- builder client or pipeline, retries and background
summaries included -- with its usage in one shape and hashes of the prompt
prefix it sent, so cache hit rate, prefix breaks and cost per outcome can be
measured. Nothing reads it to bill anyone.

Downgrading drops the table.

Revision ID: 20261012llmcallusage
Revises: 20261011escalations
"""

import sqlalchemy as sa
from alembic import op

revision = "20261012llmcallusage"
down_revision = "20261011escalations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "llm_call_usage",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "organization_id",
            sa.Integer(),
            sa.ForeignKey("organizations.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("feature", sa.String(64), nullable=False),
        sa.Column("provider", sa.String(64), nullable=False),
        sa.Column("model", sa.String(128), nullable=False, server_default=""),
        sa.Column("conversation_key", sa.String(96), nullable=True),
        sa.Column("workflow_run_id", sa.Integer(), nullable=True),
        sa.Column("prompt_fingerprint", sa.String(16), nullable=True),
        sa.Column("prefix_hash", sa.String(16), nullable=True),
        sa.Column("system_hash", sa.String(16), nullable=True),
        sa.Column("tools_hash", sa.String(16), nullable=True),
        sa.Column("input_tokens", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("output_tokens", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column(
            "cache_read_tokens", sa.BigInteger(), nullable=False, server_default="0"
        ),
        sa.Column(
            "cache_write_tokens", sa.BigInteger(), nullable=False, server_default="0"
        ),
        sa.Column(
            "reasoning_tokens", sa.BigInteger(), nullable=False, server_default="0"
        ),
        sa.Column("is_retry", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("byok", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_llm_call_usage_created_at", "llm_call_usage", ["created_at"])
    op.create_index(
        "ix_llm_call_usage_conversation",
        "llm_call_usage",
        ["conversation_key", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_llm_call_usage_conversation", table_name="llm_call_usage")
    op.drop_index("ix_llm_call_usage_created_at", table_name="llm_call_usage")
    op.drop_table("llm_call_usage")
