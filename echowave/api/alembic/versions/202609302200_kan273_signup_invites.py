"""signup_invites and signup_invite_redemptions (INVITE-1, KAN-273).

Revision ID: 202609302200kan273
Revises: e8b4d6f2a1c9
Create Date: 2026-09-30
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "202609302200kan273"
down_revision: Union[str, None] = "e8b4d6f2a1c9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "signup_invites",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("code", sa.String(32), nullable=False),
        sa.Column("email", sa.String(320), nullable=True),
        sa.Column("max_uses", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("uses", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("note", sa.String(200), nullable=True),
        sa.Column(
            "created_by_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("max_uses >= 1", name="ck_signup_invites_max_uses"),
        sa.CheckConstraint(
            "uses >= 0 AND uses <= max_uses", name="ck_signup_invites_uses"
        ),
    )
    op.create_index("ix_signup_invites_code", "signup_invites", ["code"], unique=True)
    op.create_table(
        "signup_invite_redemptions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "invite_id",
            sa.Integer(),
            sa.ForeignKey("signup_invites.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "organization_id",
            sa.Integer(),
            sa.ForeignKey("organizations.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("door", sa.String(16), nullable=False),
        sa.Column("redeemed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_signup_invite_redemptions_invite_id",
        "signup_invite_redemptions",
        ["invite_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_signup_invite_redemptions_invite_id",
        table_name="signup_invite_redemptions",
    )
    op.drop_table("signup_invite_redemptions")
    op.drop_index("ix_signup_invites_code", table_name="signup_invites")
    op.drop_table("signup_invites")
