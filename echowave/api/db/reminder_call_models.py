"""Tables for reminder calls (services/reminder_calls).

The contract is docs/plans/reminder-calls.md (section 7). Four tables, each
scoped by ``organization_id`` and read only through it:

* ``reminder_call_numbers`` -- the number a person confirmed, on a card,
  for reminder calls, with the "I am 18 or over" confirmation that card
  carries (decision D4). Theirs, in that workspace.
* ``reminder_call_schedules`` -- one per confirmed reminder card: what to
  say (the person's own words), when (local time, zone, recurrence), on
  which number, and the card version that approved it. ``next_due_at`` is
  advanced only by a compare-and-swap from the value the tick read.
* ``reminder_call_occurrences`` -- one per due time, unique by
  ``occurrence_key``. Holds the **task** state (open, snoozed,
  user_reported_done, cancelled): did the person deal with it.
* ``reminder_call_dispatches`` -- one per ring attempt, unique per
  (occurrence, attempt). Holds the **delivery** state (queued,
  dispatching, accepted, answered, no_answer, failed, unknown, skipped):
  did the call happen. Delivery never moves the task.

Kept out of ``models.py`` (launch convention); ``models.py`` imports this
module at its end so the tables are on ``Base.metadata``.
"""

from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)

from api.db.models import Base


def _now() -> datetime:
    return datetime.now(UTC)


class ReminderCallNumberModel(Base):
    """The number a person confirmed for reminder calls, on a card, once,
    with the adult confirmation (``adult_confirmed_at``) the card requires."""

    __tablename__ = "reminder_call_numbers"

    id = Column(Integer, primary_key=True)
    organization_id = Column(
        Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    user_id = Column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    #: E.164, as ``dnd.to_dialable`` writes it.
    phone = Column(String(20), nullable=False)
    confirmed_at = Column(DateTime(timezone=True), nullable=True)
    #: When the person ticked "I am 18 or over" on the number card. NULL:
    #: never rung (the gate refuses ``not_adult``).
    adult_confirmed_at = Column(DateTime(timezone=True), nullable=True)
    card_event_id = Column(Integer, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)

    __table_args__ = (
        UniqueConstraint(
            "organization_id", "user_id", name="uq_reminder_call_number_person"
        ),
    )


class ReminderCallScheduleModel(Base):
    """One confirmed reminder card. ``state``: ``active`` | ``cancelled``.
    ``next_due_at`` NULL means no occurrence is left to make (a one-off
    whose time has come). ``version`` is the card version the person
    confirmed; an occurrence made under another version is not rung."""

    __tablename__ = "reminder_call_schedules"

    id = Column(Integer, primary_key=True)
    organization_id = Column(
        Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    user_id = Column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    #: The person's own words, read out as written.
    title = Column(String(200), nullable=False)
    language = Column(String(8), nullable=False, default="en")
    phone = Column(String(20), nullable=False)
    timezone = Column(String(64), nullable=False)
    #: "HH:MM", local.
    local_time = Column(String(5), nullable=False)
    #: once | daily | weekdays | weekly
    recurrence = Column(String(16), nullable=False, default="once")
    weekday = Column(Integer, nullable=True)
    #: The local date of a one-off.
    date = Column(Date, nullable=True)
    #: ``{"max_retries", "gap_minutes"}`` as the card showed it.
    retry_policy = Column(JSON, nullable=True)
    #: What reaches the person when no call does: "push".
    fallback = Column(String(16), nullable=False, default="push")
    #: An approved exception to quiet hours. Always NULL while decision D2
    #: allows none for general reminders.
    quiet_exception = Column(JSON, nullable=True)
    card_event_id = Column(Integer, nullable=True)
    thread_id = Column(String(64), nullable=True)
    version = Column(String(32), nullable=False)
    state = Column(String(16), nullable=False, default="active")
    next_due_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=_now)

    __table_args__ = (
        Index("ix_reminder_call_schedules_due", "state", "next_due_at"),
        Index("ix_reminder_call_schedules_person", "organization_id", "user_id"),
    )


class ReminderCallOccurrenceModel(Base):
    """One due time of one schedule: the task side."""

    __tablename__ = "reminder_call_occurrences"

    id = Column(Integer, primary_key=True)
    schedule_id = Column(
        Integer,
        ForeignKey("reminder_call_schedules.id", ondelete="CASCADE"),
        nullable=False,
    )
    organization_id = Column(
        Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    user_id = Column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    schedule_version = Column(String(32), nullable=False)
    due_at = Column(DateTime(timezone=True), nullable=False)
    #: schedule id : version : local due ISO. Two ticks make one row.
    occurrence_key = Column(String(160), nullable=False, unique=True)
    #: open | snoozed | user_reported_done | cancelled
    task_state = Column(String(24), nullable=False, default="open")
    snoozed_until = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=_now)

    __table_args__ = (
        Index("ix_reminder_call_occurrences_schedule", "schedule_id", "due_at"),
    )


class ReminderCallDispatchModel(Base):
    """One ring attempt: the delivery side.

    ``state``: ``queued`` -> ``dispatching`` (claimed; the gate runs; the
    run id is written before the provider is asked) -> ``accepted`` ->
    ``answered`` | ``no_answer`` | ``failed``; or ``unknown`` (it may have
    rung, nothing proves what happened: never re-dialled, reconciled); or
    ``skipped`` with ``reason`` (the gate said no, or it was never dialled).
    ``allowance_day`` is the person's local day whose daily-cap slot this
    attempt holds (``call_when_done.allowance``)."""

    __tablename__ = "reminder_call_dispatches"

    id = Column(Integer, primary_key=True)
    occurrence_id = Column(
        Integer,
        ForeignKey("reminder_call_occurrences.id", ondelete="CASCADE"),
        nullable=False,
    )
    organization_id = Column(
        Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    user_id = Column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    attempt = Column(Integer, nullable=False, default=1)
    state = Column(String(16), nullable=False, default="queued")
    reason = Column(String(32), nullable=True)
    #: When this attempt may ring.
    due_at = Column(DateTime(timezone=True), nullable=False)
    workflow_run_id = Column(Integer, nullable=True)
    provider = Column(String(32), nullable=True)
    provider_call_id = Column(String(128), nullable=True)
    #: ``[{"at", "from", "to", "reason", "source"}]``, appended only.
    outcome_history = Column(JSON, nullable=True)
    allowance_day = Column(Date, nullable=True)
    #: What the notification fallback reported, per channel.
    notified = Column(JSON, nullable=True)
    reserved_at = Column(DateTime(timezone=True), nullable=True)
    dialled_at = Column(DateTime(timezone=True), nullable=True)
    settled_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)

    __table_args__ = (
        UniqueConstraint("occurrence_id", "attempt", name="uq_reminder_call_attempt"),
        Index("ix_reminder_call_dispatches_due", "state", "due_at"),
    )
