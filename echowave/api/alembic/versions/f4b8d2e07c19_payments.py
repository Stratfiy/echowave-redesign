"""payments

Prepaid top-ups. One row per attempt to buy credit, written when the order is
created and moved to ``paid`` only by a signature-verified webhook.

The partial unique index on (provider, payment_id) is the point of the table.
Razorpay delivers webhooks at least once, not exactly once, so idempotency has
to be enforced by the database rather than by a check-then-write in application
code that two concurrent retries can both pass.

Revision ID: f4b8d2e07c19
Revises: e2a7c418d5b6
Create Date: 2026-07-30 07:41:12.905331

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f4b8d2e07c19"
down_revision: Union[str, None] = "e2a7c418d5b6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "payments",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column(
            "provider",
            sa.String(length=32),
            nullable=False,
            server_default="razorpay",
        ),
        sa.Column("order_id", sa.String(length=64), nullable=False),
        sa.Column("payment_id", sa.String(length=64), nullable=True),
        sa.Column("amount_paise", sa.BigInteger(), nullable=False),
        sa.Column(
            "status", sa.String(length=16), nullable=False, server_default="created"
        ),
        sa.Column("credit_ledger_id", sa.Integer(), nullable=True),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("provider_payload", sa.JSON(), nullable=True),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["credit_ledger_id"], ["credit_ledger.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("provider", "order_id", name="uq_payments_provider_order"),
    )
    op.create_index(op.f("ix_payments_id"), "payments", ["id"])
    op.create_index(
        "ix_payments_org_created", "payments", ["organization_id", "created_at"]
    )
    # NULLs exempt, so the many rows for orders that were never paid do not
    # collide with each other.
    op.create_index(
        "uq_payments_provider_payment",
        "payments",
        ["provider", "payment_id"],
        unique=True,
        postgresql_where=sa.text("payment_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_payments_provider_payment", table_name="payments")
    op.drop_index("ix_payments_org_created", table_name="payments")
    op.drop_index(op.f("ix_payments_id"), table_name="payments")
    op.drop_table("payments")
