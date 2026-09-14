"""Provenance on a provider rate: where it was read, and when.

KAN-58. Every row in the rate book should name the page or invoice its figure
came from and the day somebody read it there. Without that, a seeded list
price and a negotiated contract rate look identical on the card, and neither
can be audited — only believed.

``source_checked_on`` is text (``YYYY-MM-DD``, or ``YYYY-MM`` for the rows the
survey did not re-read) rather than a date, so the column never forces a day
onto a figure that was only ever dated to a month.

Revision ID: e3f4a5b6c7d8
Revises: d2e3f4a5b6c7
"""

import sqlalchemy as sa
from alembic import op

revision = "e3f4a5b6c7d8"
down_revision = "d2e3f4a5b6c7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "provider_rates", sa.Column("source_url", sa.String(length=512), nullable=True)
    )
    op.add_column(
        "provider_rates",
        sa.Column("source_checked_on", sa.String(length=10), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("provider_rates", "source_checked_on")
    op.drop_column("provider_rates", "source_url")
