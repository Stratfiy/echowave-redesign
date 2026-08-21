"""subscription plans, and which plan a mandate bought

Revision ID: e1f4a7c92b31
Revises: d4a1c7e93b02
Create Date: 2026-08-21

The starter plan was three constants and one hardcoded route, which works for
exactly one plan. This makes a plan a row: priced, named and switched on by an
operator without a release, the same way the rate card and the model bundles
already are.

The seed reproduces today's ₹2,999 exactly — ₹2,500 of balance and one number —
so a deployment that runs this migration keeps selling precisely what it sold
before. It is written from the environment's own figures rather than from
literals here, because the price is derived from the rental price and hardcoding
2999 in a migration is how the two drift apart the first time somebody moves the
rental to ₹599.
"""

import os

import sqlalchemy as sa
from alembic import op

revision = "e1f4a7c92b31"
down_revision = "d4a1c7e93b02"
branch_labels = None
depends_on = None

STARTER = "starter"


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def upgrade() -> None:
    op.create_table(
        "subscription_plans",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("code", sa.String(length=32), nullable=False),
        sa.Column("label", sa.String(length=80), nullable=False),
        sa.Column("blurb", sa.Text(), nullable=False, server_default=""),
        sa.Column("price_paise", sa.BigInteger(), nullable=False),
        sa.Column("balance_paise", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("included_numbers", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("extra_number_price_paise", sa.BigInteger(), nullable=True),
        sa.Column("razorpay_plan_id", sa.String(length=64), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "uq_subscription_plans_code", "subscription_plans", ["code"], unique=True
    )

    op.add_column(
        "payment_mandates", sa.Column("plan_code", sa.String(length=32), nullable=True)
    )

    balance = _int_env("STARTER_PLAN_BALANCE_PAISE", 250_000)
    number = _int_env("NUMBER_RENTAL_PRICE_PAISE", 49_900)
    op.execute(
        sa.text(
            """
            INSERT INTO subscription_plans
                (code, label, blurb, price_paise, balance_paise,
                 included_numbers, extra_number_price_paise, razorpay_plan_id,
                 enabled, sort_order, created_at, updated_at)
            VALUES
                (:code, :label, :blurb, :price, :balance, 1, :extra, :rzp,
                 true, 0, NOW(), NOW())
            ON CONFLICT (code) DO NOTHING
            """
        ).bindparams(
            code=STARTER,
            label="Starter",
            blurb="A phone number and a month of calling, on one monthly payment.",
            price=balance + number,
            balance=balance,
            extra=number,
            rzp=os.getenv("RAZORPAY_STARTER_PLAN_ID") or None,
        )
    )

    # Existing plan mandates are on the one plan that existed.
    op.execute(
        sa.text(
            "UPDATE payment_mandates SET plan_code = :code "
            "WHERE purpose = 'starter_plan' AND plan_code IS NULL"
        ).bindparams(code=STARTER)
    )


def downgrade() -> None:
    op.drop_column("payment_mandates", "plan_code")
    op.drop_index("uq_subscription_plans_code", table_name="subscription_plans")
    op.drop_table("subscription_plans")
