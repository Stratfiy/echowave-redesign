"""Tables for launch stream `settings` (LAUNCH-PLAN.md, phase 2).

Kept out of ``models.py`` (launch convention, KAN-276); ``models.py`` imports
this module at its end so the tables are on ``Base.metadata`` for alembic and
the tests. The person's own preferences stay in ``member_preferences`` (stream
`controls`); this stream adds columns there rather than a second store.

See ``SETTINGS.md`` at the repository's ``echowave/`` root for the design.
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
)

from api.db.models import Base


def _now() -> datetime:
    return datetime.now(UTC)


class MemoryFactRevisionModel(Base):
    """One change to a remembered fact: made, edited, forgotten, put back,
    shared. A fact's history (screen 16, "A fact detail shows provenance and
    changes"), and what lets an edit be checked against the value the screen
    read.

    No foreign key to ``organisation_facts``: sharing a personal fact moves
    it to a new row, and the history moves with it rather than disappearing.
    Readers reach a revision only through the fact, after the fact's own
    scope check, so a colleague never reads another member's old values.
    """

    __tablename__ = "memory_fact_revisions"

    id = Column(Integer, primary_key=True)
    organization_id = Column(
        Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    fact_id = Column(Integer, nullable=False)
    actor_user_id = Column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    #: created | edited | forgotten | restored | shared
    change = Column(String(16), nullable=False)
    before_value = Column(Text, nullable=True)
    after_value = Column(Text, nullable=True)
    #: One plain line: "Shared to Acme Clinic".
    note = Column(String(255), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)

    __table_args__ = (
        Index("ix_memory_fact_revisions_fact", "organization_id", "fact_id"),
    )


class SavedItemModel(Base):
    """Something a person kept: a reply, a file, a note (screen 15).

    Owned by the person, held in one scope -- a workspace or their personal
    space (``organization_id``). ``visibility`` is ``private`` (only the
    owner) or ``workspace`` (every member of that workspace). Nobody else's
    private item is ever returned, not even as a search hint.
    """

    __tablename__ = "saved_items"

    id = Column(Integer, primary_key=True)
    organization_id = Column(
        Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    owner_user_id = Column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    visibility = Column(String(16), nullable=False, default="private")
    #: reply | file | note | link
    kind = Column(String(16), nullable=False, default="reply")
    title = Column(String(200), nullable=False)
    body = Column(Text, nullable=True)
    #: The conversation line it came from, so "Return to conversation" works.
    source_event_id = Column(
        BigInteger, ForeignKey("agent_events.id", ondelete="SET NULL"), nullable=True
    )
    thread_id = Column(String(36), nullable=True)
    #: {filename, size, url?} for a file.
    file_ref = Column(JSON, nullable=True)
    #: ready | processing | deleted
    status = Column(String(16), nullable=False, default="ready")
    deleted_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=_now)

    __table_args__ = (
        Index("ix_saved_items_scope", "organization_id", "owner_user_id"),
    )


class PersonalDataRequestModel(Base):
    """A person's request about their own data: an export or a deletion
    (screen 25). The record of what happened to each store, kept after the
    data is gone -- "Deletion records track downstream processing and
    legitimate retention exceptions".

    ``user_id`` is SET NULL on purpose: a deletion record must outlive the
    account it deleted.
    """

    __tablename__ = "personal_data_requests"

    id = Column(Integer, primary_key=True)
    user_id = Column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    #: Where the approval card lives (the person's personal space).
    organization_id = Column(
        Integer, ForeignKey("organizations.id", ondelete="SET NULL"), nullable=True
    )
    #: export | deletion
    kind = Column(String(16), nullable=False)
    #: awaiting_approval | queued | running | ready | expired | pending |
    #: partial_failure | complete | cancelled | failed
    status = Column(String(24), nullable=False)
    #: [{store, label, count, status, exception}]
    stores = Column(JSON, nullable=False, default=list)
    card_event_id = Column(BigInteger, nullable=True)
    #: The export itself, for a personal export (small: preferences, own
    #: facts, onboarding answers, saved items).
    export_payload = Column(JSON, nullable=True)
    error = Column(String(255), nullable=True)
    expires_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=_now)

    __table_args__ = (Index("ix_personal_data_requests_user", "user_id", "kind"),)


class TemporaryConversationModel(Base):
    """A conversation that saves nothing to memory and is deleted after a
    while (screen 18, "Temporary conversation is a clear new-session action
    and explains effective retention rather than promising zero processing").
    """

    __tablename__ = "temporary_conversations"

    id = Column(Integer, primary_key=True)
    organization_id = Column(
        Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    user_id = Column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    thread_id = Column(String(36), nullable=False, unique=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    purged_at = Column(DateTime(timezone=True), nullable=True)
