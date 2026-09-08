"""A second net price, so one plan can carry one headline in both tax regimes.

Parented on b3f7c02d5e14 to keep a single head — see
api/tests/test_migration_heads.py.

A domestic account pays the net plus GST; a zero-rated export account pays the
net outright. So a plan sold as one price to everybody — "₹2,999, tax included
in India" — is two different nets that happen to collect the same amount. One
column cannot hold both.

Null means "same as price_paise", which is exactly what every existing plan did
before this column existed, so nothing is repriced by running this.

Revision ID: c8e5a13f7b20
Revises: b3f7c02d5e14
"""

import sqlalchemy as sa
from alembic import op

revision = "c8e5a13f7b20"
down_revision = "b3f7c02d5e14"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "subscription_plans",
        sa.Column("price_paise_export", sa.BigInteger(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("subscription_plans", "price_paise_export")
