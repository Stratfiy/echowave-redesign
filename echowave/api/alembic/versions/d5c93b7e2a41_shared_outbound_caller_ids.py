"""Decibyl's own numbers, lent to every account as outbound caller IDs.

A trial account has no telephony configuration and therefore cannot place a
call, which is the thing that gates anyone evaluating the product. Twilio solves
the same problem the same way: one shared number places every caller-ID
verification call it makes, and it never becomes a customer's number.

The column is what keeps that safe. Inbound dispatch resolves an organization
from `(provider, account_id, called number)` with no organization in the key, so
a shared row that could be dialled *in* would hand one customer's caller to
whichever tenant the database returned first. Both inbound lookups and the
routing-conflict check exclude these rows.

Additive and defaulted false, so every existing number keeps behaving exactly as
it does today.

Revision ID: d5c93b7e2a41
Revises: c7a41e5b9d38
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "d5c93b7e2a41"
down_revision: Union[str, None] = "c7a41e5b9d38"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "telephony_phone_numbers",
        sa.Column(
            "is_shared_outbound",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    # The pool is read on every outbound call that falls back to it, across all
    # organizations, so it is worth not scanning the table for two rows.
    op.create_index(
        "ix_phone_numbers_shared_outbound",
        "telephony_phone_numbers",
        ["is_shared_outbound"],
        postgresql_where=sa.text("is_shared_outbound"),
    )


def downgrade() -> None:
    op.drop_index(
        "ix_phone_numbers_shared_outbound", table_name="telephony_phone_numbers"
    )
    op.drop_column("telephony_phone_numbers", "is_shared_outbound")
