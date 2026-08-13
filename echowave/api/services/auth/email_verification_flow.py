"""Issuing a verification code and getting it into an inbox.

Thin on purpose. It exists so routes do not have to know both the rate-limit
rules and the mail transport, and so the "never fail the caller" policy lives
in one place rather than being remembered at each call site.
"""

from __future__ import annotations

from loguru import logger

from api.services.auth import email_verification
from api.services.messaging.email import email_is_configured, send_email


async def issue_code(user_id: int, email: str) -> bool:
    """Issue a code and mail it. Returns whether it was sent.

    **Never raises.** Both callers — signup and an explicit resend — have
    already done the thing that matters, and a mail server timeout must not
    unwind an account that now exists. A rate-limit refusal is likewise not an
    error here: asking twice quickly is a normal thing for a person to do.
    """
    if not email_is_configured():
        logger.warning(
            "Email verification not sent for user {}: SMTP is not configured.",
            user_id,
        )
        return False

    try:
        started = await email_verification.start_verification(user_id, email)
    except email_verification.EmailVerificationError as exc:
        logger.info(f"No verification code issued for user {user_id}: {exc.reason}")
        return False

    result = await send_email(
        to=started.email,
        subject=email_verification.subject(),
        body_text=email_verification.body(started.code),
    )
    if not result.ok:
        logger.error(
            "Verification email to user {} failed: {}", user_id, result.error
        )
    return result.ok
