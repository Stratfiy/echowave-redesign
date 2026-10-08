"""Tables for launch stream `voice` (LAUNCH-PLAN.md, phase 2).

Kept out of ``models.py`` (launch convention, KAN-276); ``models.py`` imports
this module at its end so the tables are on ``Base.metadata`` for alembic and
the tests. The one existing table this stream extends, ``member_preferences``
(speed and captions beside the voice), keeps its new columns in
``controls_models.py`` beside the old ones.

See ``VOICE.md`` at the repository's ``echowave/`` root for the design.
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
    UniqueConstraint,
    text,
)

from api.db.models import Base


def _now() -> datetime:
    return datetime.now(UTC)


class VoiceSessionModel(Base):
    """One live voice conversation between a person and Decibyl (screen 05).

    A person's own: every read is by ``organization_id`` *and* ``user_id``, so
    a colleague cannot see that somebody talked, let alone what about. The
    configuration the session started with is copied into ``config`` and
    never changes for its life -- a model or voice changed in Settings
    applies to the next session, not mid-sentence (screen 05 acceptance).

    ``state_version`` is bumped on every move; a move that names an older
    one is stale and changes nothing (design "Event delivery").
    """

    __tablename__ = "voice_sessions"

    id = Column(Integer, primary_key=True)
    organization_id = Column(
        Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    user_id = Column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    #: Which of Decibyl's conversations the words land in; None is the
    #: original one, as on the text path.
    thread_id = Column(String(64), nullable=True)
    #: connecting, live, reconnecting, ended, failed.
    state = Column(String(16), nullable=False, default="connecting")
    #: What the client last said it was doing while live: listening,
    #: processing, speaking. Display only; the server never acts on it.
    phase = Column(String(16), nullable=True)
    state_version = Column(Integer, nullable=False, default=0, server_default=text("0"))
    muted = Column(Boolean, nullable=False, default=False, server_default=text("false"))
    language = Column(String(16), nullable=True)
    voice = Column(String(64), nullable=True)
    #: The configuration this session runs on, fixed at start.
    config = Column(JSON, nullable=False, default=dict)
    reconnects = Column(Integer, nullable=False, default=0, server_default=text("0"))
    #: Intervals the connection was lost: [{"started_at", "ended_at",
    #: "lost_ms", "audio_lost"}] -- said to the person, never hidden.
    gaps = Column(JSON, nullable=False, default=list)
    end_reason = Column(String(48), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)
    connected_at = Column(DateTime(timezone=True), nullable=True)
    last_seen_at = Column(DateTime(timezone=True), nullable=False, default=_now)
    ended_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("ix_voice_sessions_org_user", "organization_id", "user_id"),
        # One live session per person (handoff 9: "1 live voice session"),
        # held by the database so two tabs racing cannot both open one.
        Index(
            "uq_voice_sessions_one_live",
            "user_id",
            unique=True,
            postgresql_where=text("state IN ('connecting', 'live', 'reconnecting')"),
        ),
    )


class VoiceTurnModel(Base):
    """The timings of one turn of a voice session (handoff 12).

    Two clocks, never subtracted from each other: ``response_ms`` and
    ``interruption_ms`` are measured entirely on the person's device (speech
    end to first meaningful audible reply; interruption to playback
    stopping), and ``stages`` entirely on the server's monotonic clock. A
    stage nobody measured is absent, not zero.
    """

    __tablename__ = "voice_turns"

    id = Column(Integer, primary_key=True)
    session_id = Column(
        Integer, ForeignKey("voice_sessions.id", ondelete="CASCADE"), nullable=False
    )
    organization_id = Column(
        Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    user_id = Column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    turn_index = Column(Integer, nullable=False)
    language = Column(String(16), nullable=True)
    #: web (browser WebRTC) or pstn; reported separately (handoff 12).
    channel = Column(String(16), nullable=False, default="web")
    response_ms = Column(Integer, nullable=True)
    interruption_ms = Column(Integer, nullable=True)
    interrupted = Column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    #: A turn that called a tool is reported apart; its filler is not a
    #: meaningful response.
    tool_turn = Column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    #: Server stage durations in ms: stt_final, brain_first_text,
    #: tts_first_audio. Absent when not measured.
    stages = Column(JSON, nullable=False, default=dict)
    stt_provider = Column(String(48), nullable=True)
    tts_provider = Column(String(48), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)

    __table_args__ = (
        UniqueConstraint("session_id", "turn_index", name="uq_voice_turn_index"),
        Index("ix_voice_turns_created", "created_at"),
    )


class AppointmentPolicyModel(Base):
    """What the Call and Appointment helper may do for one workspace.

    Off until somebody sets it: ``booking`` starts at ``off``, so no call can
    book anything a person never granted (handoff 6, "book within granted
    policy"). ``revision`` is the save contract: an older one is a conflict.
    """

    __tablename__ = "appointment_policies"

    organization_id = Column(
        Integer, ForeignKey("organizations.id", ondelete="CASCADE"), primary_key=True
    )
    #: off, suggest (offer open times, never confirm), book (confirm an open
    #: time inside the hours).
    booking = Column(String(16), nullable=False, default="off")
    duration_minutes = Column(Integer, nullable=False, default=30)
    #: How soon a booking may start, and how far ahead it may be.
    lead_minutes = Column(Integer, nullable=False, default=60)
    horizon_days = Column(Integer, nullable=False, default=14)
    services = Column(JSON, nullable=False, default=list)
    #: details (collect name, number and reason from anyone; disclose
    #: nothing), known_caller (only a caller already in contacts may book).
    verification = Column(String(16), nullable=False, default="details")
    #: Who a call is handed to when the helper is unsure: a number, E.164.
    escalate_to = Column(String(32), nullable=True)
    #: The agent that places "call it for me" calls and answers booking calls.
    call_workflow_id = Column(
        Integer, ForeignKey("workflows.id", ondelete="SET NULL"), nullable=True
    )
    revision = Column(Integer, nullable=False, default=0, server_default=text("0"))
    updated_by = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"))
    updated_at = Column(DateTime(timezone=True), nullable=False, default=_now)


class AppointmentModel(Base):
    """One appointment the Call and Appointment helper booked, or a person
    did. Two bookings may not overlap in one workspace: the booking takes a
    per-workspace advisory lock and checks overlap inside it."""

    __tablename__ = "appointments"

    id = Column(Integer, primary_key=True)
    organization_id = Column(
        Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    starts_at = Column(DateTime(timezone=True), nullable=False)
    ends_at = Column(DateTime(timezone=True), nullable=False)
    #: booked, cancelled.
    status = Column(String(16), nullable=False, default="booked")
    service = Column(String(120), nullable=True)
    caller_name = Column(String(120), nullable=True)
    caller_number = Column(String(32), nullable=True)
    reason = Column(String(500), nullable=True)
    #: call, call_for_me, chat.
    source = Column(String(16), nullable=False, default="call")
    workflow_run_id = Column(
        Integer, ForeignKey("workflow_runs.id", ondelete="SET NULL"), nullable=True
    )
    booked_by_user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"))
    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)

    __table_args__ = (
        Index("ix_appointments_org_start", "organization_id", "starts_at"),
    )
