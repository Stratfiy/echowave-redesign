"""A plan can carry the per-minute fee it entitles an account to.

The platform fee is already per-account and effective-dated — that is what
``organization_rate_history`` is — but the only thing that ever wrote a row was
an operator on the admin screen. So a tiered price list was a thing you could
describe and not a thing the product did: every account on every tier kept the
list rate until somebody remembered to go and change it, one account at a time.

The fee is what a larger plan actually discounts. Balance and numbers can be
made generous cheaply; the per-minute rate is the number a customer compares
against a competitor, and the one they are buying a bigger plan to move.

Null rather than a default, and the distinction is the whole design: null means
"this plan says nothing about the fee", so the account keeps whatever rate it
already had — a negotiated rate, or the platform list price. Only a plan with an
explicit figure moves anybody. That way adding this column changes nothing until
an operator prices a tier, and a tier priced later cannot silently re-rate the
accounts already on it.

Revision ID: c8e3a1f60b47
Revises: b4d7e2f1a9c3
"""

import sqlalchemy as sa
from alembic import op

revision = "c8e3a1f60b47"
down_revision = "b4d7e2f1a9c3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "subscription_plans",
        sa.Column("platform_rate_mpaise", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("subscription_plans", "platform_rate_mpaise")
