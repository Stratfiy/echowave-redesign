"""Issuing a reset code and getting it into an inbox.

The route behind this has one answer for every address, so the flow keeps
every outcome to itself: it logs, it never raises, and it returns nothing the
route could leak.
"""

from __future__ import annotations

from loguru import logger

from api.services.auth import password_reset
from api.services.auth.email_verification import EmailVerificationError
from api.services.messaging.email import email_is_configured, send_email


async def issue_reset_code(email: str) -> None:
    if not email_is_configured():
        logger.warning("Password reset not sent: SMTP is not configured.")
        return
    try:
        started = await password_reset.start_reset(email)
    except EmailVerificationError as exc:
        logger.info(f"No password reset code issued: {exc.reason}")
        return
    if started is None:
        # No account. Say nothing, do nothing: the route's answer is the same.
        return
    if isinstance(started, password_reset.NoPasswordToReset):
        result = await send_email(
            to=started.email,
            subject=password_reset.subject(),
            body_text=password_reset.google_only_body(),
        )
    else:
        result = await send_email(
            to=started.email,
            subject=password_reset.subject(),
            body_text=password_reset.body(started.code),
        )
    if not result.ok:
        logger.error("Password reset email failed: {}", result.error)
