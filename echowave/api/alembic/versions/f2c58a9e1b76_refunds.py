"""refunds: reversing a payment and the credit it granted

Nothing reversed a payment before this. A chargeback or a goodwill refund left
credit granted with no way to claw it back, so every refund was two manual acts
— one in Razorpay, one in the ledger — and nobody could tell afterwards whether
both had happened.

A table rather than a flag on the payment, because a payment can be refunded in
parts and "how much is left" has to be a sum over records rather than a mutable
number somebody can get wrong.

Both amounts are stored. ``amount_paise`` is gross — what the customer gets
back, tax included, which is what the provider moves. ``reversed_credit_paise``
is net — what leaves the ledger, which never held the tax. Keeping both is what
lets a credit note be produced later without re-deriving the split from a rate
that may since have changed.

``status`` exists because the row is written before the provider is called: a
refund that fails mid-flight leaves evidence rather than nothing.

Revision ID: f2c58a9e1b76
Revises: e8b12d47f0c3
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f2c58a9e1b76"
down_revision: Union[str, Sequence[str], None] = "e8b12d47f0c3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "refunds",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("payment_id", sa.Integer(), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("amount_paise", sa.BigInteger(), nullable=False),
        sa.Column("reversed_credit_paise", sa.BigInteger(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column(
            "status",
            sa.String(length=16),
            server_default=sa.text("'pending'"),
            nullable=False,
        ),
        sa.Column("provider_refund_id", sa.String(length=64), nullable=True),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["payment_id"], ["payments.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_refunds_id"), "refunds", ["id"])
    op.create_index("ix_refunds_payment", "refunds", ["payment_id"])


def downgrade() -> None:
    op.drop_index("ix_refunds_payment", table_name="refunds")
    op.drop_index(op.f("ix_refunds_id"), table_name="refunds")
    op.drop_table("refunds")
