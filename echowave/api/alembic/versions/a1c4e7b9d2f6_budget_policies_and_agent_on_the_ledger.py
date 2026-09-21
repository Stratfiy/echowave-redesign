"""Budget policies, and which agent each ledger debit belongs to (S-1)

Two things a customer's spend cap needs and the ledger did not have.

The ledger gains ``workflow_id``: a call's debit is stamped with its run's
agent, an event's debit with the bot that did the work. Existing call debits
are backfilled from their runs, which is the whole of history a cap can be
measured against today; event debits from before this migration carry NULL
and count against the workspace only, because the event's own id does not
say which bot made it.

``budget_policies`` and ``budget_incidents`` are the cap and the record of it
being crossed -- after the shape paperclip uses for the same thing, in credits
rather than cents. Nothing reads either table until
``BUDGET_POLICIES_ENABLED`` is on.

Revision ID: a1c4e7b9d2f6
Revises: e5b2c8d1f7a3
"""

import sqlalchemy as sa
from alembic import op

revision = "a1c4e7b9d2f6"
down_revision = "e5b2c8d1f7a3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "credit_ledger",
        sa.Column(
            "workflow_id",
            sa.Integer(),
            sa.ForeignKey("workflows.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_credit_ledger_usage_org_workflow_created",
        "credit_ledger",
        ["organization_id", "workflow_id", "created_at"],
        postgresql_where=sa.text("kind = 'usage'"),
    )
    # A call's debit is keyed on its run; the run knows its agent.
    op.execute(
        """
        UPDATE credit_ledger AS l
        SET workflow_id = r.workflow_id
        FROM workflow_runs AS r
        WHERE l.ref_type = 'workflow_run'
          AND l.workflow_id IS NULL
          AND l.ref_id ~ '^[0-9]+$'
          AND r.id = l.ref_id::integer
        """
    )

    op.create_table(
        "budget_policies",
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
            sa.ForeignKey("workflows.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("window_kind", sa.String(length=16), nullable=False),
        sa.Column("amount_paise", sa.BigInteger(), nullable=False),
        sa.Column("warn_percent", sa.Integer(), nullable=False, server_default="80"),
        sa.Column(
            "hard_stop", sa.Boolean(), nullable=False, server_default=sa.text("true")
        ),
        sa.Column(
            "is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")
        ),
        sa.Column(
            "created_by",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("amount_paise >= 0", name="ck_budget_policies_amount"),
        sa.CheckConstraint(
            "warn_percent >= 0 AND warn_percent <= 100",
            name="ck_budget_policies_warn_percent",
        ),
    )
    op.create_index("ix_budget_policies_id", "budget_policies", ["id"])
    op.create_index(
        "ix_budget_policies_org_active",
        "budget_policies",
        ["organization_id", "is_active"],
    )
    op.create_index(
        "uq_budget_policies_workspace_window",
        "budget_policies",
        ["organization_id", "window_kind"],
        unique=True,
        postgresql_where=sa.text("workflow_id IS NULL AND is_active"),
    )
    op.create_index(
        "uq_budget_policies_agent_window",
        "budget_policies",
        ["organization_id", "workflow_id", "window_kind"],
        unique=True,
        postgresql_where=sa.text("workflow_id IS NOT NULL AND is_active"),
    )

    op.create_table(
        "budget_incidents",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "organization_id",
            sa.Integer(),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "policy_id",
            sa.Integer(),
            sa.ForeignKey("budget_policies.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "workflow_id",
            sa.Integer(),
            sa.ForeignKey("workflows.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("threshold", sa.String(length=8), nullable=False),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("window_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("amount_limit_paise", sa.BigInteger(), nullable=False),
        sa.Column("amount_observed_paise", sa.BigInteger(), nullable=False),
        sa.Column(
            "status", sa.String(length=16), nullable=False, server_default="open"
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_budget_incidents_id", "budget_incidents", ["id"])
    op.create_index(
        "ix_budget_incidents_org_status",
        "budget_incidents",
        ["organization_id", "status"],
    )
    op.create_index(
        "uq_budget_incidents_policy_window_threshold",
        "budget_incidents",
        ["policy_id", "window_start", "threshold"],
        unique=True,
        postgresql_where=sa.text("status <> 'dismissed'"),
    )


def downgrade() -> None:
    op.drop_table("budget_incidents")
    op.drop_table("budget_policies")
    op.drop_index(
        "ix_credit_ledger_usage_org_workflow_created", table_name="credit_ledger"
    )
    op.drop_column("credit_ledger", "workflow_id")
