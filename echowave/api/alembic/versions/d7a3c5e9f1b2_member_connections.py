"""member_connections: which member authorised which connected account (WS-1).

Revision ID: d7a3c5e9f1b2
Revises: c6f2a4b8d1e3
Create Date: 2026-09-30
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "d7a3c5e9f1b2"
down_revision: Union[str, None] = "c6f2a4b8d1e3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "member_connections",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "organization_id",
            sa.Integer(),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("toolkit", sa.String(length=64), nullable=False),
        sa.Column("connected_account_id", sa.String(length=128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_member_connections_id", "member_connections", ["id"])
    op.create_index(
        "ix_member_connections_org_user",
        "member_connections",
        ["organization_id", "user_id"],
    )
    op.create_index(
        "ix_member_connections_account",
        "member_connections",
        ["organization_id", "connected_account_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_member_connections_account", table_name="member_connections")
    op.drop_index("ix_member_connections_org_user", table_name="member_connections")
    op.drop_index("ix_member_connections_id", table_name="member_connections")
    op.drop_table("member_connections")
