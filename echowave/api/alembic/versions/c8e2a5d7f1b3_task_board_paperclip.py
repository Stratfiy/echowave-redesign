"""the task board on paperclip's issue model (TB-1)

Revision ID: c8e2a5d7f1b3
Revises: a7c0e3f6b9d2
Create Date: 2026-09-22

Statuses take paperclip's names: ``doing`` becomes ``in_progress``,
``waiting`` and ``could_not`` become ``blocked`` (both were work a person had
to do something about; the result column keeps why). ``backlog``,
``in_review`` and ``cancelled`` are new. Every task gets a number counted
inside its workspace, oldest first, so identifiers are stable from here on.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c8e2a5d7f1b3"
down_revision: Union[str, None] = "a7c0e3f6b9d2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "agent_tasks",
        sa.Column(
            "priority", sa.String(8), nullable=False, server_default=sa.text("'medium'")
        ),
    )
    op.add_column(
        "agent_tasks",
        sa.Column(
            "assignee_user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column(
        "agent_tasks",
        sa.Column(
            "parent_id",
            sa.Integer(),
            sa.ForeignKey("agent_tasks.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column("agent_tasks", sa.Column("blocked_by", sa.JSON(), nullable=True))
    op.add_column("agent_tasks", sa.Column("number", sa.Integer(), nullable=True))
    op.execute(
        """
        UPDATE agent_tasks AS t
        SET number = numbered.rn
        FROM (
            SELECT id, ROW_NUMBER() OVER (PARTITION BY organization_id ORDER BY id) AS rn
            FROM agent_tasks
        ) AS numbered
        WHERE numbered.id = t.id
        """
    )
    op.create_unique_constraint(
        "uq_agent_tasks_org_number", "agent_tasks", ["organization_id", "number"]
    )
    op.execute("UPDATE agent_tasks SET status = 'in_progress' WHERE status = 'doing'")
    op.execute(
        "UPDATE agent_tasks SET status = 'blocked' WHERE status IN ('waiting', 'could_not')"
    )

    op.create_table(
        "agent_task_comments",
        sa.Column("id", sa.Integer(), primary_key=True, index=True),
        sa.Column(
            "organization_id",
            sa.Integer(),
            sa.ForeignKey("organizations.id"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "task_id",
            sa.Integer(),
            sa.ForeignKey("agent_tasks.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "author_user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "author_workflow_id",
            sa.Integer(),
            sa.ForeignKey("workflows.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("agent_task_comments")
    op.execute(
        "UPDATE agent_tasks SET status = 'doing' WHERE status IN ('in_progress', 'in_review')"
    )
    op.execute("UPDATE agent_tasks SET status = 'waiting' WHERE status = 'blocked'")
    op.execute("UPDATE agent_tasks SET status = 'could_not' WHERE status = 'cancelled'")
    op.execute("UPDATE agent_tasks SET status = 'todo' WHERE status = 'backlog'")
    op.drop_constraint("uq_agent_tasks_org_number", "agent_tasks", type_="unique")
    op.drop_column("agent_tasks", "number")
    op.drop_column("agent_tasks", "blocked_by")
    op.drop_column("agent_tasks", "parent_id")
    op.drop_column("agent_tasks", "assignee_user_id")
    op.drop_column("agent_tasks", "priority")
