"""Outbound email, for the operational notices a customer cannot get otherwise.

Deliberately small. This is not a marketing channel and it is not a queue — it
sends one message to a handful of recipients over SMTP and reports whether that
worked. Everything richer (templates, tracking, unsubscribe management) belongs
to a provider we have not chosen yet, and building a half-version of it here
would be the thing that makes choosing one hard later.

Two properties matter more than the feature set:

**Email being unconfigured is never an error.** :func:`send` returns ``False``
rather than raising when there is no SMTP host, because every caller is a
billing job whose real work is billing. A monthly rental must not fail because
a notice could not be sent — that trades a courtesy for the thing the courtesy
was about.

**Sending is off the event loop.** ``smtplib`` is blocking and a slow or dead
SMTP server hangs for the length of its timeout. Run inline, that stalls the
worker that is trying to bill everybody else, so every send goes through
``asyncio.to_thread``. The standard library is used rather than an async SMTP
package on purpose: one fewer dependency in the path that has to work on a box
we cannot easily test against.
"""

from __future__ import annotations

import asyncio
import smtplib
from email.message import EmailMessage
from email.utils import formataddr

from loguru import logger

from api.constants import (
    EMAIL_FROM,
    EMAIL_FROM_NAME,
    EMAIL_REPLY_TO,
    SMTP_HOST,
    SMTP_PASSWORD,
    SMTP_PORT,
    SMTP_STARTTLS,
    SMTP_USE_TLS,
    SMTP_USERNAME,
)

#: How long to wait on the SMTP conversation. Short, because this runs inside a
#: loop over accounts: a two-minute default timeout multiplied by every account
#: on a dead server is a job that never finishes.
_TIMEOUT_SECONDS = 15


def is_configured() -> bool:
    return bool(SMTP_HOST)


def _build(
    *, to: list[str], subject: str, text: str, html: str | None = None
) -> EmailMessage:
    message = EmailMessage()
    message["From"] = formataddr((EMAIL_FROM_NAME, EMAIL_FROM))
    # Recipients go in Bcc rather than To when there are several: the members of
    # one organization are colleagues, but their addresses are still personal
    # data and there is no reason for a billing notice to publish them to each
    # other.
    if len(to) == 1:
        message["To"] = to[0]
    else:
        message["To"] = formataddr((EMAIL_FROM_NAME, EMAIL_FROM))
        message["Bcc"] = ", ".join(to)
    if EMAIL_REPLY_TO:
        message["Reply-To"] = EMAIL_REPLY_TO
    message["Subject"] = subject
    message.set_content(text)
    if html:
        message.add_alternative(html, subtype="html")
    return message


def _send_sync(message: EmailMessage, recipients: list[str]) -> None:
    if SMTP_USE_TLS:
        server: smtplib.SMTP = smtplib.SMTP_SSL(
            SMTP_HOST, SMTP_PORT, timeout=_TIMEOUT_SECONDS
        )
    else:
        server = smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=_TIMEOUT_SECONDS)
    try:
        if SMTP_STARTTLS and not SMTP_USE_TLS:
            server.starttls()
        if SMTP_USERNAME and SMTP_PASSWORD:
            server.login(SMTP_USERNAME, SMTP_PASSWORD)
        # Recipients passed explicitly rather than read off the headers, so the
        # Bcc list above is actually delivered to — send_message() would drop it.
        server.send_message(message, to_addrs=recipients)
    finally:
        try:
            server.quit()
        except Exception:  # pragma: no cover - a failed quit is not a failed send
            pass


async def send(
    *, to: list[str], subject: str, text: str, html: str | None = None
) -> bool:
    """Send one message. Returns whether it went.

    Never raises. A caller deciding what to do about a failed notice is a
    caller that has stopped doing its actual job.
    """
    recipients = [address.strip() for address in to if address and address.strip()]
    if not recipients:
        return False
    if not is_configured():
        logger.debug("Email not configured; dropping {!r} to {}", subject, recipients)
        return False

    message = _build(to=recipients, subject=subject, text=text, html=html)
    try:
        await asyncio.to_thread(_send_sync, message, recipients)
    except Exception as exc:
        logger.warning(
            "Could not send {!r} to {}: {}", subject, ", ".join(recipients), exc
        )
        return False

    logger.info("Sent {!r} to {} recipient(s)", subject, len(recipients))
    return True
