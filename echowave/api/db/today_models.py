"""Tables for launch stream `today` (LAUNCH-PLAN.md, phase 2).

Kept out of ``models.py`` (launch convention, KAN-276); ``models.py`` imports
this module at its end so the tables are on ``Base.metadata`` for alembic and
the tests. See ``TODAY.md`` at the repository's ``echowave/`` root.

Every row here belongs to one person in one workspace: ``organization_id``
and ``user_id`` are both on every table and every read filters by both, so a
colleague's reminders, briefs and deliveries are never in anybody else's
Today -- and a person's personal space (an organisation of its own) keeps
them apart from the workspaces they join by the same rule.
"""

from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)

from api.db.models import Base


def _now() -> datetime:
    return datetime.now(UTC)


class TodayEventModel(Base):
    """Something happening at a confirmed time: an appointment, a meeting.

    Kept apart from the reminders about it (handoff 22: "separate event time
    from reminder time"), so moving the event can recalculate every linked
    reminder by its approved offset, and cancelling it cancels them.
    """

    __tablename__ = "today_events"

    id = Column(Integer, primary_key=True)
    organization_id = Column(
        Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    user_id = Column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    title = Column(String(200), nullable=False)
    #: The confirmed moment, in UTC. Never invented from vague notes.
    starts_at = Column(DateTime(timezone=True), nullable=False)
    #: The IANA zone the person confirmed the time in.
    timezone = Column(String(64), nullable=False)
    #: ``active`` or ``cancelled``.
    status = Column(String(16), nullable=False, default="active")
    #: Bumped on every change; a save naming an older one is a conflict.
    revision = Column(Integer, nullable=False, default=0, server_default=text("0"))
    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=_now)

    __table_args__ = (
        Index("ix_today_events_owner", "organization_id", "user_id", "starts_at"),
    )


class TodayReminderModel(Base):
    """One reminder: at a time, on a recurrence, or at an offset from an event.

    ``remind_at`` is always the next occurrence in UTC, so the delivery tick
    asks one indexed question. Event-linked reminders carry ``offset_minutes``
    (0 = at event time, -1440 = one day before) and are recalculated when the
    event moves; ``history`` keeps the old and new times so the editor can show
    what changed.
    """

    __tablename__ = "today_reminders"

    id = Column(Integer, primary_key=True)
    organization_id = Column(
        Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    user_id = Column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    title = Column(String(200), nullable=False)
    note = Column(Text, nullable=False, default="")
    event_id = Column(
        Integer, ForeignKey("today_events.id", ondelete="SET NULL"), nullable=True
    )
    offset_minutes = Column(Integer, nullable=True)
    #: ``once``, ``daily``, ``weekdays`` or ``weekly``.
    recurrence = Column(String(16), nullable=False, default="once")
    #: ``HH:MM`` local time, for a recurring reminder.
    local_time = Column(String(5), nullable=True)
    #: 0 = Monday, for weekly.
    weekday = Column(Integer, nullable=True)
    timezone = Column(String(64), nullable=False)
    #: The next occurrence, UTC. NULL once there is none.
    remind_at = Column(DateTime(timezone=True), nullable=True)
    #: ``in_app``, ``whatsapp`` or ``push``.
    channel = Column(String(16), nullable=False, default="in_app")
    #: ``active``, ``paused``, ``done``, ``missed`` or ``cancelled``.
    status = Column(String(16), nullable=False, default="active")
    revision = Column(Integer, nullable=False, default=0, server_default=text("0"))
    history = Column(JSON, nullable=False, default=list)
    last_delivered_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=_now)

    __table_args__ = (
        Index("ix_today_reminders_owner", "organization_id", "user_id"),
        # The tick's only question: what active reminder is due.
        Index(
            "ix_today_reminders_due",
            "remind_at",
            postgresql_where=text("status = 'active'"),
        ),
        Index("ix_today_reminders_event", "event_id"),
    )


