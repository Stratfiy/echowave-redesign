"""Escalation v2: one record per handover, and each call's escalation outcome

Additive only: two tables (services/escalation). ``escalations`` is the
idempotent state machine for one handover of a caller to a person;
``call_escalation_outcomes`` is one row per call that ran with the policy on,
for reporting. Written only while ``escalation_v2`` is on, so with the flag
off both stay empty.

Downgrading drops both tables.

Revision ID: 20261011escalations
Revises: 20261010images
"""

import sqlalchemy as sa
from alembic import op

revision = "20261011escalations"
down_revision = "20261010images"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "escalations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("escalation_uuid", sa.String(36), nullable=False, unique=True),
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
        sa.Column("idempotency_key", sa.String(128), nullable=False, unique=True),
        sa.Column("state", sa.String(16), nullable=False, server_default="requested"),
        sa.Column("failure_reason", sa.String(40), nullable=True),
        sa.Column("reason_code", sa.String(24), nullable=False),
        sa.Column("reason_detail", sa.String(200), nullable=True),
        sa.Column("trigger", sa.String(8), nullable=False, server_default="auto"),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("attempts", sa.JSON(), nullable=False),
        sa.Column("current_transfer_id", sa.String(64), nullable=True),
        sa.Column("human_call_id", sa.String(128), nullable=True),
        sa.Column("fallback", sa.String(16), nullable=True),
        sa.Column("handoff_card", sa.JSON(), nullable=True),
        sa.Column("human_response", sa.String(16), nullable=True),
        sa.Column(
            "human_user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("outcome_note", sa.Text(), nullable=True),
        sa.Column("timeline_event_id", sa.Integer(), nullable=True),
        sa.Column("time_to_human_ms", sa.Integer(), nullable=True),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("bridged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("handed_back_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_escalations_org_requested",
        "escalations",
        ["organization_id", "requested_at"],
    )
    op.create_index("ix_escalations_run", "escalations", ["workflow_run_id"])
    op.create_index("ix_escalations_transfer", "escalations", ["current_transfer_id"])

    op.create_table(
        "call_escalation_outcomes",
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
        sa.Column("workflow_run_id", sa.Integer(), nullable=False, unique=True),
        sa.Column("outcome", sa.String(24), nullable=False),
        sa.Column("reason_code", sa.String(24), nullable=True),
        sa.Column("transfer_result", sa.String(16), nullable=True),
        sa.Column("failure_reason", sa.String(40), nullable=True),
        sa.Column("fallback", sa.String(16), nullable=True),
        sa.Column("time_to_human_ms", sa.Integer(), nullable=True),
        sa.Column(
            "escalation_id",
            sa.Integer(),
            sa.ForeignKey("escalations.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_call_escalation_outcomes_org_recorded",
        "call_escalation_outcomes",
        ["organization_id", "recorded_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_call_escalation_outcomes_org_recorded",
        table_name="call_escalation_outcomes",
    )
    op.drop_table("call_escalation_outcomes")
    op.drop_index("ix_escalations_transfer", table_name="escalations")
    op.drop_index("ix_escalations_run", table_name="escalations")
    op.drop_index("ix_escalations_org_requested", table_name="escalations")
    op.drop_table("escalations")
