"""subscription plans, and which plan a mandate bought

Revision ID: e1f4a7c92b31
Revises: d4a1c7e93b02
Create Date: 2026-08-21

The starter plan was three constants and one hardcoded route, which works for
exactly one plan. This makes a plan a row: priced, named and switched on by an
operator without a release, the same way the rate card and the model bundles
already are.

The seed reproduces today's ₹2,999 — ₹2,500 of balance and one number — so a
deployment that runs this migration keeps selling precisely what it sold before.

It reads STARTER_PLAN_PRICE_PAISE rather than adding the balance to the
extra-number price. Deriving it was the original intent and it is now wrong:
₹559 is what an *extra* number costs, a plan's included number is priced inside
its monthly price, and Starter is pinned to a Razorpay plan that collects a
fixed amount. Derived, a deployment with NUMBER_RENTAL_PRICE_PAISE=55900 seeds
Starter at ₹3,059 while the bank collects ₹2,999 grossed up — and the two come
apart at the bank rather than in a spreadsheet. See the note above
STARTER_PLAN_BALANCE_PAISE in api/constants.py, which says exactly this.

The same reasoning applies to the fallbacks: they match api/constants.py so one
variable does not have two defaults, which is the other way these drift.
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
    # The extra-number price, for `extra_number_price_paise` only. It does not
    # feed the plan's own price — see the note at the top of this file.
    number = _int_env("NUMBER_RENTAL_PRICE_PAISE", 55_900)
    price = _int_env("STARTER_PLAN_PRICE_PAISE", 299_900)
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
            price=price,
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
