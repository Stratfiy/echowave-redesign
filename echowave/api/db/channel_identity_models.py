"""Who is writing, from which app (DCH-1, KAN-277).

``channel_identities`` ties an external identity -- a WhatsApp number, a
Telegram chat, a Slack user in a workspace, a Teams user in a tenant -- to
one member of one organisation. Decibyl answers that member, as that member
(their Gmail, their memory), and a card tapped from the app is settled by
that member.

``channel_link_codes`` is the one-time code that creates the row: made in
Settings by the signed-in member, sent once from the app, gone after ten
minutes or one use. Only the hash is stored.
"""

from datetime import UTC, datetime

from sqlalchemy import JSON, Column, DateTime, ForeignKey, Integer, String, UniqueConstraint

from api.db.models import Base


class ChannelIdentityModel(Base):
    __tablename__ = "channel_identities"

    id = Column(Integer, primary_key=True)
    organization_id = Column(
        Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    #: whatsapp | telegram | slack | teams
    channel = Column(String(16), nullable=False)
    #: +E164 | Telegram chat id | "T123:U456" | "tenant:aadObjectId"
    external_id = Column(String(255), nullable=False)
    display_name = Column(String(200), nullable=True)
    #: What sending back needs: Slack channel + team, Teams serviceUrl +
    #: conversation id. Nothing secret.
    conversation_ref = Column(JSON, nullable=True)
    verified_at = Column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(UTC))

    __table_args__ = (
        UniqueConstraint("channel", "external_id", name="uq_channel_identity"),
    )


class ChannelLinkCodeModel(Base):
    __tablename__ = "channel_link_codes"

    id = Column(Integer, primary_key=True)
    organization_id = Column(
        Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    channel = Column(String(16), nullable=False)
    code_hash = Column(String(64), nullable=False, unique=True, index=True)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    used_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(UTC))


class SlackInstallationModel(Base):
    """The Decibyl Slack app installed in one Slack workspace, for one
    organisation. The bot token is Fernet-encrypted with the same secret as
    provider keys (``PLATFORM_CREDENTIAL_SECRET``)."""

    __tablename__ = "slack_installations"

    id = Column(Integer, primary_key=True)
    organization_id = Column(
        Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    team_id = Column(String(32), nullable=False, unique=True, index=True)
    team_name = Column(String(200), nullable=True)
    bot_user_id = Column(String(32), nullable=True)
    encrypted_bot_token = Column(String(512), nullable=False)
    installed_by_user_id = Column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
