"""The plan ladder: what a plan permits, how else it can be paid for, and its caps.

KAN-53. Until now a plan was a price, a balance and some numbers. The ladder
decided on 14 Sept 2026 (KAN-47) needs a plan to also say:

* whether deployed bots may use the phone at all (``voice_allowed``: Everyday
  and Free say no -- an Everyday account cannot attach a number or start a
  campaign);
* whether it is on sale (``purchasable``: Free and Campus Builder are not
  bought, they are granted);
* what a foreign, text-only account pays in dollars (``price_usd_cents``);
* what a year costs (``annual_price_paise``, ten months for twelve) and which
  provider plans collect it (``razorpay_plan_id_annual`` and its export twin).

A mandate now records which period it collects for (``billing_period``), so
the grant on a collection knows whether it is one month's credits or twelve.

And every cap moves out of code into ``plan_limits``: one row per plan per
key, null meaning unlimited. Seeded by the service (``plan_limits.SEED``) on
first read, never overwritten, so an operator's edit survives a deploy.

Revision ID: c1d2e3f4a5b6
Revises: a9c3e5d7f1b2
"""

import sqlalchemy as sa
from alembic import op

revision = "c1d2e3f4a5b6"
down_revision = "a9c3e5d7f1b2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "subscription_plans",
        sa.Column("voice_allowed", sa.Boolean(), nullable=False, server_default="true"),
    )
    op.add_column(
        "subscription_plans",
        sa.Column("purchasable", sa.Boolean(), nullable=False, server_default="true"),
    )
    op.add_column(
        "subscription_plans", sa.Column("price_usd_cents", sa.Integer(), nullable=True)
    )
    op.add_column(
        "subscription_plans",
        sa.Column("annual_price_paise", sa.BigInteger(), nullable=True),
    )
    op.add_column(
        "subscription_plans",
        sa.Column("razorpay_plan_id_annual", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "subscription_plans",
        sa.Column(
            "razorpay_plan_id_annual_export", sa.String(length=64), nullable=True
        ),
    )

    op.add_column(
        "payment_mandates",
        sa.Column(
            "billing_period",
            sa.String(length=16),
            nullable=False,
            server_default="monthly",
        ),
    )

    op.create_table(
        "plan_limits",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("plan_code", sa.String(length=32), nullable=False),
        sa.Column("key", sa.String(length=48), nullable=False),
        # Null is unlimited. A cap of zero is a real cap: "none of these".
        sa.Column("value", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "uq_plan_limits_plan_key", "plan_limits", ["plan_code", "key"], unique=True
    )


def downgrade() -> None:
    op.drop_index("uq_plan_limits_plan_key", table_name="plan_limits")
    op.drop_table("plan_limits")
    op.drop_column("payment_mandates", "billing_period")
    for column in (
        "razorpay_plan_id_annual_export",
        "razorpay_plan_id_annual",
        "annual_price_paise",
        "price_usd_cents",
        "purchasable",
        "voice_allowed",
    ):
        op.drop_column("subscription_plans", column)
