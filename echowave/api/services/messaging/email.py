"""Outbound email, over plain SMTP.

There is no provider SDK here on purpose. SES, Sendgrid, Mailgun, Postmark and
a plain Workspace/Gmail account all speak SMTP, so one client reaches all of
them and switching providers is an environment change, not a deploy.

Follows the same posture as Razorpay and Google Calendar: unconfigured is a
normal, expected state (most self-hosted installs won't have set it up yet),
so a send is skipped and logged rather than raising. The one caller-visible
signal is the boolean on :class:`SendResult` -- callers that need to know
whether mail actually left (nothing today) can check it; callers that don't
(a best-effort receipt email) can ignore it.
"""

from __future__ import annotations

import asyncio
import smtplib
from dataclasses import dataclass
from email.message import EmailMessage

from loguru import logger

from api.constants import (
    EMAIL_FROM_ADDRESS,
    EMAIL_FROM_NAME,
    SMTP_HOST,
    SMTP_PASSWORD,
    SMTP_PORT,
    SMTP_USE_TLS,
    SMTP_USERNAME,
)


@dataclass(frozen=True)
class SendResult:
    ok: bool
    error: str | None = None


def email_is_configured() -> bool:
    return bool(SMTP_HOST and EMAIL_FROM_ADDRESS)


def _send_sync(
    *,
    to: str,
    subject: str,
    body_text: str,
    attachment_bytes: bytes | None,
    attachment_filename: str | None,
) -> None:
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = f"{EMAIL_FROM_NAME} <{EMAIL_FROM_ADDRESS}>"
    message["To"] = to
    message.set_content(body_text)

    if attachment_bytes is not None:
        message.add_attachment(
            attachment_bytes,
            maintype="application",
            subtype="pdf",
            filename=attachment_filename or "document.pdf",
        )

    if SMTP_USE_TLS and SMTP_PORT == 465:
        smtp_cls = smtplib.SMTP_SSL
    else:
        smtp_cls = smtplib.SMTP

    with smtp_cls(SMTP_HOST, SMTP_PORT, timeout=15) as client:
        if SMTP_USE_TLS and SMTP_PORT != 465:
            client.starttls()
        if SMTP_USERNAME and SMTP_PASSWORD:
            client.login(SMTP_USERNAME, SMTP_PASSWORD)
        client.send_message(message)


async def send_email(
    *,
    to: str,
    subject: str,
    body_text: str,
    attachment_bytes: bytes | None = None,
    attachment_filename: str | None = None,
) -> SendResult:
    """Send one email, best-effort.

    Never raises: a failed send is a fact to log and report, not an exception
    that should unwind whatever triggered it -- the underlying event (a
    payment, an invoice) already happened and must not be undone by a mail
    server timeout.
    """
    if not email_is_configured():
        logger.warning(
            "Email to {} not sent: SMTP_HOST/EMAIL_FROM_ADDRESS are not set.", to
        )
        return SendResult(ok=False, error="Email is not configured.")

    try:
        await asyncio.to_thread(
            _send_sync,
            to=to,
            subject=subject,
            body_text=body_text,
            attachment_bytes=attachment_bytes,
            attachment_filename=attachment_filename,
        )
    except Exception as exc:  # noqa: BLE001 -- reported, not propagated; see docstring
        logger.error("Failed to send email to {}: {}", to, exc)
        return SendResult(ok=False, error=str(exc))

    return SendResult(ok=True)
