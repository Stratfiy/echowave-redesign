"""Carrying out what :mod:`auto_topup` decided, without ever charging twice.

The decision engine is pure and exhaustively tested; this is the part that
touches a database, a mail server and a bank, in that order, and the ordering is
the design.

**The attempt row is written before the bank is called, never after.** A crash
between presenting a card and recording that we did leaves a charge no guard can
see, and the next sweep makes another. So the row goes in first, in its own
committed transaction, and the partial unique index makes a second one
impossible even if two workers reach here at the same instant.

**The notice is what the row is for, legally as well as operationally.** A debit
may not run until a clear day after the customer was told, so scheduling writes
``notified_at`` and the charge refuses to run before it. If anybody ever asks
whether notice was given, that column is the answer.

**A failed charge is recorded and the run of failures counted.** Three in a row
pauses the account's setting and says why, because presenting a dead instrument
repeatedly costs money and merchant standing, and because the customer needs to
be told something other than silence.
"""

from __future__ import annotations

from datetime import UTC, datetime

from loguru import logger
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from api.constants import UI_APP_URL
from api.db.models import (
    AutoTopupAttemptModel,
    AutoTopupSettingModel,
    OrganizationModel,
    PaymentModel,
)
from api.services.billing import auto_topup, payments
from api.services.billing.billing_profile import get_profile
from api.services.billing.tax import TaxError
from api.services.messaging import email

SCHEDULED = "scheduled"
CHARGING = "charging"
SUCCEEDED = "succeeded"
FAILED = "failed"
CANCELLED = "cancelled"

IN_FLIGHT = (SCHEDULED, CHARGING)


async def load_settings(
    session: AsyncSession, *, organization_id: int
) -> AutoTopupSettingModel | None:
    return await session.scalar(
        select(AutoTopupSettingModel).where(
            AutoTopupSettingModel.organization_id == organization_id
        )
    )


def as_settings(row: AutoTopupSettingModel | None) -> auto_topup.Settings:
    """The stored row as the engine's input. A missing row is off."""
    if row is None:
        return auto_topup.Settings()
    return auto_topup.Settings(
        enabled=bool(row.enabled) and not row.paused_reason,
        trigger_days=int(row.trigger_days),
        trigger_paise=int(row.trigger_paise),
        amount_paise=int(row.amount_paise),
        monthly_cap_paise=int(row.monthly_cap_paise),
        max_per_month=int(row.max_per_month),
    )


async def load_history(
    session: AsyncSession, *, organization_id: int, now: datetime
) -> auto_topup.History:
    """What has already been attempted, as the engine's guards need it.

    The month is a calendar month in UTC. That is a simplification worth
    naming: an account near the boundary gets its allowance a few hours early or
    late relative to its own timezone. The alternative — per-account local
    months — makes the cap unauditable, and the cap is a safety limit rather
    than a billing period.
    """
    month_start = now.replace(
        day=1, hour=0, minute=0, second=0, microsecond=0, tzinfo=UTC
    )

    in_flight = await session.scalar(
        select(func.count(AutoTopupAttemptModel.id)).where(
            AutoTopupAttemptModel.organization_id == organization_id,
            AutoTopupAttemptModel.status.in_(IN_FLIGHT),
        )
    )

    pending = await session.scalar(
        select(AutoTopupAttemptModel)
        .where(
            AutoTopupAttemptModel.organization_id == organization_id,
            AutoTopupAttemptModel.status.in_(IN_FLIGHT),
        )
        .order_by(AutoTopupAttemptModel.created_at.desc())
        .limit(1)
    )

    last_success = await session.scalar(
        select(AutoTopupAttemptModel.charged_at)
        .where(
            AutoTopupAttemptModel.organization_id == organization_id,
            AutoTopupAttemptModel.status == SUCCEEDED,
        )
        .order_by(AutoTopupAttemptModel.charged_at.desc())
        .limit(1)
    )

    charged_this_month = await session.scalar(
        select(func.coalesce(func.sum(AutoTopupAttemptModel.amount_paise), 0)).where(
            AutoTopupAttemptModel.organization_id == organization_id,
            AutoTopupAttemptModel.status == SUCCEEDED,
            AutoTopupAttemptModel.charged_at >= month_start,
        )
    )
    count_this_month = await session.scalar(
        select(func.count(AutoTopupAttemptModel.id)).where(
            AutoTopupAttemptModel.organization_id == organization_id,
            AutoTopupAttemptModel.status == SUCCEEDED,
            AutoTopupAttemptModel.charged_at >= month_start,
        )
    )

    return auto_topup.History(
        in_flight=bool(in_flight),
        last_success_at=last_success,
        charged_this_month_paise=int(charged_this_month or 0),
        count_this_month=int(count_this_month or 0),
        consecutive_failures=await _consecutive_failures(
            session, organization_id=organization_id
        ),
        notified_at=pending.notified_at if pending is not None else None,
        charge_started=(pending is not None and pending.status == CHARGING),
    )


