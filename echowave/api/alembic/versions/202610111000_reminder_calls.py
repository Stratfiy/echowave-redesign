"""Reminder calls: numbers, schedules, occurrences and dispatches

Additive only (docs/plans/reminder-calls.md, section 7).

* ``reminder_call_numbers`` -- the number a person confirmed for reminder
  calls, with the "I am 18 or over" confirmation (decision D4).
* ``reminder_call_schedules`` -- one per confirmed reminder card.
* ``reminder_call_occurrences`` -- one per due time (task state), unique by
  ``occurrence_key``.
* ``reminder_call_dispatches`` -- one per ring attempt (delivery state),
  unique per (occurrence, attempt).
* ``care_dose_calls.allowance_day`` -- the local day whose shared daily-cap
  slot a care call holds, where reminder calls share the cap with care
  (decision D3). NULL for every existing row, and while the feature is off.

Nothing is read or written by these until ``REMINDER_CALLS_ENABLED`` is on.
Downgrading drops the four tables and the column.

Revision ID: 20261011remindercalls
Revises: 20261010calloutcomes
"""

import sqlalchemy as sa
from alembic import op

revision = "20261011remindercalls"
down_revision = "20261010calloutcomes"
branch_labels = None
depends_on = None


def _org() -> sa.Column:
    return sa.Column(
        "organization_id",
        sa.Integer(),
        sa.ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
    )


def _user() -> sa.Column:
    return sa.Column(
        "user_id",
        sa.Integer(),
        sa.ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )


def upgrade() -> None:
    op.create_table(
        "reminder_call_numbers",
        sa.Column("id", sa.Integer(), primary_key=True),
        _org(),
        _user(),
        sa.Column("phone", sa.String(20), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("adult_confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("card_event_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "organization_id", "user_id", name="uq_reminder_call_number_person"
        ),
    )
    op.create_table(
        "reminder_call_schedules",
        sa.Column("id", sa.Integer(), primary_key=True),
        _org(),
        _user(),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("language", sa.String(8), nullable=False),
        sa.Column("phone", sa.String(20), nullable=False),
        sa.Column("timezone", sa.String(64), nullable=False),
        sa.Column("local_time", sa.String(5), nullable=False),
        sa.Column("recurrence", sa.String(16), nullable=False),
        sa.Column("weekday", sa.Integer(), nullable=True),
        sa.Column("date", sa.Date(), nullable=True),
        sa.Column("retry_policy", sa.JSON(), nullable=True),
        sa.Column("fallback", sa.String(16), nullable=False),
        sa.Column("quiet_exception", sa.JSON(), nullable=True),
        sa.Column("card_event_id", sa.Integer(), nullable=True),
        sa.Column("thread_id", sa.String(64), nullable=True),
        sa.Column("version", sa.String(32), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("next_due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_reminder_call_schedules_due",
        "reminder_call_schedules",
        ["state", "next_due_at"],
    )
    op.create_index(
        "ix_reminder_call_schedules_person",
        "reminder_call_schedules",
        ["organization_id", "user_id"],
    )
    op.create_table(
        "reminder_call_occurrences",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "schedule_id",
            sa.Integer(),
            sa.ForeignKey("reminder_call_schedules.id", ondelete="CASCADE"),
            nullable=False,
        ),
        _org(),
        _user(),
        sa.Column("schedule_version", sa.String(32), nullable=False),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("occurrence_key", sa.String(160), nullable=False, unique=True),
        sa.Column("task_state", sa.String(24), nullable=False),
        sa.Column("snoozed_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_reminder_call_occurrences_schedule",
        "reminder_call_occurrences",
        ["schedule_id", "due_at"],
    )
    op.create_table(
        "reminder_call_dispatches",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "occurrence_id",
            sa.Integer(),
            sa.ForeignKey("reminder_call_occurrences.id", ondelete="CASCADE"),
            nullable=False,
        ),
        _org(),
        _user(),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("reason", sa.String(32), nullable=True),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("workflow_run_id", sa.Integer(), nullable=True),
        sa.Column("provider", sa.String(32), nullable=True),
        sa.Column("provider_call_id", sa.String(128), nullable=True),
        sa.Column("outcome_history", sa.JSON(), nullable=True),
        sa.Column("allowance_day", sa.Date(), nullable=True),
        sa.Column("notified", sa.JSON(), nullable=True),
        sa.Column("reserved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("dialled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("settled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "occurrence_id", "attempt", name="uq_reminder_call_attempt"
        ),
    )
    op.create_index(
        "ix_reminder_call_dispatches_due",
        "reminder_call_dispatches",
        ["state", "due_at"],
    )
    op.add_column(
        "care_dose_calls", sa.Column("allowance_day", sa.Date(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("care_dose_calls", "allowance_day")
    op.drop_index(
        "ix_reminder_call_dispatches_due", table_name="reminder_call_dispatches"
    )
    op.drop_table("reminder_call_dispatches")
    op.drop_index(
        "ix_reminder_call_occurrences_schedule",
        table_name="reminder_call_occurrences",
    )
    op.drop_table("reminder_call_occurrences")
    op.drop_index(
        "ix_reminder_call_schedules_person", table_name="reminder_call_schedules"
    )
    op.drop_index(
        "ix_reminder_call_schedules_due", table_name="reminder_call_schedules"
    )
    op.drop_table("reminder_call_schedules")
    op.drop_table("reminder_call_numbers")
