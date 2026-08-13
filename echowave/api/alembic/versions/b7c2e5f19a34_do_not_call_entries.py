"""do_not_call_entries: the per-organization do-not-disturb list

Revision ID: b7c2e5f19a34
Revises: 294fabcaca38
Create Date: 2026-08-12

The list is per-organization, not global. A number that asked one customer to
stop has not asked every customer on the platform to stop, and a shared table
would leak one customer's contact list into another's — these rows are
themselves personal data.

phone_number holds the normalised key produced by
services/compliance/dnd.normalise_number, never the string a user typed. The
unique constraint is what makes re-uploading last month's file idempotent
rather than doubling the table.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b7c2e5f19a34"
down_revision: Union[str, Sequence[str], None] = "294fabcaca38"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "do_not_call_entries",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("phone_number", sa.String(length=20), nullable=False),
        sa.Column(
            "source",
            sa.String(length=32),
            nullable=False,
            server_default="manual",
        ),
        sa.Column("note", sa.String(length=255), nullable=True),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=True,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id", "phone_number", name="_dnc_org_number_uc"
        ),
    )
    op.create_index(
        "ix_do_not_call_entries_org_number",
        "do_not_call_entries",
        ["organization_id", "phone_number"],
    )
    op.create_index(
        op.f("ix_do_not_call_entries_id"), "do_not_call_entries", ["id"]
    )

    # Why a campaign row was refused before dialling. Separate from
    # retry_reason, which is why a call that was placed did not connect —
    # conflating them would let a refusal enter the retry path, which is the
    # breach the refusal exists to prevent.
    op.add_column(
        "queued_runs",
        sa.Column("refusal_reason", sa.String(length=64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("queued_runs", "refusal_reason")
    op.drop_index(op.f("ix_do_not_call_entries_id"), table_name="do_not_call_entries")
    op.drop_index(
        "ix_do_not_call_entries_org_number", table_name="do_not_call_entries"
    )
    op.drop_table("do_not_call_entries")
