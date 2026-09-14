"""Onboarding credits: one trial row per step per organisation.

The signup bonus already has ``uq_credit_ledger_signup_bonus``; the five
further steps share this index so a race between two requests that both find
a step unpaid can only write one row.

Revision ID: c7d8e9f0a1b2
Revises: b6c7d8e9f0a1
Create Date: 2026-09-14
"""

import sqlalchemy as sa
from alembic import op

revision = "c7d8e9f0a1b2"
down_revision = "b6c7d8e9f0a1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "uq_credit_ledger_onboarding_step",
        "credit_ledger",
        ["organization_id", "ref_type"],
        unique=True,
        postgresql_where=sa.text("kind = 'trial' AND ref_type LIKE 'onboarding:%'"),
    )


def downgrade() -> None:
    op.drop_index("uq_credit_ledger_onboarding_step", table_name="credit_ledger")
