"""Devices running the native app that a person allowed push on (MOBILE.md).

One row per Expo push token. The token names one install of the app on one
phone; it is not a secret (it only lets Expo deliver to that install), but
it is the person's, so it is only ever read and written for its owner.
"""

from datetime import UTC, datetime

from sqlalchemy import Column, DateTime, ForeignKey, Index, Integer, String

from api.db.models import Base


def _now() -> datetime:
    return datetime.now(UTC)


class MobilePushTokenModel(Base):
    __tablename__ = "mobile_push_tokens"

    id = Column(Integer, primary_key=True)
    user_id = Column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    #: The workspace the app was signed in to when it registered. Pushes are
    #: sent while ``mobile_push`` is on for this workspace.
    organization_id = Column(
        Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    #: ``ExponentPushToken[...]``. Unique: one install is one person's at a
    #: time, so signing in as someone else on the same phone moves it.
    token = Column(String(255), nullable=False, unique=True)
    platform = Column(String(16), nullable=False)
    #: "Pixel 8" -- what the person sees in their device list.
    device_label = Column(String(80), nullable=True)
    app_version = Column(String(32), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)
    last_seen_at = Column(DateTime(timezone=True), nullable=False, default=_now)
    last_success_at = Column(DateTime(timezone=True), nullable=True)
    last_failure_at = Column(DateTime(timezone=True), nullable=True)
    failure_code = Column(String(64), nullable=True)
    #: Set when the person signed out on that phone, removed it, or Expo said
    #: the install is gone (``DeviceNotRegistered``).
    revoked_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (Index("ix_mobile_push_tokens_user", "user_id"),)
