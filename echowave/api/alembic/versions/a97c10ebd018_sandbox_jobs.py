"""sandbox jobs: the record of a script a bot ran in the sandbox

Revision ID: a97c10ebd018
Revises: f5a6b7c8d9e0
Create Date: 2026-09-15 14:30:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a97c10ebd018"
down_revision: Union[str, None] = "f5a6b7c8d9e0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "sandbox_jobs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "organization_id",
            sa.Integer(),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "workflow_id",
            sa.Integer(),
            sa.ForeignKey("workflows.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("workflow_run_id", sa.Integer(), nullable=True),
        sa.Column(
            "status", sa.String(length=16), nullable=False, server_default="running"
        ),
        sa.Column("code_hash", sa.String(length=32), nullable=False),
        sa.Column("code_chars", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("calls", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("exit_code", sa.Integer(), nullable=True),
        sa.Column("output", sa.Text(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_sandbox_jobs_id", "sandbox_jobs", ["id"])
    op.create_index(
        "ix_sandbox_jobs_organization_id", "sandbox_jobs", ["organization_id"]
    )
    op.create_index(
        "ix_sandbox_jobs_workflow_run_id", "sandbox_jobs", ["workflow_run_id"]
    )
    op.create_index("ix_sandbox_jobs_status", "sandbox_jobs", ["status"])


def downgrade() -> None:
    op.drop_index("ix_sandbox_jobs_status", table_name="sandbox_jobs")
    op.drop_index("ix_sandbox_jobs_workflow_run_id", table_name="sandbox_jobs")
    op.drop_index("ix_sandbox_jobs_organization_id", table_name="sandbox_jobs")
    op.drop_index("ix_sandbox_jobs_id", table_name="sandbox_jobs")
    op.drop_table("sandbox_jobs")
