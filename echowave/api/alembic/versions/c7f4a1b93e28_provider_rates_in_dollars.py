"""Let a provider rate be quoted in dollars as well as rupees

Revision ID: c7f4a1b93e28
Revises: a3c9e1b47d02
Create Date: 2026-08-24

``provider_rates`` held one currency: millipaise. That is right for a vendor
who invoices in rupees and wrong for one who invoices in dollars, and this
deployment buys from both — Sarvam bills ₹30/hour, OpenAI bills per million
tokens in USD.

Storing a dollar vendor's price as rupees freezes the conversion at whatever
the rate was on the day somebody typed it. The dollar price did not change
when the rupee moved; the cost did, and the card would not know.

So this mirrors what ``organization_rate_history`` already does for the
platform rate: two nullable columns and a constraint that exactly one is set.
A dollar row is converted at *read* time against the FX in force, so its cost
tracks the rupee. A rupee row has no FX applied at all, which is the whole
reason it is written that way.

Existing rows are untouched and stay rupee-native. That is correct rather than
merely convenient: they were seeded by converting a USD list price at ₹96, and
nobody can now say whether the operator meant the dollar price or the rupee
one. Re-entering a vendor in dollars is a deliberate act, on the screen.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c7f4a1b93e28"
down_revision: Union[str, Sequence[str], None] = "a3c9e1b47d02"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "provider_rates",
        sa.Column("rate_micros_usd", sa.Integer(), nullable=True),
    )
    # Nullable only now that the dollar column can carry the price instead.
    op.alter_column("provider_rates", "rate_mpaise", nullable=True)
    op.create_check_constraint(
        "ck_provider_rates_one_currency",
        "provider_rates",
        "(rate_mpaise IS NULL) <> (rate_micros_usd IS NULL)",
    )


def downgrade() -> None:
    # A dollar-quoted row has no rupee price to fall back to, and inventing one
    # would need an exchange rate this migration has no business choosing. They
    # are dropped rather than silently converted at a rate nobody picked.
    op.execute("DELETE FROM provider_rates WHERE rate_micros_usd IS NOT NULL")
    op.drop_constraint("ck_provider_rates_one_currency", "provider_rates")
    op.drop_column("provider_rates", "rate_micros_usd")
    op.alter_column("provider_rates", "rate_mpaise", nullable=False)
