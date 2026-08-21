"""Scheduled billing of recurring charges, and nightly number reconciliation.

Both run daily rather than monthly. A monthly job that fails has a month of
silence before anyone notices; a daily job that finds nothing due does almost
no work, and the one that finds a missed month bills it on the next tick. The
period arithmetic in ``rentals`` is what makes running daily correct — it bills
by period boundary, not by "today is the first".
"""

from loguru import logger

from api.services.billing import rentals
from api.services.telephony import number_lifecycle


async def charge_recurring_rentals(_ctx) -> None:
    """Collect every rental period that has come due.

    Idempotent by construction: a period already billed collides on
    ``uq_recurring_charge_period`` and is skipped. Running this twice in a day,
    or re-running it over a day it already processed, charges nobody twice.
    """
    counters = await rentals.run_due_charges()
    if counters["failed"]:
        logger.warning(
            "{} rental charge(s) could not be collected. Accounts are moving "
            "through the dunning schedule; see services/billing/dunning.py",
            counters["failed"],
        )


async def reconcile_carrier_numbers(_ctx) -> None:
    """Diff our number inventory against the carrier's, and report drift.

    Reports only — it does not release or purchase anything. Both directions of
    drift can be caused by a partial failure elsewhere, and a job that
    automatically "corrected" them could release a number a customer is using
    because a carrier API call timed out.
    """
    reports = await number_lifecycle.reconcile_all()

    orphaned = [n for r in reports for n in r.at_carrier_only]
    missing = [n for r in reports for n in r.in_database_only]

    if orphaned:
        logger.error(
            "RECONCILIATION: {} number(s) are rented at the carrier with no "
            "active record here — we are paying for them and billing nobody: "
            "{}",
            len(orphaned),
            ", ".join(sorted(orphaned)[:20]),
        )
    if missing:
        logger.error(
            "RECONCILIATION: {} number(s) are active here but not at the "
            "carrier — inbound calls to them will fail: {}",
            len(missing),
            ", ".join(sorted(missing)[:20]),
        )

    failed = [r for r in reports if r.error]
    if failed:
        logger.warning(
            "RECONCILIATION: {} carrier account(s) could not be read; their "
            "numbers were not checked this run",
            len(failed),
        )

    if reports and not orphaned and not missing and not failed:
        logger.info(
            "RECONCILIATION: {} carrier account(s) agree with our inventory",
            len(reports),
        )


async def notify_dunning(
    *,
    organization_id: int,
    charge_id: int,
    state,
    phone_number: str | None,
    amount_paise: int,
) -> bool:
    """Tell the account that a rental could not be collected.

    Claimed before the send, exactly as the low-balance job does: the other
    order sends twice when two workers race, and this job is retried daily.
    The dedupe key is the charge and the state it reached, so a retry that
    changes nothing sends nothing and crossing into suspension sends one.

    Never raises. The dunning ladder has already been applied and committed by
    the caller; failing the whole rental run because SMTP was down would leave
    a suspension in place with the next tick finding nothing left to do.
    """
    from sqlalchemy.exc import IntegrityError

    from api.constants import UI_APP_URL
    from api.db import db_client
    from api.db.models import NotificationModel
    from api.services.billing import dunning_email
    from api.services.messaging import email

    try:
        notice = dunning_email.compose(
            state=state,
            charge_id=charge_id,
            account_name="your account",
            phone_number=phone_number,
            amount_paise=amount_paise,
            top_up_url=f"{(UI_APP_URL or '').rstrip('/')}/billing",
        )
        if notice is None:
            return False

        recipients = await _dunning_recipients(organization_id=organization_id)
        if not recipients:
            logger.debug(
                "Rental charge {} needs a dunning notice but org {} has no "
                "email address on file",
                charge_id,
                organization_id,
            )
            return False

        async with db_client.async_session() as session:
            record = NotificationModel(
                organization_id=organization_id,
                kind="dunning",
                dedupe_key=notice.dedupe_key,
                channel="email",
                recipients=", ".join(recipients),
                subject=notice.subject,
            )
            session.add(record)
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()
                return False

        results = [
            await email.send_email(
                sender="billing",
                to=address,
                subject=notice.subject,
                body_text=notice.body,
            )
            for address in recipients
        ]
        sent = any(result.ok for result in results)

        async with db_client.async_session() as session:
            stored = await session.get(NotificationModel, record.id)
            if stored is not None:
                stored.sent = sent
                if not sent:
                    stored.error = "SMTP send failed; see worker logs"
                await session.commit()
        return sent
    except Exception as exc:  # noqa: BLE001 — see docstring
        logger.error(
            "Could not send the dunning notice for charge {}: {}", charge_id, exc
        )
        return False


async def _dunning_recipients(*, organization_id: int) -> list[str]:
    """Every member's address, deduplicated.

    Through ``db_client`` rather than a join written here, for the same reason
    the low-balance job does it that way: organization membership has already
    moved once, and a second hand-rolled join is a second place to update.
    """
    from api.db import db_client

    members = await db_client.get_organization_users(organization_id)
    return sorted(
        {(m.email or "").strip().lower() for m in members if (m.email or "").strip()}
    )
