"""Durable audit of sensitive staff actions, impersonation first (KAN-82).

Revision ID: f5a6b7c8d9e0
Revises: e4f5a6b7c8d9
Create Date: 2026-09-15
"""

import sqlalchemy as sa
from alembic import op

revision = "f5a6b7c8d9e0"
down_revision = "e4f5a6b7c8d9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "admin_action_log",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "actor_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False
        ),
        sa.Column("action", sa.String(48), nullable=False),
        sa.Column(
            "target_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True
        ),
        sa.Column("target_provider_id", sa.String(128), nullable=True),
        sa.Column("target_organization_id", sa.Integer(), nullable=True),
        sa.Column("actor_ip", sa.String(64), nullable=True),
        sa.Column("note", sa.String(500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_admin_action_log_id", "admin_action_log", ["id"])
    op.create_index(
        "ix_admin_action_log_actor_user_id", "admin_action_log", ["actor_user_id"]
    )
    op.create_index("ix_admin_action_log_action", "admin_action_log", ["action"])
    op.create_index(
        "ix_admin_action_log_created_at", "admin_action_log", ["created_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_admin_action_log_created_at", table_name="admin_action_log")
    op.drop_index("ix_admin_action_log_action", table_name="admin_action_log")
    op.drop_index("ix_admin_action_log_actor_user_id", table_name="admin_action_log")
    op.drop_index("ix_admin_action_log_id", table_name="admin_action_log")
    op.drop_table("admin_action_log")
