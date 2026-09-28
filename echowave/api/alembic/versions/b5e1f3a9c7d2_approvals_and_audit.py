"""approval rules and the audit log (KAN-160, E-1)

Revision ID: b5e1f3a9c7d2
Revises: a7d2e9c4b1f6
Create Date: 2026-09-28

Two tables, additive, read by nothing while APPROVALS_2026_09_ENABLED is
off. ``approval_rules``: per workspace, who must approve what -- by subject
(a document kind, a card, a decision) and amount band. ``audit_entries``:
append-only, what a person or the platform did to something that matters,
with before and after, exportable.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b5e1f3a9c7d2"
down_revision: Union[str, None] = "a7d2e9c4b1f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "approval_rules",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "organization_id",
            sa.Integer(),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        # What the rule covers: a register kind (purchase_order, tax_invoice,
        # ...), "card" for a proposed action, "decision" for a bot's question,
        # or "*" for everything.
        sa.Column("subject", sa.String(length=32), nullable=False),
        sa.Column("min_amount_paise", sa.BigInteger(), nullable=True),
        sa.Column("max_amount_paise", sa.BigInteger(), nullable=True),
        # Who: a role (admin, owner) or a named member.
        sa.Column("approver_role", sa.String(length=16), nullable=True),
        sa.Column("approver_user_id", sa.Integer(), nullable=True),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_table(
        "audit_entries",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "organization_id",
            sa.Integer(),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
            index=True,
        ),
        sa.Column("actor_user_id", sa.Integer(), nullable=True),
        sa.Column("actor", sa.String(length=120), nullable=False),
        sa.Column("action", sa.String(length=48), nullable=False),
        sa.Column("subject_kind", sa.String(length=32), nullable=False),
        sa.Column("subject_id", sa.String(length=64), nullable=True),
        sa.Column("subject", sa.String(length=255), nullable=True),
        sa.Column("before", sa.JSON(), nullable=True),
        sa.Column("after", sa.JSON(), nullable=True),
        sa.Column("note", sa.String(length=500), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("audit_entries")
    op.drop_table("approval_rules")
