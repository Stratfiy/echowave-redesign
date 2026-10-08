"""Live voice sessions, turn latency, appointments (launch stream `voice`)

Additive only. Four new tables and one new ``tool_category`` value; nothing existing
is rewritten and nothing is backfilled, so downgrading drops exactly what this
adds and every flag off leaves the schema unused.

* ``voice_sessions`` -- a person's live voice conversation with Decibyl, with
  a partial unique index that holds one live session per person.
* ``voice_turns`` -- per-turn latency (handoff 12): client-measured response
  and interruption times beside server stage durations, never subtracted
  across clocks.
* ``appointment_policies`` / ``appointments`` -- what the Call and Appointment
  helper may book, and what it booked.
* ``tool_category`` gains ``appointments``: the built-in tool a Call and
  Appointment agent books with.

Revision ID: 20261008voice
Revises: 20261008settings
"""

import sqlalchemy as sa
from alembic import op
from alembic_postgresql_enum import TableReference

revision = "20261008voice"
down_revision = "20261008settings"
branch_labels = None
depends_on = None

_TOOL_CATEGORIES = [
    "http_api",
    "end_call",
    "transfer_call",
    "calculator",
    "native",
    "integration",
    "mcp",
    "google_calendar",
    "rate_table",
    "composio",
    "web",
    "team_calls",
]
_WITH_APPOINTMENTS = [*_TOOL_CATEGORIES, "appointments"]
_TOOLS = [
    TableReference(table_schema="public", table_name="tools", column_name="category")
]


def upgrade() -> None:
    # --- voice sessions ------------------------------------------------------
    op.create_table(
        "voice_sessions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "organization_id",
            sa.Integer(),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("thread_id", sa.String(64), nullable=True),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("phase", sa.String(16), nullable=True),
        sa.Column(
            "state_version", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column(
            "muted", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        sa.Column("language", sa.String(16), nullable=True),
        sa.Column("voice", sa.String(64), nullable=True),
        sa.Column("config", sa.JSON(), nullable=False),
        sa.Column(
            "reconnects", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column("gaps", sa.JSON(), nullable=False),
        sa.Column("end_reason", sa.String(48), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("connected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_voice_sessions_org_user", "voice_sessions", ["organization_id", "user_id"]
    )
    op.create_index(
        "uq_voice_sessions_one_live",
        "voice_sessions",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text("state IN ('connecting', 'live', 'reconnecting')"),
    )

    # --- voice turns (latency) ----------------------------------------------
    op.create_table(
        "voice_turns",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "session_id",
            sa.Integer(),
            sa.ForeignKey("voice_sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "organization_id",
            sa.Integer(),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("turn_index", sa.Integer(), nullable=False),
        sa.Column("language", sa.String(16), nullable=True),
        sa.Column("channel", sa.String(16), nullable=False),
        sa.Column("response_ms", sa.Integer(), nullable=True),
        sa.Column("interruption_ms", sa.Integer(), nullable=True),
        sa.Column(
            "interrupted", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        sa.Column(
            "tool_turn", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        sa.Column("stages", sa.JSON(), nullable=False),
        sa.Column("stt_provider", sa.String(48), nullable=True),
        sa.Column("tts_provider", sa.String(48), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("session_id", "turn_index", name="uq_voice_turn_index"),
    )
    op.create_index("ix_voice_turns_created", "voice_turns", ["created_at"])

    # --- appointments ---------------------------------------------------------
    op.create_table(
        "appointment_policies",
        sa.Column(
            "organization_id",
            sa.Integer(),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("booking", sa.String(16), nullable=False),
        sa.Column("duration_minutes", sa.Integer(), nullable=False),
        sa.Column("lead_minutes", sa.Integer(), nullable=False),
        sa.Column("horizon_days", sa.Integer(), nullable=False),
        sa.Column("services", sa.JSON(), nullable=False),
        sa.Column("verification", sa.String(16), nullable=False),
        sa.Column("escalate_to", sa.String(32), nullable=True),
        sa.Column(
            "call_workflow_id",
            sa.Integer(),
            sa.ForeignKey("workflows.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "revision", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column(
            "updated_by",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "appointments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "organization_id",
            sa.Integer(),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("service", sa.String(120), nullable=True),
        sa.Column("caller_name", sa.String(120), nullable=True),
        sa.Column("caller_number", sa.String(32), nullable=True),
        sa.Column("reason", sa.String(500), nullable=True),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column(
            "workflow_run_id",
            sa.Integer(),
            sa.ForeignKey("workflow_runs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "booked_by_user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_appointments_org_start", "appointments", ["organization_id", "starts_at"]
    )

    # --- the appointments tool ---------------------------------------------
    op.sync_enum_values(
        enum_schema="public",
        enum_name="tool_category",
        new_values=_WITH_APPOINTMENTS,
        affected_columns=_TOOLS,
        enum_values_to_rename=[],
    )


def downgrade() -> None:
    op.execute("DELETE FROM tools WHERE category = 'appointments'")
    op.sync_enum_values(
        enum_schema="public",
        enum_name="tool_category",
        new_values=_TOOL_CATEGORIES,
        affected_columns=_TOOLS,
        enum_values_to_rename=[],
    )
    op.drop_index("ix_appointments_org_start", table_name="appointments")
    op.drop_table("appointments")
    op.drop_table("appointment_policies")
    op.drop_index("ix_voice_turns_created", table_name="voice_turns")
    op.drop_table("voice_turns")
    op.drop_index("uq_voice_sessions_one_live", table_name="voice_sessions")
    op.drop_index("ix_voice_sessions_org_user", table_name="voice_sessions")
    op.drop_table("voice_sessions")
