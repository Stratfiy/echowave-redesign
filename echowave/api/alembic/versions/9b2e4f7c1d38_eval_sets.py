"""Scripted callers an agent is rerun against after every edit.

Parented on 4c8e2b7a9d15 to keep this branch on a single head — see
api/tests/test_migration_heads.py.

Revision ID: 9b2e4f7c1d38
Revises: 4c8e2b7a9d15
"""

import sqlalchemy as sa
from alembic import op

revision = "9b2e4f7c1d38"
down_revision = "4c8e2b7a9d15"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "eval_cases",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("workflow_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("persona", sa.Text(), nullable=False),
        sa.Column("goal", sa.Text(), nullable=False),
        sa.Column(
            "must_say", sa.JSON(), nullable=False, server_default=sa.text("'[]'::json")
        ),
        sa.Column(
            "must_not_say",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'[]'::json"),
        ),
        sa.Column("max_turns", sa.Integer(), nullable=False, server_default="6"),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["workflow_id"], ["workflows.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_eval_cases_id", "eval_cases", ["id"])
    op.create_index(
        "ix_eval_cases_workflow", "eval_cases", ["workflow_id", "created_at"]
    )
    op.create_table(
        "eval_results",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("case_id", sa.Integer(), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("workflow_id", sa.Integer(), nullable=False),
        sa.Column("workflow_run_id", sa.Integer(), nullable=True),
        sa.Column(
            "status", sa.String(length=16), nullable=False, server_default="queued"
        ),
        sa.Column("verdict", sa.Text(), nullable=True),
        sa.Column(
            "transcript",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'[]'::json"),
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["case_id"], ["eval_cases.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["workflow_id"], ["workflows.id"]),
        sa.ForeignKeyConstraint(
            ["workflow_run_id"], ["workflow_runs.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_eval_results_id", "eval_results", ["id"])
    op.create_index("ix_eval_results_case", "eval_results", ["case_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_eval_results_case", table_name="eval_results")
    op.drop_index("ix_eval_results_id", table_name="eval_results")
    op.drop_table("eval_results")
    op.drop_index("ix_eval_cases_workflow", table_name="eval_cases")
    op.drop_index("ix_eval_cases_id", table_name="eval_cases")
    op.drop_table("eval_cases")
