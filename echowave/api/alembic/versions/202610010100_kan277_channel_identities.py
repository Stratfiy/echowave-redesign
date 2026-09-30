"""channel_identities, channel_link_codes, slack_installations: Decibyl in your apps (DCH-1, KAN-277).

Revision ID: 202610010100kan277
Revises: 202609302300kan255
Create Date: 2026-10-01
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "202610010100kan277"
down_revision: Union[str, None] = "202609302300kan255"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "channel_identities",
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
        sa.Column("channel", sa.String(16), nullable=False),
        sa.Column("external_id", sa.String(255), nullable=False),
        sa.Column("display_name", sa.String(200), nullable=True),
        sa.Column("conversation_ref", sa.JSON(), nullable=True),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("channel", "external_id", name="uq_channel_identity"),
    )
    op.create_index(
        "ix_channel_identities_organization_id",
        "channel_identities",
        ["organization_id"],
    )
    op.create_table(
        "channel_link_codes",
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
        sa.Column("channel", sa.String(16), nullable=False),
        sa.Column("code_hash", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_channel_link_codes_code_hash",
        "channel_link_codes",
        ["code_hash"],
        unique=True,
    )
    op.create_table(
        "slack_installations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "organization_id",
            sa.Integer(),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("team_id", sa.String(32), nullable=False),
        sa.Column("team_name", sa.String(200), nullable=True),
        sa.Column("bot_user_id", sa.String(32), nullable=True),
        sa.Column("encrypted_bot_token", sa.String(512), nullable=False),
        sa.Column(
            "installed_by_user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_slack_installations_team_id",
        "slack_installations",
        ["team_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ix_slack_installations_team_id", table_name="slack_installations")
    op.drop_table("slack_installations")
    op.drop_index("ix_channel_link_codes_code_hash", table_name="channel_link_codes")
    op.drop_table("channel_link_codes")
    op.drop_index(
        "ix_channel_identities_organization_id", table_name="channel_identities"
    )
    op.drop_table("channel_identities")