async def _consecutive_failures(session: AsyncSession, *, organization_id: int) -> int:
    """Failures since the last success. Not failures in total.

    Counting every failure ever would pause an account permanently after three
    bad months across a year of working ones, which is not what the stop is for.
    """
    recent = (
        (
            await session.execute(
                select(AutoTopupAttemptModel.status)
                .where(
                    AutoTopupAttemptModel.organization_id == organization_id,
                    AutoTopupAttemptModel.status.in_((SUCCEEDED, FAILED)),
                )
                .order_by(AutoTopupAttemptModel.created_at.desc())
                .limit(auto_topup.MAX_CONSECUTIVE_FAILURES)
            )
        )
        .scalars()
        .all()
    )
    run = 0
    for status in recent:
        if status != FAILED:
            break
        run += 1
    return run


async def _account_for(
    session: AsyncSession, *, organization_id: int, balance_paise: int, burn_paise: int
) -> auto_topup.Account:
    token = await payments.active_token(session, organization_id=organization_id)
    return auto_topup.Account(
        balance_paise=balance_paise,
        daily_burn_paise=burn_paise,
        mandate_max_paise=token.max_amount_paise if token else None,
        mandate_is_live=token is not None,
    )


async def _notify(
    session: AsyncSession,
    *,
    org: OrganizationModel,
    amount_paise: int,
    charge_after: datetime,
    recipients: list[str],
) -> None:
    """Tell the customer a debit is coming, before it does.

    Required a clear day ahead under India's e-mandate rules, and worth sending
    on its own merits: an unexplained debit is a chargeback, and a chargeback
    costs more than the top-up.
    """
    if not recipients:
        logger.warning(
            "Org {} has no billing recipient for the auto top-up notice", org.id
        )
        return

    rupees = f"₹{amount_paise / 100:,.0f}"
    when = charge_after.strftime("%d %b %Y")
    subject = f"We will top up your Decibyl credit by {rupees} on {when}"
    body = (
        f"Your credit is running low, and automatic top-up is switched on for "
        f"your account.\n\n"
        f"On {when} we will charge your saved payment method {rupees} and add "
        f"that credit to your balance.\n\n"
        f"If you would rather we did not, switch automatic top-up off or change "
        f"the amount before then:\n"
        f"  {UI_APP_URL}/billing\n\n"
        f"Nothing is charged until that date."
    )
    for address in recipients:
        await email.send_email(
            to=address, subject=subject, body_text=body, sender="billing"
        )


async def schedule(
    session: AsyncSession,
    *,
    org: OrganizationModel,
    decision: auto_topup.Decision,
    recipients: list[str],
) -> AutoTopupAttemptModel | None:
    """Record the intent, then tell the customer. In that order.

    The row first, because it is what stops a second sweep starting another
    attempt while the email is being sent — and sending mail is the slowest
    thing here, so that window is real rather than theoretical.

    An ``IntegrityError`` means another worker won the race. That is the guard
    doing its job, not an error: it returns None and this sweep does nothing.
    """
    attempt = AutoTopupAttemptModel(
        organization_id=org.id,
        status=SCHEDULED,
        amount_paise=decision.amount_paise,
        reason=decision.reason,
        notified_at=datetime.now(UTC),
        charge_after=decision.charge_after,
    )
    session.add(attempt)
    try:
        await session.flush()
    except IntegrityError:
        await session.rollback()
        logger.info("Org {}: another worker already scheduled an auto top-up", org.id)
        return None

    await _notify(
        session,
        org=org,
        amount_paise=decision.amount_paise,
        charge_after=decision.charge_after or datetime.now(UTC),
        recipients=recipients,
    )
    return attempt


