"""Monthly rental charges for resources we hold on a customer's behalf.

This is the money side of managed numbers, and it is deliberately **not** part
of the per-call costing path. A call receipt is computed from measured usage on
a workflow run; a rental accrues because a month passed. Sharing the code would
have meant inventing a fake run to hang a rental off, and would have shown
customers a line item for calls they never made.

Two invariants hold everything together.

**A period is billed at most once.** ``recurring_charge_periods`` has a unique
constraint on ``(recurring_charge_id, period_start)`` and the ledger has a
partial unique index on rental refs. Both are needed: the period row and the
ledger debit are two writes, and a crash between them must not let a retry
charge twice. The cron is therefore safe to run twice, safe to run late, and
safe to re-run over a month it already processed.

**Cost and price are both recorded.** Every period stores what the customer was
charged and what the carrier charges us. Recording only the price is precisely
how the platform's margin figures came to ignore number rental entirely — the
number looked healthy because a real fixed cost was invisible to it.

Proration: the first period runs from purchase to the end of that calendar
month and is charged pro rata by whole days. Every period after is a full
month. A number bought on the 28th does not pay for the 27 days before it
existed.
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import UTC, datetime

from loguru import logger
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from api import constants
from api.db import db_client
from api.db.models import (
    CreditLedgerModel,
    OrganizationModel,
    PaymentMandateModel,
    RecurringChargeModel,
    RecurringChargePeriodModel,
    TelephonyPhoneNumberModel,
)
from api.enums import (
    CreditLedgerKind,
    PhoneNumberStatus,
    RecurringChargeStatus,
    RecurringChargeType,
)
from api.services.billing import dunning
from api.services.billing.costing import current_balance_paise
from api.services.billing.money import round_half_up_div


@dataclass(frozen=True)
class ChargeOutcome:
    """What one attempt to bill one period did."""

    charge_id: int
    charged: bool
    amount_paise: int = 0
    prorated: bool = False
    reason: str = ""


def month_end(moment: datetime) -> datetime:
    """Midnight UTC on the first day of the following month."""
    if moment.month == 12:
        return moment.replace(
            year=moment.year + 1,
            month=1,
            day=1,
            hour=0,
            minute=0,
            second=0,
            microsecond=0,
        )
    return moment.replace(
        month=moment.month + 1,
        day=1,
        hour=0,
        minute=0,
        second=0,
        microsecond=0,
    )


def month_start(moment: datetime) -> datetime:
    return moment.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def prorated_amount(
    *, full_price_paise: int, period_start: datetime, period_end: datetime
) -> int:
    """A partial month's price, by whole days.

    Days rather than seconds because a customer can check days against a
    calendar, and the difference between the two is a few paise on a ₹399
    rental. ``round_half_up_div`` for the same reason it is used everywhere
    else in billing: banker's rounding would make a half-paise land differently
    depending on which day of the month it was.

    Always at least one day, so a number bought at 23:55 on the last of the
    month is not free.
    """
    days_in_month = calendar.monthrange(period_start.year, period_start.month)[1]
    used_days = max(1, (period_end - period_start).days)
    if used_days >= days_in_month:
        return full_price_paise
    return round_half_up_div(full_price_paise * used_days, days_in_month)


async def numbers_a_mandate_covers(session: AsyncSession, *, mandate) -> int:
    """How many numbers one standing instruction pays the rent for.

    A rental mandate is registered for one number's rent, so it covers one. A
    plan mandate covers whatever its plan includes.

    This number is the difference between an entitlement and a hole. A charge
    carrying a mandate is skipped by the monthly job — the bank is collecting
    for it instead (``_mandate_collects``) — while a collection settles exactly
    one charge (``record_mandate_collection``). Attach a mandate to every number
    an account holds and every number is skipped, one is settled, and the rest
    are free for ever with nothing anywhere reading as an error.
    """
    from api.services.billing import subscription_plans
    from api.services.billing.mandates import PURPOSE_STARTER_PLAN

    if mandate is None:
        return 0
    if mandate.purpose != PURPOSE_STARTER_PLAN:
        return 1

    plan = await subscription_plans.resolve(
        session, code=getattr(mandate, "plan_code", None)
    )
    # A plan mandate whose plan has been deleted still covers the number it was
    # sold with. Dropping to zero would start billing the balance for a number
    # the bank is also collecting for, which is the double charge this whole
    # path exists to avoid.
    return plan.included_numbers if plan is not None else 1


async def next_number_price_paise(
    session: AsyncSession, *, organization_id: int
) -> int:
    """What the next number this account buys will cost per month, net of GST.

    The plan's own figure when the account is on a plan that sets one, and the
    platform rental price otherwise. Resolved here rather than read from
    constants at the call site so a plan that sells extra numbers cheaper is a
    row an operator writes, not a branch somebody has to remember to add.
    """
    from api.services.billing import subscription_plans
    from api.services.billing.mandates import PURPOSE_STARTER_PLAN, get_mandate

    mandate = await get_mandate(
        session, organization_id=organization_id, purpose=PURPOSE_STARTER_PLAN
    )
    if mandate is not None:
        plan = await subscription_plans.resolve(
            session, code=getattr(mandate, "plan_code", None)
        )
        if plan is not None:
            return plan.extra_number_price_paise
    return constants.NUMBER_RENTAL_PRICE_PAISE


async def numbers_on_mandate(session: AsyncSession, *, mandate_id: int) -> int:
    """How many live numbers are already attached to this standing instruction.

    Live meaning not released and not cancelled: a number given back frees the
    entitlement it was using, so an account that releases its plan number can
    claim another one without paying twice.
    """
    return (
        await session.scalar(
            select(func.count())
            .select_from(RecurringChargeModel)
            .where(
                RecurringChargeModel.mandate_id == mandate_id,
                RecurringChargeModel.charge_type
                == RecurringChargeType.NUMBER_RENTAL.value,
                RecurringChargeModel.status.notin_(
                    [
                        RecurringChargeStatus.RELEASED.value,
                        RecurringChargeStatus.CANCELLED.value,
                    ]
                ),
            )
        )
    ) or 0


async def _mandate_for_this_number(
    session: AsyncSession, *, organization_id: int, mandate_id: int | None
) -> int | None:
    """The mandate to attach to a number about to be provisioned, or ``None``.

    ``None`` means "this one is beyond what the standing instruction pays for",
    and the monthly job then bills it from the prepaid balance like any other
    rental. That is the whole entitlement: the plan's numbers are inside the
    price, and the next one is a rental the customer pays for separately.

    Counted under the organization row lock, because two provisions racing
    would otherwise both see the entitlement free and both claim it — and the
    result would be two numbers the bank pays for one of.
    """
    if mandate_id is None:
        return None

    mandate = await session.get(PaymentMandateModel, mandate_id)
    covers = await numbers_a_mandate_covers(session, mandate=mandate)
    if covers <= 0:
        return None

    await session.execute(
        select(OrganizationModel.id)
        .where(OrganizationModel.id == organization_id)
        .with_for_update()
    )

    already = await numbers_on_mandate(session, mandate_id=mandate_id)

    if already >= covers:
        logger.info(
            "Org {} already holds {} number(s) covered by mandate {}, which "
            "covers {}. This number bills from the balance.",
            organization_id,
            already,
            mandate_id,
            covers,
        )
        return None
    return mandate_id


async def open_number_rental(
    *,
    organization_id: int,
    phone_number_id: int,
    cost_paise: int,
    price_paise: int,
    mandate_id: int | None = None,
    now: datetime | None = None,
) -> RecurringChargeModel | None:
    """Start charging rent for a number that has just been provisioned.

    Returns the existing charge rather than raising if one is already open —
    a provisioning retry that got as far as the purchase should converge on one
    charge, not fail because it half-succeeded before.

    ``mandate_id`` is the account's authorised standing instruction, and it is
    attached only if this number is **within** what that instruction covers.
    Beyond it the charge carries no mandate and the monthly job debits the
    balance — see :func:`_mandate_for_this_number`.
    """
    now = now or datetime.now(UTC)

    async with db_client.async_session() as session:
        existing = await session.scalar(
            select(RecurringChargeModel).where(
                RecurringChargeModel.charge_type
                == RecurringChargeType.NUMBER_RENTAL.value,
                RecurringChargeModel.resource_id == phone_number_id,
                RecurringChargeModel.status.notin_(
                    [
                        RecurringChargeStatus.RELEASED.value,
                        RecurringChargeStatus.CANCELLED.value,
                    ]
                ),
            )
        )
        if existing is not None:
            logger.debug(
                "Rental charge {} already open for number {}",
                existing.id,
                phone_number_id,
            )
            return existing

        covered_by = await _mandate_for_this_number(
            session, organization_id=organization_id, mandate_id=mandate_id
        )

        charge = RecurringChargeModel(
            organization_id=organization_id,
            charge_type=RecurringChargeType.NUMBER_RENTAL.value,
            resource_id=phone_number_id,
            status=RecurringChargeStatus.ACTIVE.value,
            cost_paise=cost_paise,
            price_paise=price_paise,
            mandate_id=covered_by,
            started_at=now,
            # Due immediately: the first period is the remainder of this month,
            # charged pro rata by the next cron tick.
            next_charge_at=now,
        )
        session.add(charge)
        try:
            await session.commit()
        except IntegrityError:
            # The partial unique index caught a concurrent open. Whoever won is
            # the one true charge.
            await session.rollback()
            return await session.scalar(
                select(RecurringChargeModel).where(
                    RecurringChargeModel.charge_type
                    == RecurringChargeType.NUMBER_RENTAL.value,
                    RecurringChargeModel.resource_id == phone_number_id,
                    RecurringChargeModel.status.notin_(
                        [
                            RecurringChargeStatus.RELEASED.value,
                            RecurringChargeStatus.CANCELLED.value,
                        ]
                    ),
                )
            )
        await session.refresh(charge)
        logger.info(
            "Opened rental charge {} for number {} at {} paise/month",
            charge.id,
            phone_number_id,
            price_paise,
        )
        return charge


async def due_charges(*, now: datetime | None = None, limit: int = 500):
    """Charges whose next billing date has arrived.

    Excludes released and cancelled charges. Includes past-due and suspended
    ones: a suspended charge is still retried daily, and collecting it is what
    lifts the suspension.
    """
    now = now or datetime.now(UTC)
    async with db_client.async_session() as session:
        return list(
            (
                await session.scalars(
                    select(RecurringChargeModel)
                    .where(
                        RecurringChargeModel.status.notin_(
                            [
                                RecurringChargeStatus.RELEASED.value,
                                RecurringChargeStatus.CANCELLED.value,
                            ]
                        ),
                        RecurringChargeModel.next_charge_at.isnot(None),
                        RecurringChargeModel.next_charge_at <= now,
                    )
                    .order_by(RecurringChargeModel.next_charge_at)
                    .limit(limit)
                )
            ).all()
        )


async def charge_period(
    charge_id: int, *, now: datetime | None = None
) -> ChargeOutcome:
    """Bill one period of one charge, exactly once.

    The period boundaries are derived from what has already been billed, not
    from the clock: a cron that missed a month bills the month it missed rather
    than skipping it, and a cron that runs twice on the same day finds the
    period already recorded and does nothing.
    """
    now = now or datetime.now(UTC)

    async with db_client.async_session() as session:
        charge = await session.get(RecurringChargeModel, charge_id)
        if charge is None:
            return ChargeOutcome(charge_id=charge_id, charged=False, reason="missing")
        if charge.status in (
            RecurringChargeStatus.RELEASED.value,
            RecurringChargeStatus.CANCELLED.value,
        ):
            return ChargeOutcome(charge_id=charge_id, charged=False, reason="closed")

        if await _mandate_collects(session, charge):
            # The bank collects for this charge, so the balance must not. Two
            # collection paths for one period is a double charge, and it is the
            # kind that looks correct in both ledgers separately. The period row
            # is written when the provider tells us it collected, in
            # record_mandate_collection.
            #
            # This does forgo the prorated first part-month: the mandate's first
            # cycle charges a full month and our part-month is never billed
            # separately. Losing part of one month is the right direction to err
            # when the alternative is billing the same month twice.
            return ChargeOutcome(
                charge_id=charge_id, charged=False, reason="mandate_collects"
            )

        period_start, period_end, prorated = _next_period(charge, now=now)
        # Strictly greater: a period becomes due *at* its start, so a tick that
        # lands exactly on a month boundary bills that month rather than
        # deferring it to the next run. With >= here, a cron firing at
        # midnight on the first would find every charge "not due".
        if period_start > now and charge.current_period_end is not None:
            return ChargeOutcome(charge_id=charge_id, charged=False, reason="not_due")

        amount = (
            prorated_amount(
                full_price_paise=charge.price_paise,
                period_start=period_start,
                period_end=period_end,
            )
            if prorated
            else charge.price_paise
        )
        cost = (
            prorated_amount(
                full_price_paise=charge.cost_paise,
                period_start=period_start,
                period_end=period_end,
            )
            if prorated
            else charge.cost_paise
        )

        balance = await current_balance_paise(
            session, organization_id=charge.organization_id
        )
        if balance < amount:
            await _record_failure(
                session,
                charge,
                now=now,
                reason=(
                    f"Balance {balance} paise is short of the {amount} paise rental."
                ),
            )
            await session.commit()
            return ChargeOutcome(
                charge_id=charge_id,
                charged=False,
                amount_paise=amount,
                reason="insufficient_balance",
            )

        period = RecurringChargePeriodModel(
            recurring_charge_id=charge.id,
            organization_id=charge.organization_id,
            period_start=period_start,
            period_end=period_end,
            charged_paise=amount,
            cost_paise=cost,
            prorated=prorated,
        )
        session.add(period)
        try:
            # Flushed before the ledger write so a duplicate period is caught
            # here, before any money moves.
            await session.flush()
        except IntegrityError:
            await session.rollback()
            logger.debug(
                "Rental period {} for charge {} already billed",
                period_start,
                charge_id,
            )
            return ChargeOutcome(
                charge_id=charge_id, charged=False, reason="already_billed"
            )

        session.add(
            CreditLedgerModel(
                organization_id=charge.organization_id,
                delta_paise=-amount,
                kind=CreditLedgerKind.RENTAL.value,
                ref_type="recurring_charge_period",
                ref_id=str(period.id),
                balance_after_paise=balance - amount,
                note=f"Number rental {period_start:%b %Y}"
                + (" (prorated)" if prorated else ""),
            )
        )

        charge.current_period_start = period_start
        charge.current_period_end = period_end
        charge.next_charge_at = period_end
        charge.last_attempt_at = now
        # Collected: the dunning clock resets completely. A customer who pays
        # late starts from zero rather than carrying a clock from an earlier
        # lapse.
        charge.first_failed_at = None
        charge.failure_count = 0
        charge.last_failure_reason = None
        if charge.status in (
            RecurringChargeStatus.PAST_DUE.value,
            RecurringChargeStatus.SUSPENDED.value,
            RecurringChargeStatus.PENDING_RELEASE.value,
        ):
            charge.status = RecurringChargeStatus.ACTIVE.value
            await _set_number_status(session, charge, PhoneNumberStatus.ACTIVE.value)
            logger.info(
                "Rental charge {} collected; number {} restored",
                charge.id,
                charge.resource_id,
            )

        try:
            await session.commit()
        except IntegrityError:
            # The ledger's rental uniqueness index. Someone else billed this
            # period between our flush and our commit.
            await session.rollback()
            return ChargeOutcome(
                charge_id=charge_id, charged=False, reason="already_billed"
            )

        return ChargeOutcome(
            charge_id=charge_id,
            charged=True,
            amount_paise=amount,
            prorated=prorated,
        )


async def _mandate_collects(
    session: AsyncSession, charge: RecurringChargeModel
) -> bool:
    """Whether an authorised autopay mandate is collecting for this charge.

    Re-read every time rather than cached on the charge, because the state that
    matters is the bank's and it changes without us doing anything — a customer
    revokes a mandate in their UPI app and the next tick has to notice and fall
    back to the balance.
    """
    if not charge.mandate_id:
        return False

    from api.db.models import PaymentMandateModel
    from api.enums import MandateStatus

    mandate = await session.get(PaymentMandateModel, charge.mandate_id)
    if not (mandate and mandate.status in MandateStatus.authorised()):
        return False

    # One mandate is one Razorpay subscription against a one-number plan, and
    # there is exactly one live mandate per organization (enforced by a partial
    # unique index). So it collects for *one* number, not for every number the
    # account rents.
    #
    # Keying the skip on `mandate_id` alone meant an org with three numbers had
    # all three charges carrying the same mandate id, all three skipped, and
    # none of them ever advancing `next_charge_at` — so they were re-skipped
    # every night, for ever. We collected one rental and paid the carrier for
    # three.
    #
    # Until the subscription can carry a quantity, the mandate covers the
    # earliest open charge and the rest bill from the prepaid balance as they
    # would with no autopay at all. `record_mandate_collection` resolves the
    # same charge the same way, so the two cannot disagree about which number
    # the bank paid for.
    covered_id = await _mandate_covered_charge_id(session, charge.mandate_id)
    return covered_id == charge.id


async def _mandate_covered_charge_id(
    session: AsyncSession, mandate_id: int
) -> int | None:
    """Which single charge this mandate collects for.

    The earliest still-open charge, by id. Deterministic on purpose: both the
    nightly skip and the webhook that records a collection ask this question,
    and an answer that could differ between them would either double-charge a
    number or bill none of them.
    """
    return await session.scalar(
        select(RecurringChargeModel.id)
        .where(
            RecurringChargeModel.mandate_id == mandate_id,
            RecurringChargeModel.status.notin_(
                [
                    RecurringChargeStatus.RELEASED.value,
                    RecurringChargeStatus.CANCELLED.value,
                ]
            ),
        )
        .order_by(RecurringChargeModel.id)
        .limit(1)
    )


async def record_mandate_collection(
    session: AsyncSession,
    *,
    mandate_id: int,
    event: dict,
    now: datetime | None = None,
) -> dict:
    """Record a period the customer's bank paid for, under an autopay mandate.

    Called from the verified webhook, so the money has already moved. This
    writes the bookkeeping and **no ledger entry**: the prepaid ledger tracks
    credit the customer bought from us, and a rental collected directly from
    their bank never became credit. Debiting it here would take the rent twice —
    once at the bank, once from a balance that was never topped up for it.

    The amount is the provider's, the period is ours. What the bank actually
    took is a fact about the collection; which month it covers is a fact about
    our schedule, and deriving it from ``_next_period`` keeps one definition of
    a period rather than two that drift at month ends.

    Idempotency keys off the **provider's payment id**, not the period. It has
    to: recording a collection advances the charge to the next period, so a
    redelivered webhook would compute a different ``period_start``, miss the
    period unique constraint entirely, and bill a month forward. The payment id
    is identical on every redelivery of the same collection, which is the
    property that makes at-least-once delivery safe.
    """
    now = now or datetime.now(UTC)

    # Ordered, not arbitrary. This picked whichever row the database happened
    # to return, while `_mandate_collects` skipped *every* charge sharing the
    # mandate — so with more than one number the collection could land on a
    # different charge each month. Both now resolve the same single charge.
    charge = await session.scalar(
        select(RecurringChargeModel)
        .where(
            RecurringChargeModel.mandate_id == mandate_id,
            RecurringChargeModel.status.notin_(
                [
                    RecurringChargeStatus.RELEASED.value,
                    RecurringChargeStatus.CANCELLED.value,
                ]
            ),
        )
        .order_by(RecurringChargeModel.id)
        .limit(1)
    )
    if charge is None:
        # A collection with nothing to apply it to. Loud, because the customer
        # has been debited and we are holding money against no resource — the
        # mirror image of the orphaned-rental failure reconciliation catches.
        logger.error(
            "Mandate {} collected but has no open recurring charge; the customer "
            "has been debited with nothing to apply it to",
            mandate_id,
        )
        return {"status": "no_charge", "mandate_id": mandate_id}

    payment = ((event.get("payload") or {}).get("payment") or {}).get("entity") or {}
    payment_id = payment.get("id")
    cost = charge.cost_paise

    # The **take**, not the ask — recording our price rather than what the bank
    # actually moved is how a reconciliation stops finding discrepancies it
    # should find — but converted to net first.
    #
    # The provider reports the gross, because a mandate is registered for what
    # the bank is told to collect, tax included. This column is net everywhere
    # else: the balance-collected path a few hundred lines up writes the
    # charge's own price, which is net. Storing the provider's figure verbatim
    # therefore put tax-inclusive rupees in a net column, and nothing reading
    # it could tell the two apart — mandate revenue read 18% high against
    # rentals paid from a balance. The gross is not lost: it is the total on
    # the receipt voucher issued for this collection.
    collected_gross = payment.get("amount")
    amount = charge.price_paise
    if collected_gross is not None:
        amount = await _net_of_collection(
            session,
            organization_id=charge.organization_id,
            gross_paise=int(collected_gross),
            fallback_paise=charge.price_paise,
        )
        if amount != charge.price_paise:
            # Not fatal: the money has moved and the period must still be
            # recorded, or the monthly cron will debit the balance for a month
            # the bank already paid. Loud, because a collection that does not
            # match its plan is either a price change that never reached the
            # provider, or a plan the customer is on that we think they are not.
            logger.error(
                "Mandate collection {} came to {} paise net but charge {} is "
                "priced at {}; recording what was collected",
                payment_id,
                amount,
                charge.id,
                charge.price_paise,
            )

    if payment_id:
        # Checked before the insert as well as relied on afterwards. The index
        # is what guarantees correctness under a race; this makes the common
        # redelivery a read rather than a rolled-back transaction.
        seen = await session.scalar(
            select(RecurringChargePeriodModel).where(
                RecurringChargePeriodModel.provider_payment_id == payment_id
            )
        )
        if seen is not None:
            logger.debug(
                "Mandate collection {} already recorded as period {}",
                payment_id,
                seen.id,
            )
            return {
                "status": "already_recorded",
                "charge_id": charge.id,
                "period_id": seen.id,
            }

    period_start, period_end, prorated = _next_period(charge, now=now)

    period = RecurringChargePeriodModel(
        recurring_charge_id=charge.id,
        organization_id=charge.organization_id,
        period_start=period_start,
        period_end=period_end,
        charged_paise=amount,
        cost_paise=cost,
        prorated=prorated,
        collected_via="mandate",
        provider_payment_id=payment_id,
    )
    # Inserted inside a SAVEPOINT so a collision rolls back the insert and
    # nothing else. A plain rollback here would also discard the mandate state
    # change the caller has already applied in this same transaction, and the
    # webhook would return "already recorded" having quietly forgotten that the
    # subscription is now active.
    try:
        async with session.begin_nested():
            session.add(period)
            await session.flush()
    except IntegrityError:
        # Razorpay delivers at least once. The unique index on
        # provider_payment_id is what makes a redelivery a no-op rather than a
        # second month of rent.
        logger.debug(
            "Mandate collection for charge {} period {} already recorded",
            charge.id,
            period_start,
        )
        return {"status": "already_recorded", "charge_id": charge.id}

    charge.current_period_start = period_start
    charge.current_period_end = period_end
    charge.next_charge_at = period_end
    charge.last_attempt_at = now
    charge.first_failed_at = None
    charge.failure_count = 0
    charge.last_failure_reason = None
    if charge.status in (
        RecurringChargeStatus.PAST_DUE.value,
        RecurringChargeStatus.SUSPENDED.value,
        RecurringChargeStatus.PENDING_RELEASE.value,
    ):
        charge.status = RecurringChargeStatus.ACTIVE.value
        await _set_number_status(session, charge, PhoneNumberStatus.ACTIVE.value)
        logger.info(
            "Mandate collected for charge {}; number {} restored",
            charge.id,
            charge.resource_id,
        )
    await session.flush()

    logger.info(
        "Recorded {} paise collected by mandate {} for charge {} period {:%b %Y}",
        amount,
        mandate_id,
        charge.id,
        period_start,
    )
    return {
        "status": "recorded",
        "charge_id": charge.id,
        "period_id": period.id,
        "charged_paise": amount,
    }


async def _net_of_collection(
    session: AsyncSession,
    *,
    organization_id: int,
    gross_paise: int,
    fallback_paise: int,
) -> int:
    """The taxable value inside what the bank collected.

    Inverts exactly the step that registered the mandate — see
    ``mandates.start_rental_mandate``, which grosses the price up before telling
    the provider what to charge — so a collection at the expected amount comes
    back as the charge's own price, to the paise.

    Falls back to our price when the tax cannot be resolved at all: an export
    with no LUT on file raises rather than guessing, and a period recorded at
    the list price is far better than one not recorded, which would have the
    monthly cron debit a balance for a month the bank already paid.
    """
    from api.services.billing.billing_profile import get_profile
    from api.services.billing.tax import TaxError, net_of

    try:
        profile = await get_profile(session, organization_id=organization_id)
        return net_of(
            gross_paise=gross_paise,
            country_code=profile.country_code,
            state_code=profile.state_code,
        )
    except TaxError:
        logger.warning(
            "Could not resolve tax for org {}; recording the collection at the "
            "charge's own price",
            organization_id,
        )
        return fallback_paise


def _next_period(
    charge: RecurringChargeModel, *, now: datetime
) -> tuple[datetime, datetime, bool]:
    """The period this charge owes next, and whether it is prorated.

    Derived from ``current_period_end`` rather than from ``now`` so a cron that
    did not run for six weeks bills the month it missed first, and catches up
    one period per tick instead of silently forgiving it.
    """
    if charge.current_period_end is None:
        start = charge.started_at
        if start.tzinfo is None:
            start = start.replace(tzinfo=UTC)
        end = month_end(start)
        return start, end, True

    start = charge.current_period_end
    if start.tzinfo is None:
        start = start.replace(tzinfo=UTC)
    return start, month_end(start), False


async def _record_failure(
    session: AsyncSession,
    charge: RecurringChargeModel,
    *,
    now: datetime,
    reason: str,
) -> None:
    """Apply the dunning policy to a charge that could not be collected."""
    if charge.first_failed_at is None:
        charge.first_failed_at = now
    charge.failure_count = (charge.failure_count or 0) + 1
    charge.last_attempt_at = now
    charge.last_failure_reason = reason[:2000]
    charge.next_charge_at = dunning.next_retry_at(now)

    first_failed = charge.first_failed_at
    if first_failed.tzinfo is None:
        first_failed = first_failed.replace(tzinfo=UTC)

    state = dunning.evaluate(
        first_failed_at=first_failed, current_status=charge.status, now=now
    )
    charge.status = state.status.value

    if state.should_suspend:
        await _set_number_status(session, charge, PhoneNumberStatus.SUSPENDED.value)

    logger.warning(
        "Rental charge {} for org {} is {} day(s) overdue ({}); status {}",
        charge.id,
        charge.organization_id,
        state.days_overdue,
        reason,
        state.status.value,
    )


async def _set_number_status(
    session: AsyncSession, charge: RecurringChargeModel, status: str
) -> None:
    """Mirror a charge's state onto the number it pays for.

    Only for number rentals, and never touching ``is_active``: that is the
    customer's own switch, and a suspension that flipped it would silently
    turn the number off for good when they later paid.
    """
    if charge.charge_type != RecurringChargeType.NUMBER_RENTAL.value:
        return
    number = await session.get(TelephonyPhoneNumberModel, charge.resource_id)
    if number is None:
        logger.error(
            "Rental charge {} points at phone number {}, which does not exist",
            charge.id,
            charge.resource_id,
        )
        return
    if number.status == PhoneNumberStatus.RELEASED.value:
        # Released is terminal. Nothing brings a number back.
        return
    number.status = status


async def run_due_charges(*, now: datetime | None = None) -> dict[str, int]:
    """Bill everything that is due. Safe to run repeatedly.

    One charge failing must not stop the rest: a customer with no balance is
    the normal case this loop exists to handle, and an exception escaping here
    would leave every later charge unbilled until the next tick.
    """
    now = now or datetime.now(UTC)
    charges = await due_charges(now=now)
    counters = {"considered": len(charges), "charged": 0, "failed": 0}

    for charge in charges:
        try:
            outcome = await charge_period(charge.id, now=now)
        except Exception:
            logger.exception("Rental charge {} could not be processed", charge.id)
            counters["failed"] += 1
            continue
        if outcome.charged:
            counters["charged"] += 1
        elif outcome.reason == "insufficient_balance":
            counters["failed"] += 1

    logger.info(
        "Rental billing: {considered} due, {charged} charged, {failed} unpaid",
        **counters,
    )
    return counters


async def close_rental(
    *,
    phone_number_id: int,
    status: RecurringChargeStatus,
    now: datetime | None = None,
) -> None:
    """Stop billing for a number, on release or on a clean cancellation.

    Called by the release path. Ending the charge is what makes the money stop;
    forgetting it is how a customer keeps paying for a number they gave back.
    """
    now = now or datetime.now(UTC)
    async with db_client.async_session() as session:
        charge = await session.scalar(
            select(RecurringChargeModel).where(
                RecurringChargeModel.charge_type
                == RecurringChargeType.NUMBER_RENTAL.value,
                RecurringChargeModel.resource_id == phone_number_id,
                RecurringChargeModel.status.notin_(
                    [
                        RecurringChargeStatus.RELEASED.value,
                        RecurringChargeStatus.CANCELLED.value,
                    ]
                ),
            )
        )
        if charge is None:
            return
        charge.status = status.value
        charge.ended_at = now
        charge.next_charge_at = None
        await session.commit()
        logger.info(
            "Closed rental charge {} for number {} as {}",
            charge.id,
            phone_number_id,
            status.value,
        )


def monthly_margin_paise(price_paise: int, cost_paise: int) -> int:
    """What a rental actually earns.

    Trivial, and here rather than inline so the dashboard and the reports call
    the same thing. The whole reason this module records cost alongside price
    is that this number was previously unobtainable.
    """
    return price_paise - cost_paise
