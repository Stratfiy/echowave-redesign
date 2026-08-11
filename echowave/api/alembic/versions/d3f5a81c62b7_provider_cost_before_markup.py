"""Record what a provider line cost us, separately from what we charged

Managed provider usage is now billed at a markup (MANAGED_PROVIDER_MARKUP_BPS,
1.3x by default) rather than passed through at cost. ``cost_paise`` keeps its
meaning — what the customer was charged, and what the ledger debited — and this
adds the other half: what the vendor charged us.

Both are stored because storing only one destroys the other. Baking the markup
into the rate card would have left provider_rates holding retail, and every
margin figure computed from it would read zero. Deriving the cost back out by
dividing by today's multiplier would be worse: the multiplier is a setting, and
an old receipt would silently re-price itself whenever it changed.

The backfill sets provider_cost_paise = cost_paise for existing rows, which is
exactly right — every one of them was written when usage was passed through at
cost, so the two were the same number.

Revision ID: d3f5a81c62b7
Revises: c7a1f4e93b28
"""

import sqlalchemy as sa
from alembic import op

revision = "d3f5a81c62b7"
down_revision = "c7a1f4e93b28"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "call_cost_items",
        sa.Column(
            "provider_cost_paise",
            sa.BigInteger(),
            nullable=False,
            server_default="0",
        ),
    )
    # Every existing row predates the markup, so it was charged at cost.
    op.execute("UPDATE call_cost_items SET provider_cost_paise = cost_paise")


def downgrade() -> None:
    op.drop_column("call_cost_items", "provider_cost_paise")
