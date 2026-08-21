"""The starter plan: one monthly collection that buys two different things.

    Rs2,500 of call balance  +  Rs499 for the number  =  Rs2,999 net

Everything else in billing sells one thing at a time. A top-up sells credit; a
rental sells a number's month. The plan sells both under a single standing
instruction, and the whole difficulty is that the two halves are settled by
completely different machinery — the balance is a credit ledger entry, the
rent is a recurring-charge period — while the customer paid once.

Three rules hold this together, and each exists because breaking it costs
money in a way that reconciles perfectly against itself.

**One collection grants one balance.** Razorpay delivers webhooks at least
once. A redelivered ``subscription.charged`` that credited a second Rs2,500
would produce two ledger rows, both correct-looking, to an account that paid
for one. The guard is a unique index keyed on the provider's payment id —
``uq_credit_ledger_plan_ref`` — chosen because that id is identical on every
redelivery of the same collection, which is the property that makes
at-least-once delivery safe. Checked before the insert *and* relied on
afterwards: the check makes the common redelivery a read, the index makes the
race correct.

**One collection settles one month of rent.** The plan price already contains
the rental, so the number's period has to be marked collected or the monthly
cron will debit it from the balance as well — the customer would pay the rent
inside the plan and again out of the credit the plan just gave them. That is
``rentals.record_mandate_collection``, which the rental mandate already uses
and which is idempotent on the same payment id.

**A failure to settle the rent does not roll back the balance.** The money has
already moved when this runs. If the rental half fails — no open charge, a
provider payload we cannot read — the customer must still get the balance they
paid for, and the discrepancy must be loud rather than silent. Refusing the
whole cycle would leave them charged and holding nothing.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from loguru import logger
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from api.constants import STARTER_PLAN_BALANCE_PAISE
from api.db.models import CreditLedgerModel, PaymentMandateModel
from api.enums import CreditLedgerKind

#: ``ref_type`` on the ledger row. Names the thing the grant came from, so a
#: reconciliation can walk from a ledger entry back to the collection without
#: guessing which provider id it is looking at.
REF_TYPE = "plan_collection"

#: Only ever appears inside a ledger note, so it is a label rather than the
#: provider constant the mandate module resolves.
PROVIDER_LABEL = "razorpay"

#: ``ref_type`` on an expiry row. ``ref_id`` is the id of the grant it retires,
#: which is what makes writing one twice impossible.
REF_TYPE_EXPIRY = "plan_expiry"

#: How long a cycle's balance lives when no further cycle is collected.
#:
#: Longer than a month on purpose. A collection can land a day or two late — a
#: bank retry, a weekend — and expiring balance the customer is about to renew
#: would be the worst possible timing for the most alarming line on a billing
#: screen. Erring long costs us a few days of balance; erring short takes money
#: from somebody who paid for it.
CYCLE_GRACE_DAYS = 34

#: Ledger kinds that reduce what an account can spend. Used to work out how
#: much of a cycle's grant is left: everything else is money arriving, and a
#: top-up bought after the grant is not plan balance and must not be expired
#: with it.
_CONSUMING_KINDS = (
    CreditLedgerKind.USAGE.value,
    CreditLedgerKind.RESERVATION.value,
    CreditLedgerKind.RENTAL.value,
)


async def _consumed_since(
    session: AsyncSession, *, grant: CreditLedgerModel
) -> int:
    """How much has been spent since ``grant`` landed, in paise.

    Plan balance is spent before anything else — it is the money that expires,
    so drawing it down first is the only ordering that does not quietly convert
    a top-up into something with an expiry date. That rule is what makes this
    subtraction the answer: everything consumed since the grant came out of the
    grant until the grant ran out.

    Reservations net against their own releases, so a call in flight reduces
    the remainder and a released hold gives it back. A top-up bought after the
    grant is not counted at all — the customer bought that outright and it does
    not expire.
    """
    total = await session.scalar(
        select(func.coalesce(func.sum(CreditLedgerModel.delta_paise), 0)).where(
            CreditLedgerModel.organization_id == grant.organization_id,
            CreditLedgerModel.id > grant.id,
            CreditLedgerModel.kind.in_(_CONSUMING_KINDS),
        )
    )
    # Those kinds are debits, so the sum is negative or zero.
    return max(0, -int(total or 0))


async def expire_grant(
    session: AsyncSession, *, grant: CreditLedgerModel, now: datetime | None = None
) -> int:
    """Retire what is left of one cycle's balance. Returns the paise expired.

    Written as its own ledger kind rather than as a negative adjustment: an
    unexplained debit the size of last month's grant is the single most
    alarming line a billing screen can show, and "this expired" is the whole
    explanation.

    Two clamps, and both matter. The remainder cannot exceed what was granted —
    a customer who topped up on top of the plan keeps every rupee of the
    top-up. And it cannot exceed the balance actually on the account, because
    an account already at zero has nothing left to take and driving it negative
    would suspend calls over an expiry rather than over a spend.
    """
    now = now or datetime.now(UTC)

    already = await session.scalar(
        select(CreditLedgerModel).where(
            CreditLedgerModel.organization_id == grant.organization_id,
            CreditLedgerModel.kind == CreditLedgerKind.PLAN_EXPIRY.value,
            CreditLedgerModel.ref_type == REF_TYPE_EXPIRY,
            CreditLedgerModel.ref_id == str(grant.id),
        )
    )
    if already is not None:
        return 0

    consumed = await _consumed_since(session, grant=grant)
    remaining = int(grant.delta_paise) - consumed
    if remaining <= 0:
        return 0

    from api.services.billing.payments import current_balance_paise

    balance = await current_balance_paise(
        session, organization_id=grant.organization_id
    )
    amount = min(remaining, max(0, int(balance)))
    if amount <= 0:
        return 0

    entry = CreditLedgerModel(
        organization_id=grant.organization_id,
        delta_paise=-amount,
        kind=CreditLedgerKind.PLAN_EXPIRY.value,
        ref_type=REF_TYPE_EXPIRY,
        ref_id=str(grant.id),
        balance_after_paise=balance - amount,
        note="Unused plan balance from the previous cycle expired",
        created_at=now,
    )
    try:
        async with session.begin_nested():
            session.add(entry)
            await session.flush()
    except IntegrityError:
        # Two workers reached the same lapsed grant. The index held.
        logger.debug("Plan grant {} was expired by another worker", grant.id)
        return 0

    logger.info(
        "Expired {} paise of unused plan balance for org {} (grant {})",
        amount,
        grant.organization_id,
        grant.id,
    )
    return amount


async def latest_grant(
    session: AsyncSession, *, organization_id: int
) -> CreditLedgerModel | None:
    """The most recent plan grant for this account, expired or not."""
    return await session.scalar(
        select(CreditLedgerModel)
        .where(
            CreditLedgerModel.organization_id == organization_id,
            CreditLedgerModel.kind == CreditLedgerKind.PLAN.value,
        )
        .order_by(CreditLedgerModel.id.desc())
        .limit(1)
    )


async def grant_plan_cycle(
    session: AsyncSession,
    *,
    mandate: PaymentMandateModel,
    event: dict,
    balance_paise: int | None = None,
    now: datetime | None = None,
) -> dict:
    """Fulfil one collected cycle of the starter plan.

    Called from the verified webhook, so the money has already moved. Returns
    what happened rather than raising, because the caller is a webhook endpoint
    and an exception there means Razorpay retries a collection we have already
    banked.
    """
    now = now or datetime.now(UTC)

    # What the plan this mandate actually bought grants, not what the current
    # default plan grants. A customer on last quarter's plan keeps getting last
    # quarter's balance: the collection is against the price they authorised,
    # and granting today's figure against yesterday's price is how a plan change
    # silently re-prices everyone already on it.
    granted = balance_paise
    if granted is None:
        from api.services.billing import subscription_plans

        plan = await subscription_plans.resolve(
            session, code=getattr(mandate, "plan_code", None)
        )
        granted = plan.balance_paise if plan is not None else STARTER_PLAN_BALANCE_PAISE
    granted = int(granted)

    payment = ((event.get("payload") or {}).get("payment") or {}).get("entity") or {}
    payment_id = payment.get("id")

    if not payment_id:
        # Without the provider's id there is nothing to make this idempotent
        # on, and a redelivery would grant a second balance. Refusing is the
        # safe direction: the customer is short a balance they paid for, which
        # is visible and fixable, rather than long one nobody can trace.
        logger.error(
            "Starter-plan collection for mandate {} carries no payment id; "
            "refusing to grant a balance that could not be made idempotent",
            mandate.id,
        )
        return {"status": "no_payment_id", "mandate_id": mandate.id}

    if granted <= 0:
        logger.error(
            "Refusing to grant a non-positive plan balance ({}) for mandate {}",
            granted,
            mandate.id,
        )
        return {"status": "invalid_amount", "mandate_id": mandate.id}

    # Cheap path for the common redelivery: a read rather than an insert that
    # rolls back. The index below is what makes the *race* correct.
    seen = await session.scalar(
        select(CreditLedgerModel).where(
            CreditLedgerModel.organization_id == mandate.organization_id,
            CreditLedgerModel.kind == CreditLedgerKind.PLAN.value,
            CreditLedgerModel.ref_type == REF_TYPE,
            CreditLedgerModel.ref_id == payment_id,
        )
    )
    if seen is not None:
        logger.debug(
            "Starter-plan collection {} already granted as ledger entry {}",
            payment_id,
            seen.id,
        )
        return {
            "status": "already_granted",
            "mandate_id": mandate.id,
            "ledger_id": seen.id,
        }

    # The previous cycle ends here. Plan balance is for the month it was
    # granted for — that is what makes a bundle worth more than a top-up of the
    # same size, and it has to be stated at the point of purchase, which
    # `plan_email` and the billing screen both do. A top-up is never touched:
    # the customer bought that outright.
    #
    # Before the new grant rather than after, so the expiry is clamped against
    # the balance the old cycle actually left behind rather than against a
    # balance the new grant has already refilled.
    previous = await latest_grant(session, organization_id=mandate.organization_id)
    if previous is not None:
        await expire_grant(session, grant=previous, now=now)

    # Imported here rather than at module scope: payments imports this module
    # to route the event, and importing it back at the top closes the loop.
    from api.services.billing.payments import current_balance_paise

    balance = await current_balance_paise(
        session, organization_id=mandate.organization_id
    )
    entry = CreditLedgerModel(
        organization_id=mandate.organization_id,
        delta_paise=granted,
        kind=CreditLedgerKind.PLAN.value,
        ref_type=REF_TYPE,
        ref_id=payment_id,
        balance_after_paise=balance + granted,
        note=f"Starter plan balance ({PROVIDER_LABEL} {payment_id})",
    )
    # A SAVEPOINT, so a collision rolls back this insert and nothing else. A
    # plain rollback would also discard the mandate state change the caller has
    # already applied in the same transaction, and the webhook would answer
    # "already granted" having quietly forgotten the subscription is now active.
    try:
        async with session.begin_nested():
            session.add(entry)
            await session.flush()
    except IntegrityError:
        logger.debug(
            "Starter-plan collection {} raced another delivery; the index held",
            payment_id,
        )
        return {"status": "already_granted", "mandate_id": mandate.id}

    logger.info(
        "Granted {} paise of plan balance to org {} for collection {}",
        granted,
        mandate.organization_id,
        payment_id,
    )

    # The rent half. Separate try: the balance is already granted and committed
    # to, and the customer keeps it whatever happens here.
    rental = {"status": "not_attempted"}
    try:
        from api.services.billing.rentals import record_mandate_collection

        rental = await record_mandate_collection(
            session, mandate_id=mandate.id, event=event, now=now
        )
    except Exception as exc:  # noqa: BLE001 — the balance outranks the period
        logger.error(
            "Starter-plan balance granted for org {} but the number's period "
            "could not be recorded: {}. The rent is inside the collection that "
            "already happened, so the monthly job must not debit it again — "
            "check recurring_charge_periods for mandate {}.",
            mandate.organization_id,
            exc,
            mandate.id,
        )
        rental = {"status": "error", "detail": str(exc)}

    if rental.get("status") == "no_charge":
        # Expected exactly once, and benign: the plan can be authorised before
        # a number has been chosen. The next collection lands on the charge.
        logger.info(
            "Starter-plan collection for org {} has no number yet; the balance "
            "was granted and the rent has nothing to settle against",
            mandate.organization_id,
        )

    return {
        "status": "granted",
        "mandate_id": mandate.id,
        "ledger_id": entry.id,
        "granted_paise": granted,
        "balance_after_paise": entry.balance_after_paise,
        "rental": rental,
    }


async def sweep_lapsed_plan_balance(
    session: AsyncSession, *, now: datetime | None = None
) -> dict[str, int]:
    """Expire plan balance whose cycle ended and was never renewed.

    :func:`grant_plan_cycle` retires the previous cycle when the next one is
    collected, which covers every account that keeps paying. An account that
    cancels, or whose bank stops honouring the mandate, gets no next collection
    — and without this its last cycle's balance would sit there for ever,
    which is precisely the "rolls over indefinitely" behaviour the expiry
    decision replaced.

    Grace is deliberate: ``CYCLE_GRACE_DAYS`` is longer than a month so a
    collection that lands a day or two late never expires balance the customer
    is about to renew.
    """
    now = now or datetime.now(UTC)
    cutoff = now - timedelta(days=CYCLE_GRACE_DAYS)

    # The most recent grant per account, taken older-than-cutoff. Anything with
    # a newer grant was already retired at the moment that grant landed.
    newest = (
        select(
            CreditLedgerModel.organization_id.label("organization_id"),
            func.max(CreditLedgerModel.id).label("grant_id"),
        )
        .where(CreditLedgerModel.kind == CreditLedgerKind.PLAN.value)
        .group_by(CreditLedgerModel.organization_id)
        .subquery()
    )
    grants = (
        await session.execute(
            select(CreditLedgerModel)
            .join(newest, CreditLedgerModel.id == newest.c.grant_id)
            .where(CreditLedgerModel.created_at < cutoff)
        )
    ).scalars().all()

    counters = {"considered": len(grants), "expired": 0, "paise": 0}
    for grant in grants:
        expired = await expire_grant(session, grant=grant, now=now)
        if expired:
            counters["expired"] += 1
            counters["paise"] += expired
    return counters
