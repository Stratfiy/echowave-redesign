"""Tables for People: a person's own contacts, with context (PEOPLE.md).

Kept out of ``models.py`` (launch convention, KAN-276); ``models.py`` imports
this module at its end so the tables are on ``Base.metadata``.

Every row belongs to one person in one workspace: ``owner_user_id`` is the
``private_to`` of reach rows under another name. Every read and write goes
through ``services/people``, which puts ``organization_id`` *and*
``owner_user_id`` in the query, so a colleague in the same workspace can never
list, search, open or change somebody else's contacts. The one way across is
``people_shares``: the owner names a colleague, and that colleague may read
the contact card (name, numbers, addresses, company) -- never the brief and
never the interactions.
"""

from datetime import UTC, datetime
from uuid import uuid4

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
    text,
)

from api.db.models import Base


def _now() -> datetime:
    return datetime.now(UTC)


class PersonModel(Base):
    """One contact of one person.

    ``phones`` are E.164 (India the default country), ``emails`` lower-case;
    both are also in ``person_handles`` so a call or a mail finds its person
    by an index rather than by scanning JSON. ``sources`` says where the
    contact came from (``google``, ``microsoft``, ``vcard``, ``csv``,
    ``picker``, ``manual``, ``decibyl``); a contact two sources agree on lists
    both.

    The brief is the short "who they are to you, what is open". ``brief_by``
    is ``decibyl`` when a model wrote it and ``you`` when the person edited
    it; an edited brief is the base the next rewrite starts from, never
    thrown away. ``brief_due_at`` is the debounce: set to a few minutes after
    the latest interaction, cleared when the brief is written.
    """

    __tablename__ = "people"

    id = Column(Integer, primary_key=True)
    uuid = Column(String(36), nullable=False, unique=True, default=lambda: str(uuid4()))
    organization_id = Column(
        Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    owner_user_id = Column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    name = Column(String(200), nullable=False)
    phones = Column(
        JSON, nullable=False, default=list, server_default=text("'[]'::json")
    )
    emails = Column(
        JSON, nullable=False, default=list, server_default=text("'[]'::json")
    )
    company = Column(String(200), nullable=True)
    relation = Column(String(200), nullable=True)
    sources = Column(
        JSON, nullable=False, default=list, server_default=text("'[]'::json")
    )
    brief = Column(Text, nullable=True)
    #: ``decibyl`` or ``you``.
    brief_by = Column(String(16), nullable=True)
    brief_at = Column(DateTime(timezone=True), nullable=True)
    brief_due_at = Column(DateTime(timezone=True), nullable=True)
    last_interaction_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=_now)
    updated_at = Column(DateTime(timezone=True), default=_now, onupdate=_now)

    __table_args__ = (
        Index("ix_people_owner", "organization_id", "owner_user_id"),
        Index(
            "ix_people_brief_due",
            "brief_due_at",
            postgresql_where=text("brief_due_at IS NOT NULL"),
        ),
    )


class PersonHandleModel(Base):
    """A phone number or email address of one contact, normalised, for the
    lookups an interaction makes ("who is +919876543210 to this person?")."""

    __tablename__ = "person_handles"

    id = Column(Integer, primary_key=True)
    person_id = Column(
        Integer, ForeignKey("people.id", ondelete="CASCADE"), nullable=False
    )
    organization_id = Column(Integer, nullable=False)
    owner_user_id = Column(Integer, nullable=False)
    #: ``phone`` or ``email``.
    kind = Column(String(8), nullable=False)
    value = Column(String(320), nullable=False)

    __table_args__ = (
        Index(
            "ix_person_handles_lookup",
            "organization_id",
            "owner_user_id",
            "kind",
            "value",
        ),
        Index("ix_person_handles_person", "person_id"),
    )


class PersonSourceModel(Base):
    """Where a contact came from outside Decibyl: the provider's own id, so
    the next incremental sync updates the same row instead of adding one."""

    __tablename__ = "person_sources"

    id = Column(Integer, primary_key=True)
    person_id = Column(
        Integer, ForeignKey("people.id", ondelete="CASCADE"), nullable=False
    )
    organization_id = Column(Integer, nullable=False)
    owner_user_id = Column(Integer, nullable=False)
    #: ``google`` or ``microsoft``.
    provider = Column(String(16), nullable=False)
    external_id = Column(String(300), nullable=False)
    etag = Column(String(300), nullable=True)
    updated_at = Column(DateTime(timezone=True), default=_now, onupdate=_now)

    __table_args__ = (
        Index(
            "ux_person_sources_external",
            "organization_id",
            "owner_user_id",
            "provider",
            "external_id",
            unique=True,
        ),
    )


