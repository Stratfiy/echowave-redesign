"""Launch stream controls: quotas, task ledger, personal space, preferences,
event outbox and feedback

Additive only. Every new column is nullable or has a server default, so the
code before this revision runs unchanged against the upgraded schema, and
nothing is backfilled: a personal space is created the first time a person
opens it (services/personal_space.py), and a task the ledger never touched
reads its state from ``status`` as before.

The one piece of logic in the database is ``personal_space_one_member``: a
trigger that refuses any membership in a personal space other than its
owner's. It is there rather than in Python because there are several paths
that add members (invitations, the team screen, the Stack Auth re-sync) and
a check in each would be one forgotten path away from a person's private
space having a second reader.

Downgrade drops the tables, the trigger and the columns; personal spaces
created meanwhile stay as ordinary organizations with one member.

Revision ID: 202610071800controls
Revises: 202610071200auto
"""

import sqlalchemy as sa
from alembic import op

revision = "202610071800controls"
down_revision = "202610071200auto"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- task ledger: extend agent_tasks ----------------------------------
    op.add_column(
        "agent_tasks", sa.Column("ledger_state", sa.String(24), nullable=True)
    )
    op.add_column(
        "agent_tasks",
        sa.Column(
            "state_version", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
    )
    op.add_column(
        "agent_tasks", sa.Column("idempotency_key", sa.String(128), nullable=True)
    )
    op.add_column(
        "agent_tasks", sa.Column("outcome_evidence", sa.JSON(), nullable=True)
    )
    op.add_column(
        "agent_tasks", sa.Column("approval_event_id", sa.Integer(), nullable=True)
    )
    op.add_column(
        "agent_tasks", sa.Column("payload_version", sa.String(64), nullable=True)
    )
    op.create_index(
        "uq_agent_tasks_org_idempotency_key",
        "agent_tasks",
        ["organization_id", "idempotency_key"],
        unique=True,
        postgresql_where=sa.text("idempotency_key IS NOT NULL"),
    )
    op.create_table(
        "agent_task_transitions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "organization_id",
            sa.Integer(),
            sa.ForeignKey("organizations.id"),
            nullable=False,
        ),
        sa.Column(
            "task_id",
            sa.Integer(),
            sa.ForeignKey("agent_tasks.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("from_state", sa.String(24), nullable=True),
        sa.Column("to_state", sa.String(24), nullable=False),
        sa.Column("actor_user_id", sa.Integer(), nullable=True),
        sa.Column("reason_code", sa.String(64), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("task_id", "sequence", name="uq_agent_task_transition_seq"),
    )
    op.create_index(
        "ix_agent_task_transitions_organization_id",
        "agent_task_transitions",
        ["organization_id"],
    )

    # --- personal space: extend organizations -----------------------------
    op.add_column("organizations", sa.Column("kind", sa.String(16), nullable=True))
    op.add_column(
        "organizations",
        sa.Column(
            "personal_owner_user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=True,
        ),
    )
    op.create_index(
        "uq_organizations_personal_owner",
        "organizations",
        ["personal_owner_user_id"],
        unique=True,
        postgresql_where=sa.text("personal_owner_user_id IS NOT NULL"),
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION personal_space_one_member() RETURNS trigger AS $$
        DECLARE
            owner_id integer;
            space_kind varchar;
        BEGIN
            SELECT kind, personal_owner_user_id INTO space_kind, owner_id
            FROM organizations WHERE id = NEW.organization_id;
            IF space_kind = 'personal' AND NEW.user_id IS DISTINCT FROM owner_id THEN
                RAISE EXCEPTION 'personal space % has one member', NEW.organization_id
                    USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER personal_space_one_member
        BEFORE INSERT OR UPDATE ON organization_memberships
        FOR EACH ROW EXECUTE FUNCTION personal_space_one_member();
        """
    )

    # --- operational quotas -----------------------------------------------
    op.create_table(
        "operational_usage",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("used", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("user_id", "kind", "day", name="uq_operational_usage_day"),
    )
    op.create_table(
        "quota_allowances",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("extra", sa.Integer(), nullable=False),
        sa.Column("reason", sa.String(500), nullable=False),
        sa.Column(
            "granted_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=False
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
    )
    op.create_index("ix_quota_allowances_user_id", "quota_allowances", ["user_id"])

    # --- member preferences -----------------------------------------------
    op.create_table(
        "member_preferences",
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("language", sa.String(16), nullable=True),
        sa.Column("timezone", sa.String(64), nullable=True),
        sa.Column("voice", sa.String(64), nullable=True),
        sa.Column("summary_time", sa.String(5), nullable=True),
        sa.Column(
            "revision", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )

    # --- analytics outbox -------------------------------------------------
    op.create_table(
        "analytics_outbox",
        sa.Column("event_id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("envelope", sa.JSON(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "attempts", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column("last_error", sa.String(64), nullable=True),
    )
    op.create_index(
        "ix_analytics_outbox_pending",
        "analytics_outbox",
        ["created_at"],
        postgresql_where=sa.text("delivered_at IS NULL"),
    )

    # --- feedback ---------------------------------------------------------
    op.create_table(
        "output_feedback",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "organization_id",
            sa.Integer(),
            sa.ForeignKey("organizations.id"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("subject_kind", sa.String(8), nullable=False),
        sa.Column("subject_id", sa.Integer(), nullable=False),
        sa.Column("verdict", sa.String(12), nullable=False),
        sa.Column("reasons", sa.JSON(), nullable=False),
        sa.Column("output_version", sa.String(64), nullable=False),
        sa.Column("model", sa.String(128), nullable=True),
        sa.Column("task_version", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "user_id", "subject_kind", "subject_id", name="uq_output_feedback_once"
        ),
    )
    op.create_index(
        "ix_output_feedback_organization_id", "output_feedback", ["organization_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_output_feedback_organization_id", table_name="output_feedback")
    op.drop_table("output_feedback")
    op.drop_index("ix_analytics_outbox_pending", table_name="analytics_outbox")
    op.drop_table("analytics_outbox")
    op.drop_table("member_preferences")
    op.drop_index("ix_quota_allowances_user_id", table_name="quota_allowances")
    op.drop_table("quota_allowances")
    op.drop_table("operational_usage")
    op.execute(
        "DROP TRIGGER IF EXISTS personal_space_one_member ON organization_memberships"
    )
    op.execute("DROP FUNCTION IF EXISTS personal_space_one_member()")
    op.drop_index("uq_organizations_personal_owner", table_name="organizations")
    op.drop_column("organizations", "personal_owner_user_id")
    op.drop_column("organizations", "kind")
    op.drop_index(
        "ix_agent_task_transitions_organization_id",
        table_name="agent_task_transitions",
    )
    op.drop_table("agent_task_transitions")
    op.drop_index("uq_agent_tasks_org_idempotency_key", table_name="agent_tasks")
    for column in (
        "payload_version",
        "approval_event_id",
        "outcome_evidence",
        "idempotency_key",
        "state_version",
        "ledger_state",
    ):
        op.drop_column("agent_tasks", column)
