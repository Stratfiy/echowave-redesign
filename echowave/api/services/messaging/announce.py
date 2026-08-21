"""Send one notice to an account, once, whatever races or retries happen.

Three call sites had already written this sequence out by hand — the
low-balance job, the dunning notice, and the tax-document mailer — and the
sequence is subtle in exactly one place, which is why it is worth having once:

**The row is claimed before the send, not after.** The other order sends twice
whenever two workers touch the same account in the same tick, and every one of
these callers is a job that is safe to re-run by design. The unique constraint
on ``(organization_id, kind, dedupe_key)`` is what makes the claim atomic; an
``IntegrityError`` means somebody else already has it, and losing that race is
a success — the notice is going out, just not from here.

The cost of that order is a notice recorded as claimed whose send then fails.
That is the right way round: a missing email about a thing that happened is
recoverable, and a duplicate email about money never is — a customer who reads
"your number is suspended" twice has to work out whether it happened twice.

**Never raises.** Every caller has already done the thing being announced. A
mail server having a bad minute must not unwind a provisioned account, a
collected payment or an applied dunning step.
"""

from __future__ import annotations

from dataclasses import dataclass

from loguru import logger
from sqlalchemy.exc import IntegrityError

from api.db import db_client
from api.db.models import NotificationModel
from api.services.messaging import email


@dataclass(frozen=True)
class Notice:
    """One thing to say to an account, and what makes it the same thing twice."""

    subject: str
    body: str
    #: Unique per notice within its kind. Two composes that produce the same
    #: key are the same notice and only one is sent.
    dedupe_key: str


async def recipients_for(organization_id: int) -> list[str]:
    """Every member's address, deduplicated and lowercased.

    Through ``db_client`` rather than a join written here: organization
    membership has already moved once, and a hand-rolled join is another place
    to update when it moves again.
    """
    members = await db_client.get_organization_users(organization_id)
    return sorted(
        {(m.email or "").strip().lower() for m in members if (m.email or "").strip()}
    )


async def announce(
    *,
    organization_id: int,
    kind: str,
    notice: Notice,
    sender: str = "notifications",
    to: list[str] | None = None,
) -> bool:
    """Send ``notice`` to this account. Returns whether anything went out.

    ``to`` overrides the recipient list for a notice addressed to one person
    rather than to the account — a welcome, which belongs to whoever just
    signed up and not yet to colleagues they have not invited.

    One message per recipient rather than one with several addresses: the
    shared sender takes a single ``to``, and a billing notice has no reason to
    publish one member's address to another.
    """
    try:
        addresses = to if to is not None else await recipients_for(organization_id)
        addresses = [a for a in (addr.strip() for addr in addresses) if a]
        if not addresses:
            logger.debug(
                "No {} notice sent: organization {} has no email address on file",
                kind,
                organization_id,
            )
            return False

        async with db_client.async_session() as session:
            record = NotificationModel(
                organization_id=organization_id,
                kind=kind,
                dedupe_key=notice.dedupe_key,
                channel="email",
                recipients=", ".join(addresses),
                subject=notice.subject,
            )
            session.add(record)
            try:
                await session.commit()
            except IntegrityError:
                # Somebody else has it. Losing this race is a success.
                await session.rollback()
                return False

        results = [
            await email.send_email(
                sender=sender,
                to=address,
                subject=notice.subject,
                body_text=notice.body,
            )
            for address in addresses
        ]
        sent = any(result.ok for result in results)
        failure = next(
            (r.error for r in results if not r.ok and r.error), None
        )

        async with db_client.async_session() as session:
            stored = await session.get(NotificationModel, record.id)
            if stored is not None:
                stored.sent = sent
                if not sent:
                    stored.error = failure or "SMTP send failed; see worker logs"
                await session.commit()
        return sent
    except Exception as exc:  # noqa: BLE001 — reported, never propagated
        logger.error(
            "Could not announce {} to organization {}: {}", kind, organization_id, exc
        )
        return False
