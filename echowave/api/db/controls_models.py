"""Tables for launch stream `controls` (LAUNCH-PLAN.md, phase 1).

Kept out of ``models.py`` (launch convention, KAN-276); ``models.py`` imports
this module at its end so the tables are on ``Base.metadata`` for alembic and
the tests. The two existing tables this stream extends -- ``agent_tasks``
(the task ledger) and ``organizations`` (personal space) -- keep their new
columns in ``models.py`` beside the old ones.

See ``CONTROLS.md`` at the repository's ``echowave/`` root for the design.
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
    text,
)

from api.db.models import Base


class OperationalUsageModel(Base):
    """How much of one daily allowance one person has used on one day.

    One row per (person, kind, UTC day), incremented in a single conditional
    ``INSERT ... ON CONFLICT DO UPDATE`` so two requests at once cannot both
    read "one left" and both go through (services/quotas.py).
    """

    __tablename__ = "operational_usage"

    id = Column(Integer, primary_key=True)
    user_id = Column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    #: ``model_turns`` | ``voice_minutes`` | ``outbound_messages`` |
    #: ``browser_minutes``. A string, so a fifth needs no migration.
    kind = Column(String(32), nullable=False)
    day = Column(Date, nullable=False)
    used = Column(Integer, nullable=False, default=0, server_default=text("0"))
    updated_at = Column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )

    __table_args__ = (
        UniqueConstraint("user_id", "kind", "day", name="uq_operational_usage_day"),
    )


class QuotaAllowanceModel(Base):
    """Extra allowance staff granted one person for a while, with a reason.

    Never open-ended: ``expires_at`` is required, and an expired or revoked
    row adds nothing. Every grant and revocation also writes the staff audit
    log (``admin_action_log``).
    """

    __tablename__ = "quota_allowances"

    id = Column(Integer, primary_key=True)
    user_id = Column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    kind = Column(String(32), nullable=False)
    #: Added to the daily limit on every day the grant is live.
    extra = Column(Integer, nullable=False)
    reason = Column(String(500), nullable=False)
    granted_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
    expires_at = Column(DateTime(timezone=True), nullable=False)
    revoked_at = Column(DateTime(timezone=True), nullable=True)
    revoked_by = Column(Integer, ForeignKey("users.id"), nullable=True)


class AgentTaskTransitionModel(Base):
    """One move of a task between ledger states, in order.

    ``sequence`` is the task's ``state_version`` after the move, so the rows
    of one task are numbered 1, 2, 3 with no gaps; the unique constraint is
    what makes two writers racing for the same step lose one of them.
    """

    __tablename__ = "agent_task_transitions"

    id = Column(Integer, primary_key=True)
    organization_id = Column(
        Integer, ForeignKey("organizations.id"), nullable=False, index=True
    )
    task_id = Column(
        Integer, ForeignKey("agent_tasks.id", ondelete="CASCADE"), nullable=False
    )
    sequence = Column(Integer, nullable=False)
    from_state = Column(String(24), nullable=True)
    to_state = Column(String(24), nullable=False)
    actor_user_id = Column(Integer, nullable=True)
    #: A normalized code (``approved``, ``timeout``), never free text.
    reason_code = Column(String(64), nullable=True)
    occurred_at = Column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )

    __table_args__ = (
        UniqueConstraint("task_id", "sequence", name="uq_agent_task_transition_seq"),
    )


class MemberPreferencesModel(Base):
    """A person's own preferences: theirs in every workspace they join.

    Deliberately not ``organization_configurations``: a workspace's timezone
    runs its schedules, and one member choosing their own must never move a
    team's routine (handoff 30). ``revision`` is bumped on every save; a save
    that names an older one is a conflict, not an overwrite.
    """

    __tablename__ = "member_preferences"

    user_id = Column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    #: A BCP 47 tag from services/member_preferences.LANGUAGES.
    language = Column(String(16), nullable=True)
    #: An IANA zone name.
    timezone = Column(String(64), nullable=True)
    #: A voice id; which voices exist is the voice stream's catalogue.
    voice = Column(String(64), nullable=True)
    #: ``HH:MM`` local time for the daily summary, or NULL for none.
    summary_time = Column(String(5), nullable=True)
    revision = Column(Integer, nullable=False, default=0, server_default=text("0"))
    updated_at = Column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )


class AnalyticsOutboxModel(Base):
    """One catalogue event, written beside the change it describes and sent
    to analytics afterwards (handoff 35, "transactional outbox").

    ``event_id`` is the envelope's id and the primary key, so writing the
    same event twice is a no-op, and the dispatcher passes it to PostHog as
    the event's uuid so a resend after a crash is deduplicated there too.
    """

    __tablename__ = "analytics_outbox"

    event_id = Column(String(36), primary_key=True)
    name = Column(String(64), nullable=False)
    envelope = Column(JSON, nullable=False)
    occurred_at = Column(DateTime(timezone=True), nullable=False)
    created_at = Column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
    delivered_at = Column(DateTime(timezone=True), nullable=True)
    attempts = Column(Integer, nullable=False, default=0, server_default=text("0"))
    #: A normalized code for the last failed send, never the provider's text.
    last_error = Column(String(64), nullable=True)

    __table_args__ = (
        Index(
            "ix_analytics_outbox_pending",
            "created_at",
            postgresql_where=text("delivered_at IS NULL"),
        ),
    )


class OutputFeedbackModel(Base):
    """ "Was this useful?" on one of Decibyl's replies or one finished task.

    Stored against what was judged -- the output's version (a hash of the
    words shown), the model that wrote it, and for a task the ledger version
    -- so an answer later edited or rerun is not credited with feedback that
    was about something else. One row per person per output; answering again
    replaces it.
    """

    __tablename__ = "output_feedback"

    id = Column(Integer, primary_key=True)
    organization_id = Column(
        Integer, ForeignKey("organizations.id"), nullable=False, index=True
    )
    user_id = Column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    #: ``reply`` (an agent_events row) | ``task`` (an agent_tasks row).
    subject_kind = Column(String(8), nullable=False)
    subject_id = Column(Integer, nullable=False)
    #: ``yes`` | ``not_quite``.
    verdict = Column(String(12), nullable=False)
    #: Codes from services/feedback.REASONS, never free text.
    reasons = Column(JSON, nullable=False, default=list)
    output_version = Column(String(64), nullable=False)
    model = Column(String(128), nullable=True)
    task_version = Column(Integer, nullable=True)
    created_at = Column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
    updated_at = Column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )

    __table_args__ = (
        UniqueConstraint(
            "user_id", "subject_kind", "subject_id", name="uq_output_feedback_once"
        ),
    )
