"""A payment records the bonus balance its pack granted.

KAN-55. A top-up pack can grant more balance than it costs (₹5,000 buys
10,500 credits, ₹5,250 of balance). ``amount_paise`` stays what was invoiced,
because tax is computed on it and the receipt voucher reads it;
``bonus_paise`` is the extra the ledger was credited, so a reconciliation of
payments against ledger rows still balances to the paisa.

Revision ID: f4a5b6c7d8e9
Revises: e3f4a5b6c7d8
"""

import sqlalchemy as sa
from alembic import op

revision = "f4a5b6c7d8e9"
down_revision = "e3f4a5b6c7d8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "payments",
        sa.Column("bonus_paise", sa.BigInteger(), nullable=False, server_default="0"),
    )
    op.add_column(
        "payments", sa.Column("pack_code", sa.String(length=16), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("payments", "pack_code")
    op.drop_column("payments", "bonus_paise")
