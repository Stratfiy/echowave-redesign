"""A second provider plan for exports, and an expiry date on plan balance.

A pinned Razorpay plan charges everyone the same amount, and that amount is
what the bank collects — nothing in this codebase can change it once the
instruction is held. The domestic figure is the gross: ₹2,999 net plus 18% GST
is ₹3,538.82.

An account outside India with an LUT on file is zero-rated and owes the **net**
₹2,999. Charging it the domestic gross overcharges it by the tax, every month,
for as long as the mandate lives. The amount guard added on 21 Aug refuses that
mismatch rather than collecting it, which meant such an account could not
subscribe at all — the right direction and not a fix.

So the plan row gets a second provider plan id, created at the net amount and
used when the supply resolves to an export. A second row rather than a
computation: there is no arithmetic that turns one pinned amount into another.
Null until an operator creates it, and until they do an export account is
refused with a message naming the amount to create it at.

**Plan balance now expires with its cycle.** ``CreditLedgerKind.PLAN`` was an
ordinary credit row that never expired, so an account paying every month and
never calling accrued its balance for ever — an unbounded liability, and a
bundle that was strictly worse than a top-up of the same size for us and
indistinguishable for the customer. Expiry is what makes the bundle a bundle.
The index below is what lets the next collection and the daily sweep both reach
the same grant without debiting it twice.

Revision ID: a3c9e1b47d02
Revises: f2b8c30d5a17
"""

import sqlalchemy as sa
from alembic import op

revision = "a3c9e1b47d02"
down_revision = "f2b8c30d5a17"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "subscription_plans",
        sa.Column("razorpay_plan_id_export", sa.String(length=64), nullable=True),
    )

    # A cycle's balance expires once. ``ref_id`` is the grant being retired, so
    # the daily sweep and the next collection can both reach the same grant and
    # only one debit lands — which matters because the two run on different
    # schedules and will race on a renewal that arrives late.
    op.create_index(
        "uq_credit_ledger_plan_expiry_ref",
        "credit_ledger",
        ["organization_id", "ref_type", "ref_id"],
        unique=True,
        postgresql_where=sa.text("kind = 'plan_expiry' AND ref_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_credit_ledger_plan_expiry_ref", table_name="credit_ledger")
    op.drop_column("subscription_plans", "razorpay_plan_id_export")
