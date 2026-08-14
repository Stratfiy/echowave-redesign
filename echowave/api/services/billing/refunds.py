"""Refunding a payment: the money back, and the credit reversed with it.

Nothing reversed a payment before this. A chargeback or a goodwill refund left
credit granted with no way to claw it back, which meant every refund was two
manual acts — one in Razorpay, one in the ledger — and nobody could tell later
whether both had happened.

Three rules carry this module, and each exists because breaking it costs real
money rather than merely being untidy.

**Never refund more than was paid.** Enforced against the payment's own gross,
not against a number in the request. A refund of ten times the payment is one
misplaced decimal, and Razorpay will happily process it.

**Never refund credit that has been spent.** The ledger has to come out at or
above zero afterwards. Refunding a customer who has already made the calls
hands back the money *and* keeps the service — and the balance goes negative,
which every downstream reservation check reads as an account in good standing
with a strange number.

**Money first, ledger second, and only if the money moved.** Reversing the
ledger before Razorpay confirms would take credit off an account whose refund
then failed. The other order is recoverable: a refund that succeeded at the
provider and failed to record here shows up as a Razorpay refund with no
ledger row, which reconciliation catches and a human can finish.

Partial refunds are supported because a dispute is usually about part of a
purchase, and forcing all-or-nothing means the honest resolution is unavailable
in exactly the case people argue about.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

import httpx
from loguru import logger
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.constants import RAZORPAY_API_BASE, RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET
from api.db.models import CreditLedgerModel, PaymentModel, RefundModel
from api.enums import CreditLedgerKind

_TIMEOUT = httpx.Timeout(20.0)


class RefundError(ValueError):
    """A refund could not be made."""


@dataclass(frozen=True)
class RefundResult:
    refund_id: str | None
    amount_paise: int
    reversed_credit_paise: int


async def refundable_paise(session: AsyncSession, *, payment: PaymentModel) -> int:
    """What is left to refund on this payment.

    Gross, because that is what the customer paid and what Razorpay holds. The
    ledger reversal works in net — the tax was never in the ledger.
    """
    already = await session.scalar(
        select(func.coalesce(func.sum(RefundModel.amount_paise), 0)).where(
            RefundModel.payment_id == payment.id,
            RefundModel.status != "failed",
        )
    )
    gross = int(payment.gross_paise or payment.amount_paise or 0)
    return max(0, gross - int(already or 0))


def _net_for(payment: PaymentModel, gross_refund_paise: int) -> int:
    """The credit to reverse for a gross refund, in the ledger's own terms.

    The ledger is GST-exclusive throughout, so refunding the tax must not
    remove credit that was never granted. Proportional rather than
    tax-first: a partial refund of a taxed payment is a partial refund of both
    halves, which is also how the credit note has to read.
    """
    gross = int(payment.gross_paise or payment.amount_paise or 0)
    net = int(payment.amount_paise or 0)
    if gross <= 0:
        return 0
    if gross_refund_paise >= gross:
        return net
    return gross_refund_paise * net // gross


async def _balance_after(
    session: AsyncSession, *, organization_id: int, delta: int
) -> int:
    from api.services.billing.costing import current_balance_paise

    balance = await current_balance_paise(session, organization_id=organization_id)
    return balance + delta


async def refund_payment(
    session: AsyncSession,
    *,
    payment_id: int,
    amount_paise: int | None,
    reason: str | None,
    actor_user_id: int | None,
    now: datetime | None = None,
) -> RefundResult:
    """Refund a payment at the provider and reverse the credit it granted.

    ``amount_paise`` is gross and optional — omitted refunds everything still
    refundable.
    """
    moment = now or datetime.now(UTC)

    payment = await session.get(PaymentModel, payment_id)
    if payment is None:
        raise RefundError("No such payment.")
    if payment.status != "paid" or not payment.payment_id:
        raise RefundError(
            "That payment was never collected, so there is nothing to refund."
        )

    remaining = await refundable_paise(session, payment=payment)
    if remaining <= 0:
        raise RefundError("That payment has already been fully refunded.")

    requested = int(amount_paise) if amount_paise is not None else remaining
    if requested <= 0:
        raise RefundError("Refund an amount greater than zero.")
    if requested > remaining:
        # Against the payment's own gross, never a number from the request. A
        # refund of ten times the payment is one misplaced decimal, and the
        # provider will process it.
        raise RefundError(
            f"Only {remaining / 100:.2f} is left to refund on this payment."
        )

    reversal = _net_for(payment, requested)

    # The guard that matters most. Refunding a customer who has already spent
    # the credit hands back the money and keeps the service, and leaves a
    # negative balance that every reservation check downstream reads as an
    # account in good standing with a strange number.
    if (
        await _balance_after(
            session, organization_id=payment.organization_id, delta=-reversal
        )
        < 0
    ):
        raise RefundError(
            "That credit has already been spent. Refunding it would leave the "
            "account with a negative balance — reduce the amount, or write it "
            "off as an adjustment instead."
        )

    record = RefundModel(
        payment_id=payment.id,
        organization_id=payment.organization_id,
        amount_paise=requested,
        reversed_credit_paise=reversal,
        reason=reason,
        status="pending",
        created_by=actor_user_id,
        created_at=moment,
    )
    session.add(record)
    await session.flush()

    provider_refund_id = await _call_provider(payment.payment_id, requested)

    # Ledger only after the money has actually moved. The other order takes
    # credit off an account whose refund then failed; this order leaves, at
    # worst, a provider refund with no ledger row — which reconciliation
    # catches and a human can finish.
    balance = await _balance_after(
        session, organization_id=payment.organization_id, delta=0
    )
    session.add(
        CreditLedgerModel(
            organization_id=payment.organization_id,
            delta_paise=-reversal,
            kind=CreditLedgerKind.ADJUSTMENT.value,
            ref_type="refund",
            ref_id=str(record.id),
            balance_after_paise=balance - reversal,
            note=f"Refund of {payment.payment_id}",
            created_by=actor_user_id,
        )
    )
    record.status = "processed"
    record.provider_refund_id = provider_refund_id
    record.processed_at = moment
    await session.flush()

    # A referral award earned on this payment is taken back if it is still
    # held. Once matured it is somebody's spendable balance and reversing it
    # is a decision for a human, not a side effect of a refund.
    from api.services.billing import referrals

    await referrals.cancel_for_payment(session, payment_id=payment.payment_id)

    logger.info(
        "Refunded {} paise of payment {} ({} paise of credit reversed)",
        requested,
        payment.payment_id,
        reversal,
    )
    return RefundResult(
        refund_id=provider_refund_id,
        amount_paise=requested,
        reversed_credit_paise=reversal,
    )


async def _call_provider(provider_payment_id: str, amount_paise: int) -> str | None:
    """Ask Razorpay for the refund. Raises unless it is accepted.

    Returns None when Razorpay is not configured at all, which is the
    self-hosted and local case — the ledger reversal is still the useful half
    there, and refusing would make refunds impossible on a deployment that
    settles some other way.
    """
    if not (RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET):
        logger.warning(
            "Razorpay is not configured; reversing the credit for {} without "
            "moving money. Settle it with the customer separately.",
            provider_payment_id,
        )
        return None

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            response = await client.post(
                f"{RAZORPAY_API_BASE}/payments/{provider_payment_id}/refund",
                auth=(RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET),
                json={"amount": amount_paise, "speed": "normal"},
            )
    except httpx.HTTPError as exc:
        raise RefundError("Could not reach Razorpay. Nothing was refunded.") from exc

    if response.status_code >= 400:
        # The provider's own words. A refund refused for "payment too old to
        # refund" needs a different action from one refused for insufficient
        # balance in the settlement account, and a generic message hides which.
        detail = ""
        try:
            detail = response.json().get("error", {}).get("description", "")
        except Exception:
            detail = response.text[:200]
        logger.error("Razorpay refused a refund: {} {}", response.status_code, detail)
        raise RefundError(
            f"Razorpay refused the refund: {detail or response.status_code}"
        )

    try:
        return response.json().get("id")
    except Exception:
        return None
