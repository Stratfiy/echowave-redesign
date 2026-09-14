"""USD top-up packs (KAN-135): the currency, minor amount, FX and credits
granted on a payment.

Revision ID: e9f0a1b2c3d4
Revises: d8e9f0a1b2c3
Create Date: 2026-09-14
"""

import sqlalchemy as sa
from alembic import op

revision = "e9f0a1b2c3d4"
down_revision = "d8e9f0a1b2c3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "payments",
        sa.Column(
            "currency", sa.String(3), nullable=False, server_default=sa.text("'INR'")
        ),
    )
    op.add_column("payments", sa.Column("amount_minor", sa.BigInteger(), nullable=True))
    op.add_column(
        "payments", sa.Column("fx_paise_per_usd", sa.Integer(), nullable=True)
    )
    op.add_column("payments", sa.Column("credits_granted", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("payments", "credits_granted")
    op.drop_column("payments", "fx_paise_per_usd")
    op.drop_column("payments", "amount_minor")
    op.drop_column("payments", "currency")
