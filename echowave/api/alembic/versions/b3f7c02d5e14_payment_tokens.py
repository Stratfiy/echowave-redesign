"""Saved instruments we may present without the customer being there.

Parented on a7d2e4f91c85 to keep this branch on a single head — see
api/tests/test_migration_heads.py.

Revision ID: b3f7c02d5e14
Revises: a7d2e4f91c85
"""

import sqlalchemy as sa
from alembic import op

revision = "b3f7c02d5e14"
down_revision = "a7d2e4f91c85"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "payment_tokens",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column(
            "provider", sa.String(length=32), nullable=False, server_default="razorpay"
        ),
        sa.Column("token_id", sa.String(length=64), nullable=False),
        sa.Column("customer_id", sa.String(length=64), nullable=True),
        sa.Column("method", sa.String(length=24), nullable=True),
        sa.Column("instrument_hint", sa.String(length=64), nullable=True),
        sa.Column("max_amount_paise", sa.BigInteger(), nullable=True),
        sa.Column(
            "status", sa.String(length=24), nullable=False, server_default="active"
        ),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_payment_tokens_id"), "payment_tokens", ["id"], unique=False
    )
    op.create_index(
        "ix_payment_tokens_org",
        "payment_tokens",
        ["organization_id", "status"],
        unique=False,
    )
    # Webhooks arrive at least once. Without this a redelivery writes a second
    # row for the same provider token, and half the code then reads that one.
    op.create_index(
        "uq_payment_tokens_token",
        "payment_tokens",
        ["provider", "token_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_payment_tokens_token", table_name="payment_tokens")
    op.drop_index("ix_payment_tokens_org", table_name="payment_tokens")
    op.drop_index(op.f("ix_payment_tokens_id"), table_name="payment_tokens")
    op.drop_table("payment_tokens")
