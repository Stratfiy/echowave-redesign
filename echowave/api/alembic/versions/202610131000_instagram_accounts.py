"""Instagram: the accounts a workspace connected, and who answers them

Additive only: one table (``instagram_accounts``,
services/messaging/instagram.py). Each row is an Instagram professional
account a workspace signed in with, its sealed long-lived token, and the
agent that answers its messages.

Downgrading drops the table.

Revision ID: 20261013instagram
Revises: 20261012llmcallusage
"""

import sqlalchemy as sa
from alembic import op

revision = "20261013instagram"
down_revision = "20261012llmcallusage"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "instagram_accounts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "organization_id",
            sa.Integer(),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("ig_user_id", sa.String(64), nullable=False),
        sa.Column("username", sa.String(128), nullable=False, server_default=""),
        sa.Column("encrypted_token", sa.Text(), nullable=False),
        sa.Column("token_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "workflow_id",
            sa.Integer(),
            sa.ForeignKey("workflows.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")
        ),
        sa.Column(
            "connected_by",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("ig_user_id", name="uq_instagram_accounts_ig_user"),
    )
    op.create_index(
        "ix_instagram_accounts_organization_id",
        "instagram_accounts",
        ["organization_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_instagram_accounts_organization_id", table_name="instagram_accounts"
    )
    op.drop_table("instagram_accounts")
