"""Tables for launch stream `agents` (LAUNCH-PLAN.md, phase 2).

Kept out of ``models.py`` (launch convention, KAN-276); ``models.py`` imports
this module at its end so the tables are on ``Base.metadata``.

Every row carries ``organization_id``; the ones a person owns also carry
``owner_user_id`` and a ``visibility`` of ``private`` (the owner only) or
``workspace`` (every member). Private is the default: a member shares on
purpose, never by accident. See ``AGENTS-LAUNCH.md`` for the design.
"""

from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Column,
    Date,
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


class HelperWorkspaceSettingModel(Base):
    """A workspace's own switch for one helper, set by an admin.

    No row means the helper is on: the five are the product, and a
    workspace turns one off on purpose ("Turned off by your workspace").
    Shared by every member of the workspace, so a team sees one set.
    """

    __tablename__ = "helper_workspace_settings"

    id = Column(Integer, primary_key=True)
    organization_id = Column(
        Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    helper = Column(String(32), nullable=False)
    enabled = Column(Boolean, nullable=False, default=True)
    updated_by = Column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    updated_at = Column(DateTime(timezone=True), nullable=False, default=_now)

    __table_args__ = (
        UniqueConstraint(
            "organization_id", "helper", name="uq_helper_workspace_settings"
        ),
    )


class SavedReportModel(Base):
    """A research report kept from a conversation (handoff 6, Research).

    Findings keep their basis (``source`` or ``inference``) and the sources
    they rest on; conflicting evidence and the sources that could not be
    read are kept beside them, never dropped. ``body`` is the rendered
    Markdown and ``content_hash`` its hash: the screen and the export are
    both that one rendering, so the export matches what was shown.
    """

    __tablename__ = "saved_reports"

    id = Column(Integer, primary_key=True)
    uuid = Column(String(36), nullable=False, unique=True)
    organization_id = Column(
        Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    owner_user_id = Column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    visibility = Column(String(16), nullable=False, default="private")
    #: ``research`` or ``trading_summary`` (information only).
    kind = Column(String(24), nullable=False, default="research")
    thread_id = Column(String(64), nullable=True)
    title = Column(String(200), nullable=False)
    question = Column(Text, nullable=True)
    summary = Column(Text, nullable=True)
    findings = Column(JSON, nullable=False, default=list)
    conflicts = Column(JSON, nullable=False, default=list)
    inaccessible = Column(JSON, nullable=False, default=list)
    sources = Column(JSON, nullable=False, default=list)
    body = Column(Text, nullable=False)
    content_hash = Column(String(64), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=_now)

    __table_args__ = (
        Index("ix_saved_reports_org_owner", "organization_id", "owner_user_id"),
    )


class CommitmentModel(Base):
    """A commitment a person approved tracking (handoff 6, Follow-up).

    ``owed_to_me`` rows are "who owes me". The follow-up itself is an
    ordinary action card (``follow_up_card_id``): its state is the delivery
    state, read live from the card, so a retry, a cancel or an edit keeps
    the card's run-once and re-approval rules.
    """

    __tablename__ = "commitments"

    id = Column(Integer, primary_key=True)
    uuid = Column(String(36), nullable=False, unique=True)
    organization_id = Column(
        Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    owner_user_id = Column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    visibility = Column(String(16), nullable=False, default="private")
    #: ``owed_to_me`` or ``i_owe``.
    direction = Column(String(16), nullable=False)
    counterparty = Column(String(200), nullable=False)
    contact = Column(String(320), nullable=True)
    description = Column(Text, nullable=False)
    #: In the currency's minor unit (paise), so no float ever holds money.
    amount_minor = Column(BigInteger, nullable=True)
    currency = Column(String(3), nullable=True)
    due_on = Column(Date, nullable=True)
    #: ``open``, ``settled`` or ``cancelled``.
    status = Column(String(16), nullable=False, default="open")
    follow_up_card_id = Column(Integer, nullable=True)
    #: The card that approved tracking it, when it came from a conversation.
    approved_card_id = Column(Integer, nullable=True)
    revision = Column(Integer, nullable=False, default=1, server_default=text("1"))
    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=_now)
    settled_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("ix_commitments_org_owner", "organization_id", "owner_user_id"),
        Index(
            "ux_commitments_approved_card",
            "organization_id",
            "approved_card_id",
            unique=True,
            postgresql_where=text("approved_card_id IS NOT NULL"),
        ),
    )


class ResearchInterestsModel(Base):
    """What one person follows for trading summaries: tickers, sectors and
    topics. The person's own, like their preferences: never a workspace's."""

    __tablename__ = "research_interests"

    user_id = Column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    interests = Column(JSON, nullable=False, default=list)
    revision = Column(Integer, nullable=False, default=1, server_default=text("1"))
    updated_at = Column(DateTime(timezone=True), nullable=False, default=_now)


class TrackerModel(Base):
    """A small list a person asked Decibyl to build ("track my client
    visits"): named columns, rows added from chat or the API."""

    __tablename__ = "trackers"

    id = Column(Integer, primary_key=True)
    uuid = Column(String(36), nullable=False, unique=True)
    organization_id = Column(
        Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    owner_user_id = Column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    visibility = Column(String(16), nullable=False, default="private")
    name = Column(String(120), nullable=False)
    columns = Column(JSON, nullable=False, default=list)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)

    __table_args__ = (
        Index("ix_trackers_org_owner", "organization_id", "owner_user_id"),
    )


class TrackerEntryModel(Base):
    __tablename__ = "tracker_entries"

    id = Column(Integer, primary_key=True)
    tracker_id = Column(
        Integer, ForeignKey("trackers.id", ondelete="CASCADE"), nullable=False
    )
    organization_id = Column(
        Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    values = Column(JSON, nullable=False, default=dict)
    created_by = Column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)

    __table_args__ = (Index("ix_tracker_entries_tracker", "tracker_id"),)
