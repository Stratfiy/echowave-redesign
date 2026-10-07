"""Tables for launch stream `meetings` (LAUNCH-PLAN.md, phase 2).

Handoff 23 ("Meeting capture and preparation") and screens 11-12. Kept out of
``models.py`` (launch convention, KAN-276); ``models.py`` imports this module
at its end so the tables are on ``Base.metadata`` for alembic and the tests.

A meeting belongs to one person inside one workspace: every read and write
filters on both ``organization_id`` and ``owner_user_id``, so a colleague in
the same workspace cannot open it (services/meetings/records.py). In the
person's personal space the workspace is theirs alone as well.

See ``MEETINGS.md`` at the repository's ``echowave/`` root for the design.
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
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy import (
    text as sql_text,
)

from api.db.models import Base


def _now() -> datetime:
    return datetime.now(UTC)


class MeetingModel(Base):
    """One captured meeting: where its words came from and what became of
    them.

    ``source`` is the stated audio source (screen 11): ``microphone`` is this
    device's microphone and nothing else, ``upload`` a recording the person
    chose, ``notes`` words pasted with no audio at all. There is no source
    for another app's audio or a phone call, because the product cannot
    capture one.
    """

    __tablename__ = "meetings"

    id = Column(Integer, primary_key=True)
    #: The id the screens and links use; never the row id.
    public_id = Column(String(32), nullable=False, unique=True)
    organization_id = Column(
        Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    owner_user_id = Column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    title = Column(String(200), nullable=False)
    #: ``microphone`` | ``upload`` | ``notes``.
    source = Column(String(16), nullable=False)
    #: A launch language code (services/shell/languages.py) or ``unknown``,
    #: which asks Sarvam to detect it.
    language = Column(String(16), nullable=False, default="unknown")
    participants = Column(JSON, nullable=True)
    #: The Chat conversation it was started from, so "Return to chat" goes
    #: back there. Not a foreign key: a thread is the id its events share.
    origin_thread_id = Column(String(36), nullable=True)
    #: When the person confirmed they have the participants' permission.
    #: Required for any audio source; NULL only for pasted notes.
    consent_confirmed_at = Column(DateTime(timezone=True), nullable=True)
    #: ``recording`` | ``paused`` | ``processing`` | ``ready`` | ``partial``
    #: | ``failed``.
    status = Column(String(16), nullable=False)
    #: Why it failed or is partial, in words a person reads.
    status_reason = Column(Text, nullable=True)
    #: Milliseconds actually captured: the timer follows real capture, so a
    #: pause or an interruption does not count.
    captured_ms = Column(
        Integer, nullable=False, default=0, server_default=sql_text("0")
    )
    capture_started_at = Column(DateTime(timezone=True), nullable=True)
    capture_ended_at = Column(DateTime(timezone=True), nullable=True)
    #: The highest segment number the client says it sent, at Stop.
    last_seq = Column(Integer, nullable=True)
    upload_name = Column(String(200), nullable=True)
    #: The reading (summary, decisions, suggested actions): ``pending`` |
    #: ``reading`` | ``ready`` | ``needs_setup`` | ``limited`` | ``failed`` |
    #: ``empty``.
    reading_status = Column(
        String(16), nullable=False, default="pending", server_default="pending"
    )
    reading_note = Column(Text, nullable=True)
    summary = Column(JSON, nullable=True)
    read_at = Column(DateTime(timezone=True), nullable=True)
    #: Bumped on every change the record screen shows, so a poll can tell.
    revision = Column(Integer, nullable=False, default=1, server_default=sql_text("1"))
    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=_now)

    __table_args__ = (
        Index(
            "ix_meetings_owner",
            "organization_id",
            "owner_user_id",
            "created_at",
        ),
    )


class MeetingSegmentModel(Base):
    """One stretch of a meeting's audio (or its pasted notes) and its words.

    Live capture sends a segment every few seconds; an upload is split into
    segments short enough for Sarvam's synchronous endpoint. The audio is
    held only until it is transcribed and then dropped (``audio`` set to
    NULL): recording retention is off by default (handoff 24, "Privacy and
    permissions"). A segment whose transcription failed keeps its audio so
    it can be tried again -- that is the recoverable content.
    """

    __tablename__ = "meeting_segments"

    id = Column(Integer, primary_key=True)
    meeting_id = Column(
        Integer, ForeignKey("meetings.id", ondelete="CASCADE"), nullable=False
    )
    organization_id = Column(Integer, nullable=False)
    seq = Column(Integer, nullable=False)
    #: Capture time, in milliseconds from the start of the meeting.
    start_ms = Column(Integer, nullable=False, default=0)
    end_ms = Column(Integer, nullable=False, default=0)
    #: ``pending`` | ``transcribing`` | ``done`` | ``failed`` | ``source``
    #: (an uploaded file waiting to be split).
    status = Column(String(16), nullable=False)
    audio = Column(LargeBinary, nullable=True)
    content_type = Column(String(64), nullable=True)
    text = Column(Text, nullable=True)
    #: The person's correction (screen 12); the original stays beside it.
    corrected_text = Column(Text, nullable=True)
    language = Column(String(16), nullable=True)
    error = Column(Text, nullable=True)
    #: Words in it that often mean a commitment ("I will send", "by Friday").
    has_action_cue = Column(
        Boolean, nullable=False, default=False, server_default=sql_text("false")
    )
    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)
    transcribed_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        UniqueConstraint("meeting_id", "seq", name="uq_meeting_segments_seq"),
    )


class MeetingBreakModel(Base):
    """A stretch with no capture: a pause the person chose, or a gap.

    A gap is shown honestly on the record (screen 11, "A source failure
    identifies the missing interval"): the microphone was lost, the page was
    in the background, the network dropped a segment, or a segment never
    arrived. A pause is the person's own choice and reads as one.
    """

    __tablename__ = "meeting_breaks"

    id = Column(Integer, primary_key=True)
    meeting_id = Column(
        Integer, ForeignKey("meetings.id", ondelete="CASCADE"), nullable=False
    )
    organization_id = Column(Integer, nullable=False)
    #: ``pause`` | ``gap``.
    kind = Column(String(8), nullable=False)
    #: For a gap: ``microphone_lost`` | ``backgrounded`` | ``offline`` |
    #: ``upload_failed`` | ``missing_segment`` | ``recorder_error``.
    reason = Column(String(32), nullable=True)
    #: Capture time at which it happened.
    at_ms = Column(Integer, nullable=False, default=0)
    #: Wall-clock length, when known.
    duration_ms = Column(Integer, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)

    __table_args__ = (Index("ix_meeting_breaks_meeting", "meeting_id"),)


class MeetingItemModel(Base):
    """A decision, or a suggested follow-up action, read from the transcript.

    Every item points at its source (``segment_seq`` and ``excerpt``); a
    suggested action becomes real only through its own action card
    (``card_event_id``, services/workflow/actions.py), and the task the card
    created is ``task_id``.
    """

    __tablename__ = "meeting_items"

    id = Column(Integer, primary_key=True)
    meeting_id = Column(
        Integer, ForeignKey("meetings.id", ondelete="CASCADE"), nullable=False
    )
    organization_id = Column(Integer, nullable=False)
    #: ``decision`` | ``action``.
    kind = Column(String(16), nullable=False)
    text = Column(Text, nullable=False)
    owner_name = Column(String(120), nullable=True)
    #: The time as it was said ("by Friday"), and the resolved time only when
    #: one was actually said; never invented.
    due_text = Column(String(120), nullable=True)
    due_at = Column(DateTime(timezone=True), nullable=True)
    #: ``high`` | ``medium`` | ``low``.
    confidence = Column(String(8), nullable=True)
    #: What is missing before it can be acted on: ``owner``, ``due``.
    missing = Column(JSON, nullable=True)
    segment_seq = Column(Integer, nullable=True)
    excerpt = Column(Text, nullable=True)
    #: Whether the excerpt was found in the transcript.
    source_found = Column(
        Boolean, nullable=False, default=False, server_default=sql_text("false")
    )
    edited = Column(
        Boolean, nullable=False, default=False, server_default=sql_text("false")
    )
    card_event_id = Column(Integer, nullable=True)
    task_id = Column(Integer, nullable=True)
    position = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=_now)

    __table_args__ = (Index("ix_meeting_items_meeting", "meeting_id", "kind"),)
