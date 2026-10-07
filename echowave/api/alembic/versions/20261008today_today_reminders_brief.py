"""Today: events, reminders, deliveries, the daily brief (launch stream `today`)

Six new tables and one nullable column on ``agent_routines``
(``armed_by_card_event_id``). Nothing existing changes shape or meaning, so
code before this revision runs unchanged against the upgraded schema, and a
downgrade drops only what this added.

Revision ID: 20261008today
Revises: 202610071500shell
"""

import sqlalchemy as sa
from alembic import op

revision = "20261008today"
down_revision = "202610071500shell"
branch_labels = None
depends_on = None


def _owner_columns() -> list[sa.Column]:
    return [
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
    ]


def upgrade() -> None:
    op.create_table(
        "today_events",
        *_owner_columns(),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("timezone", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column(
            "revision", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_today_events_owner",
        "today_events",
        ["organization_id", "user_id", "starts_at"],
    )

    op.create_table(
        "today_reminders",
        *_owner_columns(),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("note", sa.Text(), nullable=False),
        sa.Column(
            "event_id",
            sa.Integer(),
            sa.ForeignKey("today_events.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("offset_minutes", sa.Integer(), nullable=True),
        sa.Column("recurrence", sa.String(length=16), nullable=False),
        sa.Column("local_time", sa.String(length=5), nullable=True),
        sa.Column("weekday", sa.Integer(), nullable=True),
        sa.Column("timezone", sa.String(length=64), nullable=False),
        sa.Column("remind_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("channel", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column(
            "revision", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column("history", sa.JSON(), nullable=False),
        sa.Column("last_delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_today_reminders_owner", "today_reminders", ["organization_id", "user_id"]
    )
    op.create_index(
        "ix_today_reminders_due",
        "today_reminders",
        ["remind_at"],
        postgresql_where=sa.text("status = 'active'"),
    )
    op.create_index("ix_today_reminders_event", "today_reminders", ["event_id"])

    op.create_table(
        "today_deliveries",
        *_owner_columns(),
        sa.Column("subject_kind", sa.String(length=16), nullable=False),
        sa.Column("subject_id", sa.Integer(), nullable=False),
        sa.Column("occurrence_key", sa.String(length=64), nullable=False),
        sa.Column("channel", sa.String(length=16), nullable=False),
        sa.Column(
            "is_test", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("reason_code", sa.String(length=48), nullable=True),
        sa.Column("detail", sa.String(length=300), nullable=True),
        sa.Column("evidence", sa.String(length=200), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint(
            "subject_kind",
            "subject_id",
            "occurrence_key",
            "channel",
            name="uq_today_delivery_occurrence",
        ),
    )
    op.create_index(
        "ix_today_deliveries_owner",
        "today_deliveries",
        ["organization_id", "user_id", "created_at"],
    )

    op.create_table(
        "daily_brief_settings",
        *_owner_columns(),
        sa.Column(
            "enabled", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        sa.Column(
            "paused", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        sa.Column("local_time", sa.String(length=5), nullable=False),
        sa.Column("timezone", sa.String(length=64), nullable=False),
        sa.Column("days", sa.JSON(), nullable=False),
        sa.Column("channels", sa.JSON(), nullable=False),
        sa.Column("quiet_start", sa.String(length=5), nullable=False),
        sa.Column("quiet_end", sa.String(length=5), nullable=False),
        sa.Column(
            "end_of_day_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("end_of_day_time", sa.String(length=5), nullable=False),
        sa.Column(
            "revision", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "organization_id", "user_id", name="uq_daily_brief_settings_owner"
        ),
    )

    op.create_table(
        "daily_briefs",
        *_owner_columns(),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("occurrence_key", sa.String(length=16), nullable=False),
        sa.Column("timezone", sa.String(length=64), nullable=False),
        sa.Column("period_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("period_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("refreshed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("sources", sa.JSON(), nullable=False),
        sa.Column("sections", sa.JSON(), nullable=False),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "organization_id",
            "user_id",
            "kind",
            "occurrence_key",
            name="uq_daily_brief_occurrence",
        ),
    )

    op.create_table(
        "today_dismissals",
        *_owner_columns(),
        sa.Column("suggestion_key", sa.String(length=96), nullable=False),
        sa.Column("until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "organization_id", "user_id", "suggestion_key", name="uq_today_dismissal"
        ),
    )

    op.add_column(
        "agent_routines",
        sa.Column("armed_by_card_event_id", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("agent_routines", "armed_by_card_event_id")
    op.drop_table("today_dismissals")
    op.drop_table("daily_briefs")
    op.drop_table("daily_brief_settings")
    op.drop_index("ix_today_deliveries_owner", table_name="today_deliveries")
    op.drop_table("today_deliveries")
    op.drop_index("ix_today_reminders_event", table_name="today_reminders")
    op.drop_index("ix_today_reminders_due", table_name="today_reminders")
    op.drop_index("ix_today_reminders_owner", table_name="today_reminders")
    op.drop_table("today_reminders")
    op.drop_index("ix_today_events_owner", table_name="today_events")
    op.drop_table("today_events")
