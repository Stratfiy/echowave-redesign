"""Tables for launch stream `identity` (LAUNCH-PLAN.md, phase 2).

Kept out of ``models.py`` (launch convention, KAN-276); ``models.py`` imports
this module at its end so the tables are on ``Base.metadata`` for alembic and
the tests. See ``IDENTITY.md`` at the repository's ``echowave/`` root.

Everything a person owns here is keyed by ``user_id`` and, where it lives in
a workspace, by ``organization_id`` as well; every reader filters by both.
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


class ConnectionConsentModel(Base):
    """One person's consent to connect one app, and what became of it.

    Written before the provider's sign-in opens (handoff 22: "before OAuth
    explain the immediate task and minimum access"), with the words the
    person was shown, so the record says what they agreed to and where to
    return them afterwards. The provider (Composio) remains the source of
    truth for whether the account exists; this row is the consent and the
    lifecycle we observed.
    """

    __tablename__ = "connection_consents"

    id = Column(Integer, primary_key=True)
    organization_id = Column(
        Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    user_id = Column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    toolkit = Column(String(64), nullable=False)
    #: ``mine`` (only this person's assistant may use it) or ``workspace``.
    scope = Column(String(16), nullable=False)
    #: authorizing | syncing | ready | limited | expired | error | revoked
    state = Column(String(16), nullable=False)
    #: The task the person was doing, in their words or the screen's.
    purpose = Column(String(200), nullable=True)
    #: The access lines shown before Connect, exactly as shown.
    access = Column(JSON, nullable=False, default=list)
    #: A path inside the app to return to (never an outside address).
    return_to = Column(String(500), nullable=True)
    connected_account_id = Column(String(128), nullable=True)
    reason_code = Column(String(64), nullable=True)
    started_at = Column(DateTime(timezone=True), nullable=False, default=_now)
    ready_at = Column(DateTime(timezone=True), nullable=True)
    last_success_at = Column(DateTime(timezone=True), nullable=True)
    revoked_at = Column(DateTime(timezone=True), nullable=True)
    revoked_by = Column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    updated_at = Column(DateTime(timezone=True), nullable=False, default=_now)

    __table_args__ = (
        Index("ix_connection_consents_owner", "organization_id", "user_id", "toolkit"),
    )


class ChannelCheckModel(Base):
    """What we have seen a channel actually do on this deployment.

    A channel is "verified" only from traffic: an inbound message whose
    signature checked, and a delivery the platform accepted. Keys being
    present is configuration, not proof (handoff 22: "availability comes
    from verified capability flags"). Platform-wide, not per person.
    """

    __tablename__ = "channel_checks"

    channel = Column(String(16), primary_key=True)
    verified_inbound_at = Column(DateTime(timezone=True), nullable=True)
    delivery_ok_at = Column(DateTime(timezone=True), nullable=True)
    delivery_failed_at = Column(DateTime(timezone=True), nullable=True)
    failure_code = Column(String(64), nullable=True)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=_now)


class EmailIdentityModel(Base):
    """A person's address at the Decibyl domain.

    States (handoff 25): checking is the request in flight and never stored;
    ``reserved`` -> ``provisioning`` -> ``active``, with ``delivery_issue``
    and ``suspended`` beside it, and ``released`` once given up. Active only
    after a delivery to it was verified. One live address per person and one
    owner per live address, held by partial unique indexes so two requests
    for the same name cannot both win.
    """

    __tablename__ = "email_identities"

    id = Column(Integer, primary_key=True)
    user_id = Column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    #: The workspace the person asked from, for the audit trail; the address
    #: is the person's, not the workspace's.
    organization_id = Column(
        Integer, ForeignKey("organizations.id", ondelete="SET NULL"), nullable=True
    )
    alias = Column(String(64), nullable=False)
    state = Column(String(16), nullable=False)
    #: sha256 of the probe token a verifying delivery must carry.
    probe_hash = Column(String(64), nullable=True)
    issue_code = Column(String(64), nullable=True)
    reserved_at = Column(DateTime(timezone=True), nullable=False, default=_now)
    provisioning_at = Column(DateTime(timezone=True), nullable=True)
    active_at = Column(DateTime(timezone=True), nullable=True)
    suspended_at = Column(DateTime(timezone=True), nullable=True)
    released_at = Column(DateTime(timezone=True), nullable=True)
    revision = Column(Integer, nullable=False, default=1, server_default=text("1"))
    updated_at = Column(DateTime(timezone=True), nullable=False, default=_now)

    __table_args__ = (
        Index(
            "ux_email_identities_alias_live",
            "alias",
            unique=True,
            postgresql_where=text("released_at IS NULL"),
        ),
        Index(
            "ux_email_identities_user_live",
            "user_id",
            unique=True,
            postgresql_where=text("released_at IS NULL"),
        ),
    )


class EmailIdentityMessageModel(Base):
    """One inbound message to the Decibyl domain.

    ``identity_id`` set: delivered to that address's owner. NULL: nobody
    owns the recipient, and the message is quarantined, never shown to
    anyone (handoff 7: "quarantine for unknown recipients").
    """

    __tablename__ = "email_identity_messages"

    id = Column(Integer, primary_key=True)
    identity_id = Column(
        Integer, ForeignKey("email_identities.id", ondelete="CASCADE"), nullable=True
    )
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=True)
    #: delivered | quarantined | refused
    status = Column(String(16), nullable=False)
    recipient = Column(String(320), nullable=False)
    #: The provider's Message-ID, for deduplication.
    message_id = Column(String(255), nullable=False)
    #: Groups replies into one thread (from References/In-Reply-To).
    thread_key = Column(String(255), nullable=False)
    from_address = Column(String(320), nullable=True)
    subject = Column(String(500), nullable=True)
    body_text = Column(Text, nullable=True)
    size_bytes = Column(Integer, nullable=False, default=0)
    attachments = Column(JSON, nullable=False, default=list)
    reason_code = Column(String(64), nullable=True)
    received_at = Column(DateTime(timezone=True), nullable=False, default=_now)

    __table_args__ = (
        UniqueConstraint("recipient", "message_id", name="uq_email_identity_message"),
        Index("ix_email_identity_messages_owner", "user_id", "thread_key"),
    )


class EmailIdentitySendModel(Base):
    """One send from an address, written before the mail server is called,
    so a send that broke midway is still on record for reconciliation."""

    __tablename__ = "email_identity_sends"

    id = Column(Integer, primary_key=True)
    identity_id = Column(
        Integer, ForeignKey("email_identities.id", ondelete="CASCADE"), nullable=False
    )
    user_id = Column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    card_event_id = Column(Integer, nullable=False, unique=True)
    message_id = Column(String(255), nullable=False, unique=True)
    to_address = Column(String(320), nullable=False)
    #: sending | accepted | bounced | complained | failed
    state = Column(String(16), nullable=False)
    detail_code = Column(String(64), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=_now)


class CardInterestModel(Base):
    """Someone said they would like the virtual card when it exists. Interest
    only: no card details are ever collected (handoff 7)."""

    __tablename__ = "card_interest"

    user_id = Column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)


class NotificationPreferencesModel(Base):
    """How one person wants to be told things (screen 21). Theirs alone."""

    __tablename__ = "notification_preferences"

    user_id = Column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    #: {"in_app": bool, "push": bool, "email": bool, "channel": bool}
    channels = Column(JSON, nullable=False, default=dict)
    #: {topic: {"on": bool, "snoozed_until": iso | null}}
    topics = Column(JSON, nullable=False, default=dict)
    quiet_start = Column(String(5), nullable=True)
    quiet_end = Column(String(5), nullable=True)
    private_previews = Column(Boolean, nullable=False, default=True)
    revision = Column(Integer, nullable=False, default=0, server_default=text("0"))
    updated_at = Column(DateTime(timezone=True), nullable=False, default=_now)


class PushSubscriptionModel(Base):
    """One browser or device a person allowed push on."""

    __tablename__ = "push_subscriptions"

    id = Column(Integer, primary_key=True)
    user_id = Column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    endpoint = Column(Text, nullable=False, unique=True)
    p256dh = Column(String(255), nullable=False)
    auth = Column(String(255), nullable=False)
    #: "Chrome on Android" -- what the person sees in the device list.
    device_label = Column(String(80), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)
    last_success_at = Column(DateTime(timezone=True), nullable=True)
    last_failure_at = Column(DateTime(timezone=True), nullable=True)
    failure_code = Column(String(64), nullable=True)
    #: Set when the push service says the permission is gone (404/410) or
    #: the person removed it.
    revoked_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (Index("ix_push_subscriptions_user", "user_id"),)


class NotificationDeliveryModel(Base):
    """One decision about one notification on one channel: sent, or why not.
    Unique per (person, dedupe key, channel), so a retried job notifies once."""

    __tablename__ = "notification_deliveries"

    id = Column(Integer, primary_key=True)
    user_id = Column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    topic = Column(String(32), nullable=False)
    channel = Column(String(16), nullable=False)
    dedupe_key = Column(String(128), nullable=False)
    #: sent | partial | failed | off | quiet | capped | snoozed | needs_setup
    outcome = Column(String(16), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)

    __table_args__ = (
        UniqueConstraint(
            "user_id", "dedupe_key", "channel", name="uq_notification_delivery"
        ),
        Index("ix_notification_deliveries_day", "user_id", "topic", "created_at"),
    )


class DeliveryReceiptModel(Base):
    """What a provider told us about one message we sent (a WhatsApp status
    webhook, a mail bounce). The evidence reconciliation reads."""

    __tablename__ = "delivery_receipts"

    id = Column(Integer, primary_key=True)
    provider = Column(String(32), nullable=False)
    provider_message_id = Column(String(255), nullable=False)
    #: The card's idempotency key when the provider echoes it back
    #: (WhatsApp ``biz_opaque_callback_data``), else NULL.
    idempotency_key = Column(String(128), nullable=True)
    #: accepted | sent | delivered | read | failed | bounced | complained
    status = Column(String(16), nullable=False)
    detail_code = Column(String(64), nullable=True)
    recorded_at = Column(DateTime(timezone=True), nullable=False, default=_now)

    __table_args__ = (
        UniqueConstraint(
            "provider", "provider_message_id", "status", name="uq_delivery_receipt"
        ),
        Index("ix_delivery_receipts_key", "idempotency_key"),
    )


class NumberReadinessModel(Base):
    """Evidence that a number works before it is labelled ready (screen 24:
    "real incoming call and human escalation pass before the number is
    labelled ready")."""

    __tablename__ = "number_readiness"

    phone_number_id = Column(
        Integer,
        ForeignKey("telephony_phone_numbers.id", ondelete="CASCADE"),
        primary_key=True,
    )
    organization_id = Column(
        Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    incoming_call_ok_at = Column(DateTime(timezone=True), nullable=True)
    escalation_ok_at = Column(DateTime(timezone=True), nullable=True)
    recorded_by = Column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    updated_at = Column(DateTime(timezone=True), nullable=False, default=_now)
