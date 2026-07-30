"""persist uncosted usage

Records, per call, which measured usage we held no rate for.

This was previously only a log line, which made it the one thing capable of
making every margin figure on the dashboard quietly optimistic: unpriced usage
is money we paid a provider and did not record as cost. Persisting it lets the
unit-economics screen say how much of the book is incomplete rather than
presenting an overstated margin as fact.

Left NULL for calls costed before this landed — that is genuinely unknown, and
is distinct from the empty list a freshly costed call writes when nothing was
unpriced.

Revision ID: d18f4c60ba73
Revises: c73e1b5a94d2
Create Date: 2026-07-30 04:12:31.907714

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d18f4c60ba73"
down_revision: Union[str, None] = "c73e1b5a94d2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "workflow_runs", sa.Column("uncosted_usage", sa.JSON(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("workflow_runs", "uncosted_usage")