class TodayDeliveryModel(Base):
    """One attempt to put one occurrence of something in front of a person.

    Unique per (what, which occurrence, channel): a tick that runs twice, a
    worker that restarts mid-delivery, or a second tab pressing Test cannot
    deliver the same occurrence twice on the same channel. ``status`` is
    honest: ``sent`` only with evidence, ``accepted`` when a provider took it
    without confirming delivery, ``needs_setup`` when the channel cannot be
    used yet, ``skipped`` with the reason, ``failed`` with the reason.
    """

    __tablename__ = "today_deliveries"

    id = Column(Integer, primary_key=True)
    organization_id = Column(
        Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    user_id = Column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    #: ``reminder``, ``brief`` or ``end_of_day``.
    subject_kind = Column(String(16), nullable=False)
    subject_id = Column(Integer, nullable=False)
    #: The occurrence: a UTC instant for a reminder, a local date for a
    #: brief, ``test:...`` for a labelled test delivery.
    occurrence_key = Column(String(64), nullable=False)
    channel = Column(String(16), nullable=False)
    is_test = Column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    status = Column(String(16), nullable=False, default="queued")
    reason_code = Column(String(48), nullable=True)
    detail = Column(String(300), nullable=True)
    evidence = Column(String(200), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)
    delivered_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        UniqueConstraint(
            "subject_kind",
            "subject_id",
            "occurrence_key",
            "channel",
            name="uq_today_delivery_occurrence",
        ),
        Index("ix_today_deliveries_owner", "organization_id", "user_id", "created_at"),
    )


class DailyBriefSettingsModel(Base):
    """A person's daily brief in one workspace: off until they accept it.

    One row per (person, workspace) -- the brief reads the workspace's
    approvals and tasks, so a brief for a business and one for a personal
    space are two choices. ``revision`` makes a stale save a conflict.
    """

    __tablename__ = "daily_brief_settings"

    id = Column(Integer, primary_key=True)
    organization_id = Column(
        Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    user_id = Column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    enabled = Column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    paused = Column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    local_time = Column(String(5), nullable=False, default="09:00")
    timezone = Column(String(64), nullable=False)
    #: Weekdays, 0 = Monday.
    days = Column(JSON, nullable=False, default=lambda: [0, 1, 2, 3, 4, 5, 6])
    channels = Column(JSON, nullable=False, default=lambda: ["in_app"])
    #: Quiet hours for optional suggestions, local ``HH:MM``.
    quiet_start = Column(String(5), nullable=False, default="21:00")
    quiet_end = Column(String(5), nullable=False, default="08:00")
    end_of_day_enabled = Column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    end_of_day_time = Column(String(5), nullable=False, default="18:00")
    revision = Column(Integer, nullable=False, default=0, server_default=text("0"))
    updated_at = Column(DateTime(timezone=True), nullable=False, default=_now)

    __table_args__ = (
        UniqueConstraint(
            "organization_id", "user_id", name="uq_daily_brief_settings_owner"
        ),
    )


class DailyBriefModel(Base):
    """One brief (or end-of-day note) for one person, one local day.

    Unique per (person, workspace, kind, day): Refresh updates the day's
    brief rather than writing another, and the scheduled run claims
    ``delivered_at`` once, so one saved schedule is one brief occurrence.
    """

    __tablename__ = "daily_briefs"

    id = Column(Integer, primary_key=True)
    organization_id = Column(
        Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    user_id = Column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    #: ``brief`` or ``end_of_day``.
    kind = Column(String(16), nullable=False, default="brief")
    #: The local date, ``YYYY-MM-DD``.
    occurrence_key = Column(String(16), nullable=False)
    timezone = Column(String(64), nullable=False)
    period_start = Column(DateTime(timezone=True), nullable=False)
    period_end = Column(DateTime(timezone=True), nullable=False)
    refreshed_at = Column(DateTime(timezone=True), nullable=False, default=_now)
    #: ``complete``, ``partial`` or ``failed``.
    status = Column(String(16), nullable=False)
    summary = Column(Text, nullable=False, default="")
    sources = Column(JSON, nullable=False, default=list)
    sections = Column(JSON, nullable=False, default=dict)
    delivered_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)

    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "user_id",
            "kind",
            "occurrence_key",
            name="uq_daily_brief_occurrence",
        ),
    )


class TodayDismissalModel(Base):
    """A suggestion a person dismissed (or asked to see later)."""

    __tablename__ = "today_dismissals"

    id = Column(Integer, primary_key=True)
    organization_id = Column(
        Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    user_id = Column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    suggestion_key = Column(String(96), nullable=False)
    #: NULL for good; a time for "later".
    until = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)

    __table_args__ = (
        UniqueConstraint(
            "organization_id", "user_id", "suggestion_key", name="uq_today_dismissal"
        ),
    )
