"""Friend referral (KAN-133): one reward per pair, and a per-account cap.

Revision ID: f0a1b2c3d4e5
Revises: e9f0a1b2c3d4
Create Date: 2026-09-15
"""

import sqlalchemy as sa
from alembic import op

revision = "f0a1b2c3d4e5"
down_revision = "e9f0a1b2c3d4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "organizations", sa.Column("referral_monthly_cap", sa.Integer(), nullable=True)
    )
    # Both sides of one referral write a trial row keyed on the referred
    # account; this makes each side's row unique so a replayed webhook or a
    # racing capture can only ever pay once.
    op.create_index(
        "uq_credit_ledger_referral",
        "credit_ledger",
        ["organization_id", "ref_id"],
        unique=True,
        postgresql_where=sa.text("kind = 'trial' AND ref_type = 'referral'"),
    )


def downgrade() -> None:
    op.drop_index("uq_credit_ledger_referral", table_name="credit_ledger")
    op.drop_column("organizations", "referral_monthly_cap")
