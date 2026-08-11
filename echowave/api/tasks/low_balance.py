"""Tell customers their credit is running out, before it does.

The dunning schedule suspends a number seven days after a rental it cannot
collect. That is the right policy and it is silent, which makes it the wrong
experience: the money was almost always there, the customer simply did not know
it was needed.

Two properties this job has to have, both of which are about *not* being
annoying:

**One notice per account per day per severity.** The claim on
``notifications`` is made before the send, not after, so two workers racing
produce one email. A retried job finds the row and does nothing.

**Silence when nothing has changed.** Only accounts that have actually been
spending are considered, so a dormant account with ₹0 in it is not emailed
every morning for ever.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from loguru import logger
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from api.constants import UI_APP_URL
from api.db import db_client
from api.db.models import (
    DailyOrganizationRollupModel,
    NotificationModel,
    OrganizationModel,
    RecurringChargeModel,
    UserModel,
    organization_users_association,
)
from api.enums import RecurringChargeStatus
from api.services.billing import low_balance
from api.services.billing.costing import current_balance_paise
from api.services.notifications import email

#: How much history the burn rate is averaged over. Long enough that one busy
#: Tuesday does not produce a panic email, short enough to notice a campaign
#: that started last week.
BURN_WINDOW_DAYS = 7


async def _spending_accounts(session, *, since: date) -> list[int]:
    """Accounts that have spent anything in the window.

    From the rollup rather than from workflow_runs: this runs over every
    account every day, and it must stay a small aggregate query rather than a
    scan that grows with call volume.
    """
    rows = await session.execute(
        select(DailyOrganizationRollupModel.organization_id)
        .where(
            DailyOrganizationRollupModel.day >= since,
            DailyOrganizationRollupModel.charged_paise > 0,
        )
        .group_by(DailyOrganizationRollupModel.organization_id)
    )
    return [row[0] for row in rows]


async def _daily_burn_paise(session, *, organization_id: int, since: date) -> int:
    """Average daily spend across the whole window, quiet days included.

    Averaging only over days with traffic is optimistic in the direction that
    hurts: an account that calls on weekdays still runs out on a Sunday.
    """
    total = await session.scalar(
        select(func.coalesce(func.sum(DailyOrganizationRollupModel.charged_paise), 0))
        .where(
            DailyOrganizationRollupModel.organization_id == organization_id,
            DailyOrganizationRollupModel.day >= since,
        )
    )
    return int(total or 0) // BURN_WINDOW_DAYS


async def _recipients(session, *, organization_id: int) -> list[str]:
    rows = await session.execute(
        select(UserModel.email)
        .join(
            organization_users_association,
            organization_users_association.c.user_id == UserModel.id,
        )
        .where(
            organization_users_association.c.organization_id == organization_id,
            UserModel.email.isnot(None),
        )
    )
    # Deduplicated and lowercased: the same person can appear twice through two
    # provider identities, and sending them the same warning twice is exactly
    # the thing this job is trying not to do.
    return sorted({(row[0] or "").strip().lower() for row in rows if row[0]})


async def _has_numbers(session, *, organization_id: int) -> bool:
    return bool(
        await session.scalar(
            select(RecurringChargeModel.id)
            .where(
                RecurringChargeModel.organization_id == organization_id,
                RecurringChargeModel.status.notin_(
                    [
                        RecurringChargeStatus.RELEASED.value,
                        RecurringChargeStatus.CANCELLED.value,
                    ]
                ),
            )
            .limit(1)
        )
    )


async def notify_low_balances(ctx=None, *, now: datetime | None = None) -> dict:
    """Warn every spending account whose credit is about to run out.

    Safe to run repeatedly: the dedupe row is claimed before the send.
    """
    now = now or datetime.now(UTC)
    today = now.date()
    since = today - timedelta(days=BURN_WINDOW_DAYS - 1)

    counters = {"considered": 0, "warned": 0, "skipped": 0, "failed": 0}

    if not email.is_configured():
        # Not an error, and not silent either. A deployment with no SMTP host
        # has a dunning schedule that suspends numbers without warning, and
        # that is worth one log line a day.
        logger.info("Low-balance notices skipped: no SMTP host configured")
        return counters

    async with db_client.async_session() as session:
        organization_ids = await _spending_accounts(session, since=since)

    counters["considered"] = len(organization_ids)

    for organization_id in organization_ids:
        try:
            await _notify_one(organization_id, today=today, since=since, counters=counters)
        except Exception:
            # One account must not stop the rest. A missing email address or a
            # deleted organization is the normal case here.
            logger.exception(
                "Low-balance check failed for organization {}", organization_id
            )
            counters["failed"] += 1

    logger.info(
        "Low balance: {considered} considered, {warned} warned, {skipped} skipped, "
        "{failed} failed",
        **counters,
    )
    return counters


async def _notify_one(organization_id: int, *, today: date, since: date, counters: dict):
    async with db_client.async_session() as session:
        balance = await current_balance_paise(session, organization_id=organization_id)
        burn = await _daily_burn_paise(
            session, organization_id=organization_id, since=since
        )
        assessment = low_balance.assess(
            balance_paise=balance, daily_burn_paise=burn
        )
        if not assessment.should_warn:
            counters["skipped"] += 1
            return

        recipients = await _recipients(session, organization_id=organization_id)
        if not recipients:
            logger.debug(
                "Organization {} needs a low-balance warning but has no email "
                "address on file",
                organization_id,
            )
            counters["skipped"] += 1
            return

        key = low_balance.dedupe_key(assessment.level, today)
        organization = await session.get(OrganizationModel, organization_id)
        account_name = (
            getattr(organization, "billing_name", None)
            or getattr(organization, "provider_id", None)
            or "Your account"
        )
        has_numbers = await _has_numbers(session, organization_id=organization_id)

        subject, body = low_balance.compose(
            assessment=assessment,
            account_name=account_name,
            top_up_url=f"{UI_APP_URL.rstrip('/')}/billing",
            has_numbers=has_numbers,
        )

        # Claimed before the send. The other order — send, then record — sends
        # twice when two workers race, and the whole reason this table exists is
        # that this job is safe to re-run.
        record = NotificationModel(
            organization_id=organization_id,
            kind=low_balance.KIND,
            dedupe_key=key,
            channel="email",
            recipients=", ".join(recipients),
            subject=subject,
        )
        session.add(record)
        try:
            await session.commit()
        except IntegrityError:
            await session.rollback()
            counters["skipped"] += 1
            return

    sent = await email.send(to=recipients, subject=subject, text=body)

    async with db_client.async_session() as session:
        stored = await session.get(NotificationModel, record.id)
        if stored is not None:
            stored.sent = sent
            if not sent:
                stored.error = "SMTP send failed; see worker logs"
            await session.commit()

    if sent:
        counters["warned"] += 1
    else:
        counters["failed"] += 1
