"""Tables for launch stream `support` (handoff 33; screens 28, 32-33).

Kept out of ``models.py`` for the same reason as ``shell_models``: the launch
branches must not all append to one file. ``models.py`` imports this module
at its end so the tables are on ``Base.metadata``.

Five tables, each scoped by ``organization_id``:

* ``support_tickets`` -- one request from one person in one workspace.
* ``support_messages`` -- what the customer and staff say to each other.
  Only words the customer may read live here.
* ``support_notes`` -- staff internal notes. A table of their own, not a
  flag on a message: the customer routes never select from it, so a note
  cannot reach the customer by a forgotten filter.
* ``support_attachments`` -- files on a ticket, stored through
  ``services/storage``; the row holds the key, never the bytes.
* ``support_actions`` -- typed, approved, audited staff actions for a
  customer (screen 33). No free-form command or SQL column exists.
"""

from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    CheckConstraint,
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


class SupportTicketModel(Base):
    """One request for help (screen 28), seen by staff in the inbox (32).

    ``shared`` is the exact data the person chose to share, as it was shown
    to them in the preview -- a snapshot, so a later change to the task
    cannot widen what support sees. ``version`` guards assignment and status
    changes from two staff at once.
    """

    __tablename__ = "support_tickets"

    id = Column(Integer, primary_key=True)
    organization_id = Column(
        Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    requester_user_id = Column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    #: A code from ``services/support/tickets.CATEGORIES``.
    category = Column(String(24), nullable=False)
    subject = Column(String(200), nullable=False)
    #: open, waiting_on_customer, in_progress, resolved.
    status = Column(String(24), nullable=False, default="open")
    #: low, normal, high, urgent. Set by staff.
    severity = Column(String(12), nullable=False, default="normal")
    assignee_user_id = Column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    #: ``task`` or ``reply`` when the ticket is about one; both null otherwise.
    affected_kind = Column(String(8), nullable=True)
    affected_id = Column(Integer, nullable=True)
    #: What the person chose to share, exactly as previewed.
    shared = Column(JSON, nullable=False, default=dict)
    #: A free reference to an incident (staff), e.g. ``INC-42``.
    linked_incident = Column(String(64), nullable=True)
    #: The client's key for "submit": a double tap is one ticket.
    idempotency_key = Column(String(64), nullable=True)
    version = Column(Integer, nullable=False, default=1, server_default=text("1"))
    reopened_count = Column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    first_response_at = Column(DateTime(timezone=True), nullable=True)
    resolved_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=_now)

    __table_args__ = (
        UniqueConstraint(
            "requester_user_id",
            "idempotency_key",
            name="ux_support_tickets_requester_key",
        ),
        Index(
            "ix_support_tickets_org_requester", "organization_id", "requester_user_id"
        ),
        Index("ix_support_tickets_status_created", "status", "created_at"),
    )


class SupportMessageModel(Base):
    """A line in a ticket's customer thread. Customer replies, staff
    replies and the system's notices after an action -- all of it readable
    by the customer. Internal notes are ``SupportNoteModel``."""

    __tablename__ = "support_messages"

    id = Column(Integer, primary_key=True)
    ticket_id = Column(
        Integer, ForeignKey("support_tickets.id", ondelete="CASCADE"), nullable=False
    )
    organization_id = Column(Integer, nullable=False)
    author_user_id = Column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    #: customer, staff or system.
    author_kind = Column(String(12), nullable=False)
    body = Column(Text, nullable=False)
    #: The client's key for "send": a retried reply is one message.
    client_key = Column(String(64), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)

    __table_args__ = (
        UniqueConstraint("ticket_id", "client_key", name="ux_support_messages_key"),
        Index("ix_support_messages_ticket", "ticket_id", "created_at"),
    )


class SupportNoteModel(Base):
    """A staff internal note. Never selected by a customer route."""

    __tablename__ = "support_notes"

    id = Column(Integer, primary_key=True)
    ticket_id = Column(
        Integer, ForeignKey("support_tickets.id", ondelete="CASCADE"), nullable=False
    )
    author_user_id = Column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    body = Column(Text, nullable=False)
    client_key = Column(String(64), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)

    __table_args__ = (
        UniqueConstraint("ticket_id", "client_key", name="ux_support_notes_key"),
        Index("ix_support_notes_ticket", "ticket_id", "created_at"),
    )


class SupportAttachmentModel(Base):
    """A file the customer added to a ticket."""

    __tablename__ = "support_attachments"

    id = Column(Integer, primary_key=True)
    ticket_id = Column(
        Integer, ForeignKey("support_tickets.id", ondelete="CASCADE"), nullable=False
    )
    organization_id = Column(Integer, nullable=False)
    uploaded_by = Column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    file_name = Column(String(200), nullable=False)
    content_type = Column(String(100), nullable=False)
    size_bytes = Column(Integer, nullable=False)
    storage_key = Column(String(300), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)


class SupportActionModel(Base):
    """One typed staff action for a customer (screen 33).

    The lifecycle: requested -> approved -> queued -> running -> succeeded /
    failed / outcome_unknown, or requested -> rejected / expired / cancelled,
    approved -> expired. ``version`` is a hash of the kind, the target, the
    parameters and the state they were previewed against; approval names
    it, and the run checks it again. The requester can never approve
    (a check constraint, not only the service).
    """

    __tablename__ = "support_actions"

    id = Column(Integer, primary_key=True)
    ticket_id = Column(
        Integer, ForeignKey("support_tickets.id", ondelete="SET NULL"), nullable=True
    )
    #: The customer's workspace and person the action is for.
    organization_id = Column(
        Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    target_user_id = Column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=True
    )
    #: A key of ``services/support/commands.COMMANDS``.
    kind = Column(String(32), nullable=False)
    #: Typed parameters, secrets never present (no command takes one).
    params = Column(JSON, nullable=False, default=dict)
    #: What the requester saw: target, old and new values, impact.
    preview = Column(JSON, nullable=False, default=dict)
    version = Column(String(64), nullable=False)
    reason = Column(String(500), nullable=False)
    #: requested, approved, rejected, expired, cancelled, queued, running,
    #: succeeded, failed, outcome_unknown.
    state = Column(String(20), nullable=False, default="requested")
    environment = Column(String(16), nullable=False)
    requested_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    approved_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    approved_version = Column(String(64), nullable=True)
    approved_at = Column(DateTime(timezone=True), nullable=True)
    decided_note = Column(String(500), nullable=True)
    run_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    queued_at = Column(DateTime(timezone=True), nullable=True)
    started_at = Column(DateTime(timezone=True), nullable=True)
    finished_at = Column(DateTime(timezone=True), nullable=True)
    #: The immutable result record: what happened, evidence ids.
    result = Column(JSON, nullable=True)
    idempotency_key = Column(String(80), nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=_now)

    __table_args__ = (
        UniqueConstraint("idempotency_key", name="ux_support_actions_key"),
        CheckConstraint(
            "approved_by IS NULL OR approved_by <> requested_by",
            name="ck_support_actions_second_person",
        ),
        Index("ix_support_actions_ticket", "ticket_id"),
        Index("ix_support_actions_state", "state", "created_at"),
    )
