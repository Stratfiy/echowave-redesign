"""The office's task board (KAN-140 P1): people and bots file, take and
finish tasks on one shared surface.

Revision ID: e4f5a6b7c8d9
Revises: d3e4f5a6b7c8
Create Date: 2026-09-15
"""

import sqlalchemy as sa
from alembic import op

revision = "e4f5a6b7c8d9"
down_revision = "d3e4f5a6b7c8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agent_tasks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "organization_id",
            sa.Integer(),
            sa.ForeignKey("organizations.id"),
            nullable=False,
        ),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("brief", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "status", sa.String(16), nullable=False, server_default=sa.text("'todo'")
        ),
        sa.Column(
            "from_workflow_id",
            sa.Integer(),
            sa.ForeignKey("workflows.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "assignee_workflow_id",
            sa.Integer(),
            sa.ForeignKey("workflows.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("source_run_id", sa.Integer(), nullable=True),
        sa.Column("workflow_run_id", sa.Integer(), nullable=True),
        sa.Column("depth", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("result", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_agent_tasks_id", "agent_tasks", ["id"])
    op.create_index(
        "ix_agent_tasks_organization_id", "agent_tasks", ["organization_id"]
    )
    op.create_index("ix_agent_tasks_status", "agent_tasks", ["status"])
    op.create_index(
        "ix_agent_tasks_assignee_workflow_id", "agent_tasks", ["assignee_workflow_id"]
    )
    op.create_index(
        "ix_agent_tasks_org_status", "agent_tasks", ["organization_id", "status"]
    )


def downgrade() -> None:
    op.drop_index("ix_agent_tasks_org_status", table_name="agent_tasks")
    op.drop_index("ix_agent_tasks_assignee_workflow_id", table_name="agent_tasks")
    op.drop_index("ix_agent_tasks_status", table_name="agent_tasks")
    op.drop_index("ix_agent_tasks_organization_id", table_name="agent_tasks")
    op.drop_index("ix_agent_tasks_id", table_name="agent_tasks")
    op.drop_table("agent_tasks")
