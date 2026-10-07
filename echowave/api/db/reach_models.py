"""Tables for launch stream `reach`: outside tools and ordering.

Kept out of ``models.py`` (launch convention, KAN-276); ``models.py`` imports
this module at its end so the tables are on ``Base.metadata``.

Both tables belong to one person in one workspace. Every read and write goes
through ``services/reach/connections.py`` or ``services/reach/ordering``,
which filter by ``organization_id`` *and* ``user_id`` in the query, so a
colleague in the same workspace can never list, use or revoke another
person's connection or see their order.
"""

from datetime import UTC, datetime
from uuid import uuid4

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


class ReachConnectionModel(Base):
    """One person's connection to an outside server.

    ``kind`` is ``tool`` for an outside AI tool (any MCP server the person
    adds) or ``ordering`` for an ordering app (Zomato, Swiggy) reached through
    its official server. Secrets (a pasted token, OAuth tokens, a PKCE
    verifier while signing in) are Fernet-encrypted in ``secret_encrypted``
    and never returned by any route. No card details are ever stored: the
    ordering apps take payment on their own side.
    """

    __tablename__ = "reach_connections"

    id = Column(Integer, primary_key=True)
    uuid = Column(String(36), nullable=False, unique=True, default=lambda: str(uuid4()))
    organization_id = Column(
        Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    user_id = Column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    #: ``tool`` or ``ordering``.
    kind = Column(String(16), nullable=False)
    #: A slug: the ordering app's key, or one made from the tool's name.
    provider = Column(String(64), nullable=False)
    name = Column(String(120), nullable=False)
    server_url = Column(String(2048), nullable=False)
    #: ``none``, ``token`` or ``oauth``.
    auth = Column(String(16), nullable=False, default="none")
    #: ``pending`` (signing in), ``connected``, ``error`` or ``revoked``.
    status = Column(String(16), nullable=False, default="pending")
    secret_encrypted = Column(Text, nullable=True)
    #: sha256 of the OAuth ``state`` while a sign-in is open; cleared after.
    oauth_state_hash = Column(String(64), nullable=True)
    #: What the server offers, as discovered: ``[{name, description, read}]``.
    tools = Column(
        JSON, nullable=False, default=list, server_default=text("'[]'::json")
    )
    last_error = Column(String(300), nullable=True)
    connected_at = Column(DateTime(timezone=True), nullable=True)
    revoked_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=_now)
    updated_at = Column(DateTime(timezone=True), default=_now, onupdate=_now)

    __table_args__ = (
        # One live connection per person, kind and provider: connecting
        # again replaces the old one rather than adding a second.
        Index(
            "ux_reach_connections_live",
            "organization_id",
            "user_id",
            "kind",
            "provider",
            unique=True,
            postgresql_where=text("revoked_at IS NULL"),
        ),
        Index("ix_reach_connections_owner", "organization_id", "user_id"),
        Index("ix_reach_connections_oauth_state_hash", "oauth_state_hash"),
    )


class ReachOrderDraftModel(Base):
    """An order as quoted, waiting for its card, placed, or not.

    ``digest`` is a hash of exactly what the card shows -- store, items,
    every charge, the total, the address and the payment method. The card's
    payload carries it, so the card's version (``actions.payload_version``)
    changes whenever any of those change, and at run time the draft is
    re-hashed: an order that no longer matches what was approved is refused,
    not placed.
    """

    __tablename__ = "reach_order_drafts"

    id = Column(Integer, primary_key=True)
    uuid = Column(String(36), nullable=False, unique=True, default=lambda: str(uuid4()))
    organization_id = Column(
        Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    user_id = Column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    connection_id = Column(
        Integer, ForeignKey("reach_connections.id", ondelete="SET NULL"), nullable=True
    )
    provider = Column(String(64), nullable=False)
    store = Column(
        JSON, nullable=False, default=dict, server_default=text("'{}'::json")
    )
    items = Column(
        JSON, nullable=False, default=list, server_default=text("'[]'::json")
    )
    quote = Column(
        JSON, nullable=False, default=dict, server_default=text("'{}'::json")
    )
    address = Column(
        JSON, nullable=False, default=dict, server_default=text("'{}'::json")
    )
    payment = Column(
        JSON, nullable=False, default=dict, server_default=text("'{}'::json")
    )
    digest = Column(String(64), nullable=False)
    #: ``proposed``, ``placing``, ``placed``, ``failed``, ``outcome_unknown``
    #: or ``cancelled``.
    status = Column(String(24), nullable=False, default="proposed")
    card_event_id = Column(BigInteger, nullable=True)
    provider_cart_id = Column(String(200), nullable=True)
    provider_order_id = Column(String(200), nullable=True)
    result = Column(
        JSON, nullable=False, default=dict, server_default=text("'{}'::json")
    )
    quoted_at = Column(DateTime(timezone=True), nullable=False, default=_now)
    created_at = Column(DateTime(timezone=True), default=_now)
    updated_at = Column(DateTime(timezone=True), default=_now, onupdate=_now)

    __table_args__ = (
        Index("ix_reach_order_drafts_owner", "organization_id", "user_id"),
    )
