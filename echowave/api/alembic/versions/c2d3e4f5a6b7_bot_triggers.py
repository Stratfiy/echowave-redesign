"""Bot triggers (KAN-137): a webhook doorbell on a bot, written as a sentence.

Revision ID: c2d3e4f5a6b7
Revises: b1c2d3e4f5a6
Create Date: 2026-09-15
"""

import sqlalchemy as sa
from alembic import op

revision = "c2d3e4f5a6b7"
down_revision = "b1c2d3e4f5a6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "bot_triggers",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "organization_id",
            sa.Integer(),
            sa.ForeignKey("organizations.id"),
            nullable=False,
        ),
        sa.Column(
            "workflow_id",
            sa.Integer(),
            sa.ForeignKey("workflows.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("uuid", sa.String(36), nullable=False),
        sa.Column("secret", sa.String(64), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column(
            "source",
            sa.String(16),
            nullable=False,
            server_default=sa.text("'webhook'"),
        ),
        sa.Column("sentence", sa.Text(), nullable=False, server_default=""),
        sa.Column("instruction", sa.Text(), nullable=False, server_default=""),
        sa.Column("fields", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("filter", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column(
            "is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")
        ),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_fired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "fired_count", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
    )
    op.create_index("ix_bot_triggers_id", "bot_triggers", ["id"])
    op.create_index(
        "ix_bot_triggers_organization_id", "bot_triggers", ["organization_id"]
    )
    op.create_index("ix_bot_triggers_workflow_id", "bot_triggers", ["workflow_id"])
    op.create_index("ix_bot_triggers_uuid", "bot_triggers", ["uuid"], unique=True)
    op.create_index(
        "ix_bot_triggers_org_workflow",
        "bot_triggers",
        ["organization_id", "workflow_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_bot_triggers_org_workflow", table_name="bot_triggers")
    op.drop_index("ix_bot_triggers_uuid", table_name="bot_triggers")
    op.drop_index("ix_bot_triggers_workflow_id", table_name="bot_triggers")
    op.drop_index("ix_bot_triggers_organization_id", table_name="bot_triggers")
    op.drop_index("ix_bot_triggers_id", table_name="bot_triggers")
    op.drop_table("bot_triggers")
