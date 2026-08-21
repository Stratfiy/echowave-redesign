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

from datetime import UTC, datetime

from loguru import logger
from sqlalchemy import select
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