class PersonInteractionModel(Base):
    """One thing that happened with a contact: a call, a WhatsApp message,
    an email, a meeting. One line, written by the code path that did it.

    ``ref`` names what it came from (``run:812``, ``mail:<message-id>``) and
    is unique per owner, so a retried job records it once.
    """

    __tablename__ = "person_interactions"

    id = Column(Integer, primary_key=True)
    person_id = Column(
        Integer, ForeignKey("people.id", ondelete="CASCADE"), nullable=False
    )
    organization_id = Column(Integer, nullable=False)
    owner_user_id = Column(Integer, nullable=False)
    #: ``call``, ``whatsapp``, ``email`` or ``meeting``.
    channel = Column(String(16), nullable=False)
    #: ``in``, ``out`` or ``both``.
    direction = Column(String(8), nullable=False, default="out")
    line = Column(String(300), nullable=False)
    ref = Column(String(200), nullable=False)
    at = Column(DateTime(timezone=True), nullable=False, default=_now)

    __table_args__ = (
        Index(
            "ux_person_interactions_ref",
            "organization_id",
            "owner_user_id",
            "ref",
            unique=True,
        ),
        Index("ix_person_interactions_person_at", "person_id", "at"),
    )


class PeopleSyncModel(Base):
    """One person's sync with one provider: its state and the provider's
    cursor (Google's ``syncToken``, Graph's ``deltaLink``)."""

    __tablename__ = "people_syncs"

    id = Column(Integer, primary_key=True)
    organization_id = Column(
        Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    user_id = Column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    provider = Column(String(16), nullable=False)
    #: ``idle``, ``syncing``, ``ok`` or ``error``.
    status = Column(String(16), nullable=False, default="idle")
    cursor = Column(Text, nullable=True)
    last_error = Column(String(300), nullable=True)
    last_synced_at = Column(DateTime(timezone=True), nullable=True)
    started_at = Column(DateTime(timezone=True), nullable=True)
    counts = Column(
        JSON, nullable=False, default=dict, server_default=text("'{}'::json")
    )
    updated_at = Column(DateTime(timezone=True), default=_now, onupdate=_now)

    __table_args__ = (
        Index(
            "ux_people_syncs_owner",
            "organization_id",
            "user_id",
            "provider",
            unique=True,
        ),
    )


class PersonMergeModel(Base):
    """Two contacts that look like one: same number or same address.

    Never merged by itself. The owner sees both side by side and chooses
    Merge or Keep both; ``status`` is ``open``, ``merged`` or ``dismissed``.
    """

    __tablename__ = "person_merges"

    id = Column(Integer, primary_key=True)
    uuid = Column(String(36), nullable=False, unique=True, default=lambda: str(uuid4()))
    organization_id = Column(Integer, nullable=False)
    owner_user_id = Column(Integer, nullable=False)
    keep_id = Column(
        Integer, ForeignKey("people.id", ondelete="CASCADE"), nullable=False
    )
    other_id = Column(
        Integer, ForeignKey("people.id", ondelete="CASCADE"), nullable=False
    )
    #: ``phone`` or ``email``, and the value both share.
    reason = Column(String(8), nullable=False)
    value = Column(String(320), nullable=False)
    status = Column(String(16), nullable=False, default="open")
    created_at = Column(DateTime(timezone=True), default=_now)
    decided_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index(
            "ux_person_merges_pair",
            "owner_user_id",
            "keep_id",
            "other_id",
            unique=True,
        ),
        Index("ix_person_merges_owner", "organization_id", "owner_user_id", "status"),
    )


class PersonShareModel(Base):
    """The owner showed one contact card to one colleague. Name, numbers,
    addresses and company only; the brief and the interactions stay the
    owner's."""

    __tablename__ = "person_shares"

    id = Column(Integer, primary_key=True)
    person_id = Column(
        Integer, ForeignKey("people.id", ondelete="CASCADE"), nullable=False
    )
    organization_id = Column(Integer, nullable=False)
    owner_user_id = Column(Integer, nullable=False)
    shared_with_user_id = Column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    created_at = Column(DateTime(timezone=True), default=_now)

    __table_args__ = (
        Index(
            "ux_person_shares_pair",
            "person_id",
            "shared_with_user_id",
            unique=True,
        ),
        Index("ix_person_shares_with", "organization_id", "shared_with_user_id"),
    )


class PeopleSettingsModel(Base):
    """One person's choices for People. ``agents_may_read``: whether
    Decibyl's outreach and voice agents may read a contact's brief when
    they call or write to that contact for this person. Off until chosen."""

    __tablename__ = "people_settings"

    id = Column(Integer, primary_key=True)
    organization_id = Column(
        Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    user_id = Column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    agents_may_read = Column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    updated_at = Column(DateTime(timezone=True), default=_now, onupdate=_now)

    __table_args__ = (
        Index("ux_people_settings_owner", "organization_id", "user_id", unique=True),
    )
