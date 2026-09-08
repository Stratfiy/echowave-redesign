"""Top accounts up before they run dry, and charge nothing by surprise.

Runs twice a day rather than once, and that is the whole scheduling design: a
debit may not run until a clear day after the customer was told, so a job that
only ran daily would either notify and charge 24 hours later on the dot — with
no slack for a late run — or drift a full day every time. Two passes means the
charge lands within a few hours of becoming due.

Each pass does the same thing for every account: ask the engine, and act on
exactly what it says. Nothing here decides anything; that is deliberate, so the
rules stay in one tested place rather than being half in a worker.

Failures are per account. One account with a dead card must not stop the sweep
for everybody else, so each is committed on its own.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from loguru import logger
from sqlalchemy import func, select

from api.db import db_client
from api.db.models import DailyOrganizationRollupModel, OrganizationModel
from api.services.billing import auto_topup, auto_topup_runner, payments
from api.services.billing.costing import current_balance_paise

#: Matches `low_balance`, so an account is never warned about a balance that was
#: about to be topped up on a different reading of the same spending.
BURN_WINDOW_DAYS = 7


async def _daily_burn_paise(session, *, organization_id: int, since: date) -> int:
    """Average daily spend, quiet days included — as `low_balance` computes it.

    Averaging only over days with traffic is optimistic in the direction that
    hurts: an account that calls on weekdays still runs out on a Sunday.
    """
    total = await session.scalar(
        select(
            func.coalesce(func.sum(DailyOrganizationRollupModel.charged_paise), 0)
        ).where(
            DailyOrganizationRollupModel.organization_id == organization_id,
            DailyOrganizationRollupModel.day >= since,
        )
    )
    return int(total or 0) // BURN_WINDOW_DAYS


async def _enabled_accounts(session) -> list[int]:
    """Only accounts that asked for this.

    Driven from the settings table rather than from spending, unlike the
    low-balance warning: a customer who switched auto top-up on and then went
    quiet still wants their balance kept up, and an account that never enabled
    it must never be considered at all.
    """
    from api.db.models import AutoTopupSettingModel

    rows = await session.execute(
        select(AutoTopupSettingModel.organization_id).where(
            AutoTopupSettingModel.enabled.is_(True),
            AutoTopupSettingModel.paused_reason.is_(None),
        )
    )
    return [row[0] for row in rows]


async def _recipients(*, organization_id: int) -> list[str]:
    members = await db_client.get_organization_users(organization_id)
    return sorted(
        {(m.email or "").strip().lower() for m in members if (m.email or "").strip()}
    )


async def _sweep_one(session, *, organization_id: int, now: datetime) -> str:
    """One account, start to finish. Returns what happened, for the log."""
    org = await session.scalar(
        select(OrganizationModel).where(OrganizationModel.id == organization_id)
    )
    if org is None:
        return "gone"

    row = await auto_topup_runner.load_settings(
        session, organization_id=organization_id
    )
    settings = auto_topup_runner.as_settings(row)
    history = await auto_topup_runner.load_history(
        session, organization_id=organization_id, now=now
    )

    since = (now - timedelta(days=BURN_WINDOW_DAYS)).date()
    balance = await current_balance_paise(session, organization_id=organization_id)
    burn = await _daily_burn_paise(
        session, organization_id=organization_id, since=since
    )
    token = await payments.active_token(session, organization_id=organization_id)
    account = auto_topup.Account(
        balance_paise=balance,
        daily_burn_paise=burn,
        mandate_max_paise=token.max_amount_paise if token else None,
        mandate_is_live=token is not None,
    )

    decision = auto_topup.decide(
        settings=settings, account=account, history=history, now=now
    )

    if decision.action == auto_topup.SKIP:
        return f"skip: {decision.reason}"

    if decision.action == auto_topup.SCHEDULE:
        attempt = await auto_topup_runner.schedule(
            session,
            org=org,
            decision=decision,
            recipients=await _recipients(organization_id=organization_id),
        )
        return "scheduled" if attempt is not None else "skip: raced"

    # CHARGE. The notice period has elapsed on an attempt already recorded.
    attempt = await auto_topup_runner.pending_attempt(
        session, organization_id=organization_id
    )
    if attempt is None:
        # The engine said charge because history carried a notified_at, so an
        # attempt must exist. If it does not, something removed it mid-sweep and
        # charging without a row would be a debit no guard can see.
        return "skip: no attempt row to charge against"

    try:
        payment_id = await auto_topup_runner.execute(session, org=org, attempt=attempt)
    except Exception as exc:  # noqa: BLE001 - recorded on the attempt, not raised
        return f"failed: {exc}"
    return f"charged: {payment_id}"


async def sweep_auto_topups(_ctx) -> None:
    """Consider every account that has auto top-up switched on.

    Each account is committed separately. One dead card must not roll back the
    top-ups that worked, and must not stop the accounts after it in the list.
    """
    now = datetime.now(UTC)

    async with db_client.async_session() as session:
        organization_ids = await _enabled_accounts(session)

    if not organization_ids:
        return

    charged = 0
    scheduled = 0
    for organization_id in organization_ids:
        try:
            async with db_client.async_session() as session:
                outcome = await _sweep_one(
                    session, organization_id=organization_id, now=now
                )
                await session.commit()
        except Exception as exc:  # noqa: BLE001 - one account must not stop the rest
            logger.error(
                "Auto top-up sweep failed for org {}: {}", organization_id, exc
            )
            continue

        if outcome.startswith("charged"):
            charged += 1
        elif outcome == "scheduled":
            scheduled += 1
        if not outcome.startswith("skip"):
            logger.info("Auto top-up for org {}: {}", organization_id, outcome)

    if charged or scheduled:
        logger.info(
            "Auto top-up sweep: {} scheduled, {} charged, {} considered",
            scheduled,
            charged,
            len(organization_ids),
        )
