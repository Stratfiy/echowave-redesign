"""Who hears about it, and the one gate every mail passes through.

Recipients are ``OPS_ALERT_EMAILS``, or every superadmin's address when that
is unset -- the same fallback, and the same warning, as the invite approvers
(``services/auth/invite_requests.approver_emails``): an alert nobody is sent
is an alert silently lost.

Every subject and body is run through ``ops.redaction.redact_text`` on the
way out, whatever composed it: secrets, email addresses and phone numbers
are scrubbed (a number becomes its last four digits). Composers are written
not to include any of those; this is the backstop, not the plan.
"""

from __future__ import annotations

from loguru import logger

from api import constants
from api.services.messaging import email
from api.services.ops.redaction import redact_text


async def operator_emails() -> list[str]:
    configured = list(dict.fromkeys(constants.OPS_ALERT_EMAILS))
    if configured:
        return configured
    from api.services.billing.uncosted_alert import superadmin_addresses

    fallback = await superadmin_addresses()
    logger.warning(
        "OPS_ALERT_EMAILS is not set; operational alerts go to the {} superadmin "
        "address(es) instead.",
        len(fallback),
    )
    if not fallback:
        logger.error(
            "Nobody to send operational alerts to: OPS_ALERT_EMAILS is unset and "
            "no superadmin has an email address."
        )
    return fallback


def scrub(text: str) -> str:
    return redact_text(text) or ""


async def send(subject: str, body: str) -> bool:
    """Mail every operator. True if at least one send went out. Never raises."""
    try:
        recipients = await operator_emails()
        if not recipients:
            return False
        if not email.email_is_configured():
            logger.warning(
                "Operational alert not sent, SMTP is not configured: {}", scrub(subject)
            )
            return False
        subject, body = scrub(subject), scrub(body)
        results = [
            await email.send_email(
                sender="notifications", to=address, subject=subject, body_text=body
            )
            for address in recipients
        ]
        return any(r.ok for r in results)
    except Exception as exc:  # noqa: BLE001 - an alert must not take its caller down
        logger.error("Could not send an operational alert: {}", type(exc).__name__)
        return False
