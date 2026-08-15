"""Autopay mandates for the monthly number rental.

A mandate is the standing authorisation a rental is collected under — created
before any money moves, revocable by the customer's bank without telling us
first, and outliving every individual collection made under it. It gets its own
table for that reason: ``payments`` is one collected top-up, and folding a
permission into a table of transactions would mean a row that is never a
payment.

Three partial unique indexes carry the correctness:

* one row per provider subscription, because webhooks arrive at least once and
  out of order — without it a redelivery during a race writes a second row and
  half the later events update the wrong one
* one live mandate per account per purpose, because a customer who abandons an
  authorisation and starts again must not end up with two banks collecting for
  the same number
* one period per provider payment id, which is what makes a redelivered
  collection a no-op — see the comment on that index for why the existing
  period constraint cannot serve

``recurring_charge_periods.collected_via`` records whether the money came from
the prepaid balance or the customer's bank. The two are collected by completely
different code paths, and a statement that cannot tell them apart makes a
double collection impossible to spot.

Revision ID: e5b27c0a91d4
Revises: d3f5a81c62b7
"""

import sqlalchemy as sa
from alembic import op

revision = "e5b27c0a91d4"
down_revision = "d3f5a81c62b7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "payment_mandates",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column(
            "provider", sa.String(length=32), nullable=False, server_default="razorpay"
        ),
        sa.Column(
            "purpose",
            sa.String(length=32),
            nullable=False,
            server_default="number_rental",
        ),
        sa.Column("subscription_id", sa.String(length=64), nullable=True),
        sa.Column("plan_id", sa.String(length=64), nullable=True),
        sa.Column("customer_id", sa.String(length=64), nullable=True),
        sa.Column(
            "status", sa.String(length=24), nullable=False, server_default="created"
        ),
        sa.Column("short_url", sa.Text(), nullable=True),
        sa.Column("price_paise", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("authorised_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_charged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_failure_reason", sa.Text(), nullable=True),
        sa.Column("provider_payload", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_payment_mandates_id", "payment_mandates", ["id"], unique=False)
    op.create_index(
        "ix_payment_mandates_org",
        "payment_mandates",
        ["organization_id", "status"],
        unique=False,
    )
    op.create_index(
        "uq_payment_mandates_subscription",
        "payment_mandates",
        ["provider", "subscription_id"],
        unique=True,
        postgresql_where=sa.text("subscription_id IS NOT NULL"),
    )
    op.create_index(
        "uq_payment_mandates_live",
        "payment_mandates",
        ["organization_id", "purpose"],
        unique=True,
        postgresql_where=sa.text("status NOT IN ('cancelled', 'completed', 'expired')"),
    )

    op.add_column(
        "recurring_charges", sa.Column("mandate_id", sa.Integer(), nullable=True)
    )
    op.create_foreign_key(
        "fk_recurring_charges_mandate",
        "recurring_charges",
        "payment_mandates",
        ["mandate_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.add_column(
        "recurring_charge_periods",
        sa.Column(
            "collected_via",
            sa.String(length=16),
            nullable=False,
            server_default="balance",
        ),
    )
    # Every period that already exists was collected from the prepaid balance —
    # there was no other way to collect one until now — so the server default is
    # also the correct value for history.

    # The idempotency key for a mandate collection. The existing
    # (recurring_charge_id, period_start) constraint cannot serve: recording a
    # collection advances the charge to the next period, so a redelivered
    # webhook computes a different period_start, misses the constraint, and
    # bills a month forward. The provider's payment id is identical on every
    # redelivery, which is what makes at-least-once delivery safe. Partial,
    # because balance-collected periods carry no payment id and must not all
    # collide on NULL.
    op.add_column(
        "recurring_charge_periods",
        sa.Column("provider_payment_id", sa.String(length=64), nullable=True),
    )
    op.create_index(
        "uq_recurring_charge_period_payment",
        "recurring_charge_periods",
        ["provider_payment_id"],
        unique=True,
        postgresql_where=sa.text("provider_payment_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_recurring_charge_period_payment", table_name="recurring_charge_periods"
    )
    op.drop_column("recurring_charge_periods", "provider_payment_id")
    op.drop_column("recurring_charge_periods", "collected_via")
    op.drop_constraint(
        "fk_recurring_charges_mandate", "recurring_charges", type_="foreignkey"
    )
    op.drop_column("recurring_charges", "mandate_id")
    op.drop_index("uq_payment_mandates_live", table_name="payment_mandates")
    op.drop_index("uq_payment_mandates_subscription", table_name="payment_mandates")
    op.drop_index("ix_payment_mandates_org", table_name="payment_mandates")
    op.drop_index("ix_payment_mandates_id", table_name="payment_mandates")
    op.drop_table("payment_mandates")
