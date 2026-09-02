"""add missed_call_events

The interesting half of missed-call callback is invisible without this table.
A callback that connects becomes an ordinary workflow run; a callback we
refused (cooldown, daily cap, loop guard, closed window) leaves no run at all,
so the operator cannot tell a quiet hoarding from a silently declining one.

Revision ID: d51b8e2fa7c9
Revises: c4e1a9d70b32
Create Date: 2026-09-02

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d51b8e2fa7c9"
down_revision: Union[str, None] = "c4e1a9d70b32"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "missed_call_events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("telephony_phone_number_id", sa.Integer(), nullable=False),
        sa.Column("caller", sa.String(length=32), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=True),
        sa.Column(
            "outcome",
            sa.String(length=24),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("refusal_reason", sa.Text(), nullable=True),
        sa.Column("workflow_run_id", sa.Integer(), nullable=True),
        sa.Column(
            "received_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["telephony_phone_number_id"],
            ["telephony_phone_numbers.id"],
            ondelete="CASCADE",
        ),
        # SET NULL, not CASCADE: retention purges call data long before the
        # operator stops caring how many people rang their hoarding.
        sa.ForeignKeyConstraint(
            ["workflow_run_id"], ["workflow_runs.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_missed_call_events_id"), "missed_call_events", ["id"], unique=False
    )
    op.create_index(
        "ix_missed_call_events_org_received",
        "missed_call_events",
        ["organization_id", "received_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_missed_call_events_org_received", table_name="missed_call_events"
    )
    op.drop_index(op.f("ix_missed_call_events_id"), table_name="missed_call_events")
    op.drop_table("missed_call_events")
