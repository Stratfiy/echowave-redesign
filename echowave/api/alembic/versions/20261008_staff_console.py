"""Launch stream staff: console roles, staff commands, refunds, incidents,
evaluation cases and runs, and a staff suspension on users

Additive only. Eight new tables and one nullable column; nothing existing is
changed or backfilled, so the code before this revision runs unchanged
against the upgraded schema. Downgrade drops them, losing console role
grants, staff command history, refund records, incidents and evaluation
results (the admin audit log keeps a line for each staff action).

Revision ID: 20261008staff
Revises: 202610071500shell
"""

import sqlalchemy as sa
from alembic import op

revision = "20261008staff"
down_revision = "202610071500shell"
branch_labels = None
depends_on = None


def _ts(name: str, nullable: bool = True) -> sa.Column:
    return sa.Column(name, sa.DateTime(timezone=True), nullable=nullable)


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("staff_suspended_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        "staff_role_grants",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("reason", sa.String(500), nullable=False),
        sa.Column(
            "granted_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=False
        ),
        sa.Column("command_id", sa.Integer(), nullable=True),
        _ts("created_at", nullable=False),
        _ts("revoked_at"),
        sa.Column("revoked_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
    )
    op.create_index("ix_staff_role_grants_user_id", "staff_role_grants", ["user_id"])
    op.create_index(
        "uq_staff_role_grants_live",
        "staff_role_grants",
        ["user_id", "role"],
        unique=True,
        postgresql_where=sa.text("revoked_at IS NULL"),
    )

    op.create_table(
        "staff_commands",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("command", sa.String(48), nullable=False),
        sa.Column("environment", sa.String(32), nullable=False),
        sa.Column("target", sa.JSON(), nullable=False),
        sa.Column("reason", sa.String(500), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False, unique=True),
        sa.Column("state", sa.String(24), nullable=False),
        sa.Column(
            "requested_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=False
        ),
        sa.Column("requested_roles", sa.JSON(), nullable=False),
        sa.Column(
            "approval_required",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "approved_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=True
        ),
        _ts("approved_at"),
        sa.Column("preview", sa.JSON(), nullable=True),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.Column("reason_code", sa.String(64), nullable=True),
        _ts("expires_at"),
        _ts("created_at", nullable=False),
        _ts("started_at"),
        _ts("finished_at"),
    )
    op.create_index("ix_staff_commands_command", "staff_commands", ["command"])
    op.create_index("ix_staff_commands_state", "staff_commands", ["state"])

    op.create_table(
        "staff_refunds",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "payment_id",
            sa.Integer(),
            sa.ForeignKey("payments.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("amount_minor", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("state", sa.String(24), nullable=False),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("provider_refund_id", sa.String(64), nullable=True),
        sa.Column("command_id", sa.Integer(), nullable=False, unique=True),
        sa.Column("reason_code", sa.String(64), nullable=True),
        _ts("created_at", nullable=False),
        _ts("updated_at", nullable=False),
        _ts("reconciled_at"),
    )
    op.create_index("ix_staff_refunds_payment_id", "staff_refunds", ["payment_id"])
    op.create_index(
        "ix_staff_refunds_organization_id", "staff_refunds", ["organization_id"]
    )

    op.create_table(
        "staff_incidents",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("impact", sa.String(500), nullable=False),
        sa.Column("severity", sa.String(8), nullable=False),
        sa.Column("state", sa.String(24), nullable=False),
        sa.Column("environment", sa.String(32), nullable=False),
        sa.Column(
            "owner_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True
        ),
        sa.Column("links", sa.JSON(), nullable=False),
        sa.Column("opened_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column(
            "revision", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
        _ts("opened_at", nullable=False),
        _ts("resolved_at"),
    )
    op.create_index("ix_staff_incidents_state", "staff_incidents", ["state"])

    op.create_table(
        "staff_incident_steps",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "incident_id",
            sa.Integer(),
            sa.ForeignKey("staff_incidents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("summary", sa.String(500), nullable=False),
        sa.Column("outcome", sa.String(16), nullable=True),
        sa.Column("ops_command_id", sa.Integer(), nullable=True),
        sa.Column(
            "actor_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False
        ),
        _ts("created_at", nullable=False),
        sa.UniqueConstraint("incident_id", "sequence", name="uq_incident_step_seq"),
    )
    op.create_index(
        "ix_staff_incident_steps_incident_id", "staff_incident_steps", ["incident_id"]
    )

    op.create_table(
        "quality_eval_cases",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("dataset", sa.String(64), nullable=False),
        sa.Column("case_key", sa.String(64), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("input", sa.JSON(), nullable=False),
        sa.Column("expected", sa.JSON(), nullable=False),
        sa.Column("subgroup", sa.JSON(), nullable=False),
        sa.Column("review_state", sa.String(16), nullable=False),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("source_ref", sa.String(64), nullable=True),
        sa.Column(
            "created_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=False
        ),
        sa.Column(
            "reviewed_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=True
        ),
        _ts("created_at", nullable=False),
        sa.UniqueConstraint(
            "dataset", "case_key", "version", name="uq_quality_eval_case_version"
        ),
    )
    op.create_index("ix_quality_eval_cases_dataset", "quality_eval_cases", ["dataset"])

    op.create_table(
        "quality_eval_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("dataset", sa.String(64), nullable=False),
        sa.Column("dataset_version", sa.String(16), nullable=False),
        sa.Column("case_ids", sa.JSON(), nullable=False),
        sa.Column("config", sa.JSON(), nullable=False),
        sa.Column("config_version", sa.String(16), nullable=False),
        sa.Column("runner", sa.String(32), nullable=False),
        sa.Column("baseline_run_id", sa.Integer(), nullable=True),
        sa.Column("state", sa.String(24), nullable=False),
        sa.Column("totals", sa.JSON(), nullable=True),
        sa.Column("command_id", sa.Integer(), nullable=True),
        sa.Column(
            "requested_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=False
        ),
        _ts("created_at", nullable=False),
        _ts("started_at"),
        _ts("finished_at"),
    )
    op.create_index("ix_quality_eval_runs_dataset", "quality_eval_runs", ["dataset"])
    op.create_index("ix_quality_eval_runs_state", "quality_eval_runs", ["state"])

    op.create_table(
        "quality_eval_results",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "run_id",
            sa.Integer(),
            sa.ForeignKey("quality_eval_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "case_id",
            sa.Integer(),
            sa.ForeignKey("quality_eval_cases.id"),
            nullable=False,
        ),
        sa.Column("outcome", sa.String(16), nullable=False),
        sa.Column("checks", sa.JSON(), nullable=False),
        sa.Column("judge", sa.JSON(), nullable=True),
        sa.Column("output", sa.JSON(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("cost_paise", sa.Integer(), nullable=True),
        sa.Column("error_code", sa.String(64), nullable=True),
        _ts("created_at", nullable=False),
        sa.UniqueConstraint("run_id", "case_id", name="uq_quality_eval_result"),
    )
    op.create_index(
        "ix_quality_eval_results_run_id", "quality_eval_results", ["run_id"]
    )


def downgrade() -> None:
    op.drop_table("quality_eval_results")
    op.drop_table("quality_eval_runs")
    op.drop_table("quality_eval_cases")
    op.drop_table("staff_incident_steps")
    op.drop_table("staff_incidents")
    op.drop_table("staff_refunds")
    op.drop_table("staff_commands")
    op.drop_table("staff_role_grants")
    op.drop_column("users", "staff_suspended_at")
