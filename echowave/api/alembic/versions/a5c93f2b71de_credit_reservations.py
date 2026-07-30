"""credit reservations

Funds held while a call is in flight. No new table — a reservation is an
ordinary negative credit_ledger row, so it counts against the balance from the
moment it is written and no balance query can forget to subtract it.

What this adds is the two indexes that make that safe:

* a partial unique index so one run can hold funds at most once. A retried
  start that stacked a second hold would take spending power away twice for a
  single call, and only the first would ever be released.
* a partial index on created_at, because the sweeper scans open reservations by
  age every few minutes and would otherwise read every ledger row on the
  platform to do it.

Revision ID: a5c93f2b71de
Revises: f4b8d2e07c19
Create Date: 2026-07-30 09:31:07.442918

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a5c93f2b71de"
down_revision: Union[str, None] = "f4b8d2e07c19"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index(
        "uq_credit_ledger_reservation_ref",
        "credit_ledger",
        ["organization_id", "ref_type", "ref_id"],
        unique=True,
        postgresql_where=sa.text("kind = 'reservation' AND ref_id IS NOT NULL"),
    )
    op.create_index(
        "ix_credit_ledger_reservation_open",
        "credit_ledger",
        ["created_at"],
        postgresql_where=sa.text("kind = 'reservation'"),
    )


def downgrade() -> None:
    op.drop_index("ix_credit_ledger_reservation_open", table_name="credit_ledger")
    op.drop_index("uq_credit_ledger_reservation_ref", table_name="credit_ledger")
