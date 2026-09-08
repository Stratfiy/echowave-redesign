"""Password recovery using hashed, expiring tokens and atomic consumption."""

from __future__ import annotations

import asyncio
import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from urllib.parse import quote

from loguru import logger

from api.constants import UI_APP_URL
from api.services.messaging.email import email_is_configured, send_email
from api.utils.auth import hash_password

GENERIC_MESSAGE = (
    "If an account exists for this address, a reset link will arrive shortly."
)
TTL_MINUTES = 30


def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


async def issue_link(
    email: str, *, db=None, sender=send_email, now: datetime | None = None
) -> None:
    # Run after the generic HTTP response, so SMTP timing does not reveal accounts.
    if not email_is_configured():
        return
    try:
        if db is None:
            from api.db import db_client

            db = db_client
        user = await db.get_user_by_email(email.strip().lower())
        if not user:
            return
        moment = now or datetime.now(UTC)
        token = secrets.token_urlsafe(32)
        issued = await db.issue_password_reset(
            user.id,
            token_hash=token_digest(token),
            expires_at=moment + timedelta(minutes=TTL_MINUTES),
            now=moment,
        )
        if not issued:
            return
        # Fragment keeps the bearer token out of HTTP request URLs and referrers.
        link = f"{UI_APP_URL.rstrip('/')}/auth/reset-password#token={quote(token)}"
        result = await sender(
            to=user.email,
            subject="Reset your Decibyl password",
            body_text=f"Use this link to choose a new password:\n\n{link}\n\nThe link expires in {TTL_MINUTES} minutes and can be used once. Your existing browser sessions will be signed out after the reset. Two-factor authentication stays enabled.\n\nIf you did not request this, ignore this email. Your password has not changed.",
        )
        if not result.ok:
            logger.error("Password reset email delivery failed for user {}", user.id)
    except Exception:
        # Do not log tokens, reset URLs, recipient addresses or transport payloads.
        logger.error("Password reset delivery could not be completed")


async def reset_password(
    token: str, password: str, *, db=None, now: datetime | None = None
) -> bool:
    if db is None:
        from api.db import db_client

        db = db_client
    moment = now or datetime.now(UTC)
    digest = token_digest(token)
    if not await db.has_password_reset(token_hash=digest, now=moment):
        return False
    hashed = await asyncio.to_thread(hash_password, password)
    return await db.consume_password_reset(
        token_hash=digest, password_hash=hashed, now=moment
    )
