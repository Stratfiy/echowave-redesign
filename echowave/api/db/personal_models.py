"""Tables for personal adaptation (``evolve_personal``; services/personal/).

Kept out of ``models.py`` (launch convention, KAN-276); ``models.py`` imports
this module at its end so the tables are on ``Base.metadata`` for alembic and
the tests.

Two tables:

* ``personal_preferences`` -- the plan's "explicit personal preferences"
  store (research-intelligence-plan section 5): one row per thing a person
  said they prefer, owned by that person and nobody else. Never a row in
  ``organisation_facts``: a workspace's facts are read by every member and
  agent there, and a person's "Tamil for calls" is not the workspace's.
* ``conversation_context_choices`` -- which sources a person left out of one
  conversation (the context control above the composer).
"""

from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    text,
)

from api.db.models import Base


def _now() -> datetime:
    return datetime.now(UTC)


class PersonalPreferenceModel(Base):
    """One preference a person stated, with where it came from and what it
    replaced.

    **Owned by a person, not a workspace.** ``user_id`` is the owner and the
    only reader: every query filters on it, and no route takes another
    person's id. ``organization_id`` is the tenant the words were said in --
    provenance, not visibility -- so a person's "call me after 10" follows
    them, the way ``member_preferences`` does, and is still never readable
    by a colleague in that workspace or anyone in another one.

    **A correction is a new row.** The old one becomes ``superseded`` and the
    new one names it in ``supersedes_id``: the history of what a person said
    is kept (plan section 5, "Corrections should supersede old values
    without erasing the historical event"). Forget deletes the row and every
    row it replaced; nothing of it is left to be read.
    """

    __tablename__ = "personal_preferences"

    id = Column(Integer, primary_key=True)
    user_id = Column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    #: Where it was said. SET NULL: a workspace deleted later does not take
    #: the person's own preferences with it.
    organization_id = Column(
        Integer, ForeignKey("organizations.id", ondelete="SET NULL"), nullable=True
    )
    #: language | call_window | channel | cadence | length | note
    kind = Column(String(24), nullable=False)
    #: What it is for: calls | email | chat | reminders | reports | general
    topic = Column(String(24), nullable=False, default="general")
    #: The canonical value code reads: "ta-IN", "10:00", "whatsapp", "weekly",
    #: or the person's own words for a note.
    value = Column(Text, nullable=False)
    #: The line a person reads: "Calls in Tamil".
    label = Column(String(200), nullable=False)
    #: message | feedback | correction | suggestion
    source_kind = Column(String(16), nullable=False, default="message")
    #: The line it came from, when it came from one.
    source_event_id = Column(
        BigInteger, ForeignKey("agent_events.id", ondelete="SET NULL"), nullable=True
    )
    source_thread_id = Column(String(36), nullable=True)
    #: The words themselves, clipped: what the person reads under "From".
    source_excerpt = Column(String(300), nullable=True)
    #: When it was said.
    observed_at = Column(DateTime(timezone=True), nullable=False, default=_now)
    #: When it takes effect, when the person said ("from Monday").
    effective_from = Column(DateTime(timezone=True), nullable=True)
    #: When to ask again whether it still holds; NULL is "until changed".
    review_at = Column(DateTime(timezone=True), nullable=True)
    #: confirmed (said by the person, or accepted on a card) | superseded
    status = Column(String(16), nullable=False, default="confirmed")
    supersedes_id = Column(
        Integer,
        ForeignKey("personal_preferences.id", ondelete="SET NULL"),
        nullable=True,
    )
    superseded_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)

    __table_args__ = (
        Index("ix_personal_preferences_owner", "user_id", "status"),
        # One live answer per (person, kind, topic) for the kinds code reads:
        # "what language are calls in" has exactly one answer. Notes may be
        # many, so they are left out of the index.
        Index(
            "uq_personal_preferences_live",
            "user_id",
            "kind",
            "topic",
            unique=True,
            postgresql_where=text("status = 'confirmed' AND kind <> 'note'"),
        ),
    )


class ConversationContextChoiceModel(Base):
    """The sources one person left out of one conversation.

    ``thread_key`` is the thread id, or ``original`` for the conversation
    from before threads. ``excluded`` is a list of source ids
    (``personal``, ``workspace``, ``knowledge``, ``files``, ``app:<toolkit>``);
    empty means everything is in, which is also what no row means.
    """

    __tablename__ = "conversation_context_choices"

    id = Column(Integer, primary_key=True)
    organization_id = Column(
        Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    user_id = Column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    thread_key = Column(String(40), nullable=False)
    excluded = Column(JSON, nullable=False, default=list)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=_now)

    __table_args__ = (
        Index(
            "uq_conversation_context_choices",
            "organization_id",
            "user_id",
            "thread_key",
            unique=True,
        ),
    )