async def execute(
    session: AsyncSession,
    *,
    org: OrganizationModel,
    attempt: AutoTopupAttemptModel,
) -> str:
    """Present the saved instrument for an attempt whose notice has elapsed.

    The order comes first, through the same `create_topup_order` a human top-up
    uses, so the tax split, the ledger credit and the receipt voucher are the
    ones already written and tested — this path adds a way to pay an order, not
    a second way to buy credit. The webhook that follows the charge does the
    crediting, exactly as it does for a customer sitting at a checkout.
    """
    token = await payments.active_token(session, organization_id=org.id)
    if token is None:
        await _fail(session, attempt=attempt, reason="no saved instrument on file")
        raise payments.PaymentError("No saved instrument on file.")

    # Committed, not merely flushed. This function's contract — and the note at
    # the top of this module — is that the row is written before the bank is
    # called. A flush is not that: the transaction commits only when the sweep
    # finishes, so a worker that died during the charge would roll the status
    # back to `scheduled`, and the next sweep would present the card again for
    # a debit the bank may already have taken.
    #
    # Committed, a crash leaves `charging` on the row, which `decide` refuses to
    # retry. A stuck attempt needing a human beats a second charge.
    attempt.status = CHARGING
    await session.commit()
    await session.refresh(attempt)

    try:
        order = await payments.create_topup_order(
            session,
            organization_id=org.id,
            amount_paise=attempt.amount_paise,
            created_by=None,
        )
    except (payments.PaymentError, TaxError) as exc:
        await _fail(session, attempt=attempt, reason=f"order: {exc}")
        raise

    profile = await get_profile(session, organization_id=org.id)
    try:
        payment_id = await payments.charge_saved_token(
            order_id=order.order_id,
            # Gross: the order was created for credit plus tax, and that is what
            # the bank is asked for.
            amount_paise=order.gross_paise,
            token=token,
            email=getattr(profile, "billing_email", None),
            contact=getattr(profile, "phone", None),
        )
    except payments.PaymentError as exc:
        await _fail(session, attempt=attempt, reason=str(exc))
        raise

    attempt.status = SUCCEEDED
    attempt.charged_at = datetime.now(UTC)
    attempt.provider_payment_id = payment_id
    row = await session.scalar(
        select(PaymentModel).where(PaymentModel.order_id == order.order_id)
    )
    if row is not None:
        attempt.payment_id = row.id
    token.last_used_at = datetime.now(UTC)
    await session.flush()

    logger.info(
        "Auto top-up charged {} paise for org {} ({})",
        attempt.amount_paise,
        org.id,
        payment_id,
    )
    return payment_id


async def _fail(
    session: AsyncSession, *, attempt: AutoTopupAttemptModel, reason: str
) -> None:
    """Record a failure and, if the run is long enough, stop trying.

    Pausing writes a reason onto the setting rather than flipping ``enabled``
    off, so the customer's own preference is preserved and the UI can say what
    happened instead of showing a switch that silently moved.
    """
    attempt.status = FAILED
    attempt.failure_reason = reason[:500]
    await session.flush()

    run = await _consecutive_failures(session, organization_id=attempt.organization_id)
    if run >= auto_topup.MAX_CONSECUTIVE_FAILURES:
        row = await load_settings(session, organization_id=attempt.organization_id)
        if row is not None and not row.paused_reason:
            row.paused_reason = (
                f"Paused after {run} failed attempts. The last one said: {reason[:200]}"
            )
            await session.flush()
            logger.warning(
                "Auto top-up paused for org {} after {} failures",
                attempt.organization_id,
                run,
            )


async def pending_attempt(
    session: AsyncSession, *, organization_id: int
) -> AutoTopupAttemptModel | None:
    return await session.scalar(
        select(AutoTopupAttemptModel)
        .where(
            AutoTopupAttemptModel.organization_id == organization_id,
            AutoTopupAttemptModel.status.in_(IN_FLIGHT),
        )
        .order_by(AutoTopupAttemptModel.created_at.desc())
        .limit(1)
    )
