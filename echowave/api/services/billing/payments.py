"""Taking money in — prepaid credit via Razorpay.

Decibyl sells prepaid credit, so this module only ever moves money *in*. There
is no refund or payout path here; a refund is issued from the Razorpay dashboard
and reflected with a staff credit adjustment, which keeps a second money-moving
code path out of the product entirely.

Four rules, each of which exists because breaking it is how payment integrations
lose money:

1. **Only a signature-verified webhook credits an account.** The browser
   reporting "payment succeeded" is not evidence — it is an unauthenticated
   client asserting it should be given credit.
2. **The webhook is idempotent.** Razorpay delivers at least once, not exactly
   once. A retry must be a no-op, enforced by a partial unique index on the
   payment id rather than by a check-then-write race.
3. **The amount comes from the order we created**, not from the payload. A
   payload claiming ₹50,000 against an order for ₹100 credits ₹100 and is
   logged.
4. **No webhook secret means no webhook.** The route refuses every request
   rather than accepting unverified ones. An unauthenticated credit endpoint
   lets anyone top up any account for free.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx
from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.constants import (
    MAX_TOPUP_PAISE,
    MIN_TOPUP_PAISE,
    RAZORPAY_API_BASE,
    RAZORPAY_KEY_ID,
    RAZORPAY_KEY_SECRET,
    RAZORPAY_WEBHOOK_SECRET,
)
from api.db.models import CreditLedgerModel, PaymentModel
from api.enums import CreditLedgerKind
from api.services.billing.billing_profile import get_profile
from api.services.billing.tax import TaxError, compute_tax

PROVIDER = "razorpay"

#: Razorpay works in paise for INR, which is the same unit the ledger uses, so
#: no conversion happens anywhere in this module. Asserted by a test, because a
#: factor-of-100 slip here would charge a hundred times too much.
RAZORPAY_WORKS_IN_PAISE = True

_TIMEOUT = httpx.Timeout(15.0)


class PaymentError(RuntimeError):
    """A payment could not be created or processed."""


class PaymentNotConfigured(PaymentError):
    """Razorpay credentials are missing.

    Deliberately distinct: "we are not set up to take money" is an operator
    problem with a clear fix, and should not read to a customer as "your card
    was declined".
    """


@dataclass(frozen=True)
class TopupOrder:
    """What the browser needs to open Razorpay's checkout.

    ``key_id`` is the publishable key and is safe to send. The API secret and
    the webhook secret never leave the server.

    Two amounts, and confusing them is a factor-of-1.18 error in either
    direction. ``amount_paise`` is the credit bought; ``gross_paise`` is what
    the card is charged, and it is the one that goes to Razorpay.
    """

    order_id: str
    amount_paise: int
    gross_paise: int
    tax_paise: int
    currency: str
    key_id: str


def _require_api_credentials() -> tuple[str, str]:
    if not RAZORPAY_KEY_ID or not RAZORPAY_KEY_SECRET:
        raise PaymentNotConfigured(
            "Razorpay is not configured. Set RAZORPAY_KEY_ID and "
            "RAZORPAY_KEY_SECRET on the API."
        )
    return RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET


async def create_topup_order(
    session: AsyncSession,
    *,
    organization_id: int,
    amount_paise: int,
    created_by: int | None,
) -> TopupOrder:
    """Ask Razorpay for an order and record our side of it.

    ``amount_paise`` is the **credit** the customer is buying, net of GST. The
    card is charged that plus tax. Keeping the argument net rather than gross is
    deliberate: every other amount in this codebase is net, and a single
    function taking a gross one would be the place someone eventually credits a
    tax-inclusive figure to the ledger.

    The row is written before the customer pays, so the webhook that arrives
    later has something to reconcile against and cannot be the first thing that
    tells us an order existed.
    """
    key_id, key_secret = _require_api_credentials()

    if amount_paise < MIN_TOPUP_PAISE:
        raise PaymentError(f"The minimum top-up is ₹{MIN_TOPUP_PAISE / 100:,.0f}.")
    if amount_paise > MAX_TOPUP_PAISE:
        raise PaymentError(
            f"The maximum top-up is ₹{MAX_TOPUP_PAISE / 100:,.0f}. "
            "Contact us for a larger amount."
        )

    profile = await get_profile(session, organization_id=organization_id)
    try:
        tax = compute_tax(
            taxable_paise=amount_paise,
            country_code=profile.country_code,
            state_code=profile.state_code,
        )
    except TaxError as exc:
        # An export with no LUT on file lands here. Surfaced rather than
        # swallowed: it is fixed by filing the LUT, not by charging the customer
        # something we cannot invoice.
        raise PaymentError(str(exc)) from exc

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            response = await client.post(
                f"{RAZORPAY_API_BASE}/orders",
                auth=(key_id, key_secret),
                json={
                    # Gross. Razorpay collects what the customer owes, tax
                    # included; the ledger gets the net figure separately.
                    "amount": tax.total_paise,
                    "currency": "INR",
                    # Lets Razorpay's own dashboard show which account paid and
                    # what the split was, which is the first thing anyone
                    # reconciling a settlement against a return looks for.
                    "notes": {
                        "organization_id": str(organization_id),
                        "credit_paise": str(amount_paise),
                        "tax_paise": str(tax.tax_paise),
                    },
                },
            )
    except httpx.HTTPError as exc:
        raise PaymentError("Could not reach Razorpay. Try again.") from exc

    if response.status_code >= 400:
        logger.error(
            "Razorpay order creation failed for org {}: {} {}",
            organization_id,
            response.status_code,
            response.text[:500],
        )
        raise PaymentError("Razorpay rejected the order. Try again.")

    payload = response.json()
    order_id = payload.get("id")
    if not order_id:
        raise PaymentError("Razorpay did not return an order id.")

    session.add(
        PaymentModel(
            organization_id=organization_id,
            provider=PROVIDER,
            order_id=order_id,
            amount_paise=amount_paise,
            gross_paise=tax.total_paise,
            cgst_paise=tax.cgst_paise,
            sgst_paise=tax.sgst_paise,
            igst_paise=tax.igst_paise,
            status="created",
            created_by=created_by,
        )
    )
    await session.flush()

    logger.info(
        "Top-up order {} created for org {}: {} paise credit, {} paise charged",
        order_id,
        organization_id,
        amount_paise,
        tax.total_paise,
    )
    return TopupOrder(
        order_id=order_id,
        amount_paise=amount_paise,
        gross_paise=tax.total_paise,
        tax_paise=tax.tax_paise,
        currency="INR",
        key_id=key_id,
    )


def verify_webhook_signature(*, raw_body: bytes, signature: str | None) -> bool:
    """Whether this request really came from Razorpay.

    HMAC-SHA256 over the **raw** body. Re-serialising the parsed JSON would
    change the bytes and fail every time, so the caller must pass what arrived
    on the wire.

    Compared with :func:`hmac.compare_digest`, not ``==``: a byte-at-a-time
    comparison leaks where the first difference is, and that is enough to
    recover a signature given enough attempts.
    """
    if not RAZORPAY_WEBHOOK_SECRET:
        logger.error(
            "A Razorpay webhook arrived but RAZORPAY_WEBHOOK_SECRET is unset, "
            "so it cannot be verified. Refusing it — an unverified webhook is "
            "an open credit endpoint."
        )
        return False
    if not signature:
        return False

    expected = hmac.new(
        RAZORPAY_WEBHOOK_SECRET.encode(),
        raw_body,
        hashlib.sha256,
    ).hexdigest()
    if hmac.compare_digest(expected, signature):
        return True

    # A rejected webhook is nearly undebuggable without this. The two ways it
    # fails look identical from outside — a mismatched secret, and a body that
    # was altered in transit by a proxy — and the fix for each is the opposite
    # of the fix for the other.
    #
    # Signature *prefixes* only, and a fingerprint of the secret rather than
    # the secret: enough to tell "different key" from "different bytes",
    # useless to anyone reading the logs. The body length is the tell for a
    # proxy that re-encoded or truncated the payload.
    logger.warning(
        "Razorpay signature mismatch: body={} bytes, received={}…, computed={}…, "
        "secret fingerprint={} (len {}). Equal prefixes with unequal signatures "
        "means the body changed in transit; different prefixes mean the secret "
        "here and the one on the webhook do not match.",
        len(raw_body),
        signature[:8],
        expected[:8],
        hashlib.sha256(RAZORPAY_WEBHOOK_SECRET.encode()).hexdigest()[:8],
        len(RAZORPAY_WEBHOOK_SECRET),
    )
    return False


def _payment_entity(event: dict) -> dict:
    """The payment object out of a webhook envelope, whatever the event."""
    payload = event.get("payload") or {}
    return (payload.get("payment") or {}).get("entity") or {}


async def handle_webhook(
    session: AsyncSession, *, raw_body: bytes, signature: str | None
) -> dict:
    """Process one verified Razorpay webhook.

    Returns a small summary for the response body. Never raises for an event we
    do not care about — Razorpay retries anything that is not a 2xx, and
    retrying an event we deliberately ignore would loop forever.
    """
    if not verify_webhook_signature(raw_body=raw_body, signature=signature):
        raise PaymentError("Signature verification failed")

    try:
        event = json.loads(raw_body)
    except ValueError as exc:
        raise PaymentError("Body is not JSON") from exc

    event_type = event.get("event") or ""
    entity = _payment_entity(event)
    payment_id = entity.get("id")
    order_id = entity.get("order_id")

    if event_type.startswith("subscription."):
        # Autopay. Routed rather than handled here: this module credits an
        # account, and a mandate moves no money — it is the standing permission
        # the monthly rental is collected under. Signature verification has
        # already happened above, which is the only thing that may change a
        # mandate's state.
        from api.services.billing.mandates import apply_subscription_event
        from api.services.billing.rentals import record_mandate_collection

        outcome = await apply_subscription_event(session, event=event)
        if outcome.get("charged"):
            # The bank collected. Recording the period here is what stops the
            # monthly cron debiting the prepaid balance for the same month —
            # two collection paths for one period is a double charge, and it is
            # the kind that looks correct in both ledgers separately.
            outcome["period"] = await record_mandate_collection(
                session, mandate_id=outcome["mandate_id"], event=event
            )
        return outcome

    if event_type not in {"payment.captured", "payment.failed"}:
        # Acknowledged, not acted on. Refunds and settlement events arrive here;
        # treating them as errors would have Razorpay retry them indefinitely.
        return {"status": "ignored", "event": event_type}

    if not payment_id or not order_id:
        raise PaymentError("Webhook is missing a payment or order id")

    payment = await session.scalar(
        select(PaymentModel).where(
            PaymentModel.provider == PROVIDER, PaymentModel.order_id == order_id
        )
    )
    if payment is None:
        # An order we never created. Logged rather than credited: this is either
        # a different environment sharing the webhook endpoint, or someone
        # probing it.
        logger.warning(
            "Razorpay webhook for unknown order {} (payment {})",
            order_id,
            payment_id,
        )
        return {"status": "unknown_order", "order_id": order_id}

    if event_type == "payment.failed":
        # Only mark a failure if nothing succeeded. A customer who retries and
        # succeeds can produce a late `failed` for the first attempt, and that
        # must not un-credit them.
        if payment.status != "paid":
            payment.status = "failed"
            payment.provider_payload = event
        return {"status": "failed", "order_id": order_id}

    # --- payment.captured ---

    if payment.status == "paid" and payment.payment_id == payment_id:
        # The idempotent path. Razorpay delivers at least once.
        return {"status": "already_credited", "order_id": order_id}

    paid_paise = int(entity.get("amount") or 0)

    # Two amounts, and the comparison must use the right one. Razorpay collected
    # the **gross** figure — credit plus tax — so that is what the payload is
    # checked against. What reaches the ledger is the **net** credit, because
    # the tax within the payment is the government's, not the customer's to
    # spend on calls. Comparing the payload against the net amount would reject
    # every correctly-taxed payment as an 18% overpayment.
    #
    # Falls back to the net amount for rows written before GST existed, where
    # gross and net were the same number.
    expected_gross = int(payment.gross_paise or payment.amount_paise)
    tax_paise = expected_gross - int(payment.amount_paise)

    if paid_paise <= 0:
        raise PaymentError("Refusing to credit a non-positive amount")

    if paid_paise > expected_gross:
        # A payload claiming more than the order. Capped, and loud: this is
        # either a tampered webhook or a genuine mismatch worth a human.
        logger.error(
            "Razorpay webhook amount {} exceeds order {} gross {}; crediting the "
            "order amount",
            paid_paise,
            order_id,
            expected_gross,
        )
        credited = int(payment.amount_paise)
    elif paid_paise < expected_gross:
        # A partial capture. Credit what was actually paid, net of the tax
        # proportion already accounted for, so an underpayment cannot buy full
        # credit.
        logger.error(
            "Razorpay webhook amount {} is short of order {} gross {}; crediting "
            "the shortfall net of tax",
            paid_paise,
            order_id,
            expected_gross,
        )
        credited = max(0, paid_paise - tax_paise)
    else:
        credited = int(payment.amount_paise)

    if credited <= 0:
        raise PaymentError("Refusing to credit a non-positive amount")

    balance = await current_balance_paise(
        session, organization_id=payment.organization_id
    )
    entry = CreditLedgerModel(
        organization_id=payment.organization_id,
        delta_paise=credited,
        kind=CreditLedgerKind.TOPUP.value,
        ref_type="payment",
        ref_id=payment_id,
        balance_after_paise=balance + credited,
        note=f"Razorpay {payment_id}",
    )
    session.add(entry)
    await session.flush()

    payment.payment_id = payment_id
    payment.status = "paid"
    payment.paid_at = datetime.now(UTC)
    payment.provider_payload = event
    payment.credit_ledger_id = entry.id
    await session.flush()

    # The advance is taxable on receipt, so the voucher belongs to this
    # transaction rather than to a later job. It returns None rather than
    # raising when the supplier identity is unconfigured: a payment that
    # succeeded must not be rolled back over a missing environment variable, and
    # the document can be issued afterwards.
    from api.services.billing.documents import issue_receipt_voucher

    voucher = await issue_receipt_voucher(session, payment=payment)

    logger.info(
        "Credited org {} with {} paise from Razorpay payment {}{}",
        payment.organization_id,
        credited,
        payment_id,
        f" (voucher {voucher.number})" if voucher else "",
    )
    return {
        "status": "credited",
        "order_id": order_id,
        "credited_paise": credited,
    }


async def current_balance_paise(session: AsyncSession, *, organization_id: int) -> int:
    """Balance derived from the ledger.

    Re-exported from the costing module so payment code has one obvious place
    to ask, and so nobody is tempted to add a cached balance column.
    """
    from api.services.billing.costing import current_balance_paise as _balance

    return await _balance(session, organization_id=organization_id)


async def list_payments(
    session: AsyncSession, *, organization_id: int, limit: int = 50
) -> list[dict]:
    """This account's top-up history, newest first."""
    rows = (
        await session.scalars(
            select(PaymentModel)
            .where(PaymentModel.organization_id == organization_id)
            .order_by(PaymentModel.created_at.desc())
            .limit(limit)
        )
    ).all()
    return [
        {
            "id": r.id,
            "order_id": r.order_id,
            "payment_id": r.payment_id,
            # Credit bought and total charged. Both shown, because a customer
            # reconciling a card statement is looking for the gross figure while
            # one reconciling their balance is looking for the net one.
            "amount_paise": r.amount_paise,
            "gross_paise": r.gross_paise
            if r.gross_paise is not None
            else r.amount_paise,
            "tax_paise": (
                int(r.gross_paise) - int(r.amount_paise)
                if r.gross_paise is not None
                else 0
            ),
            "status": r.status,
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "paid_at": r.paid_at.isoformat() if r.paid_at else None,
        }
        for r in rows
    ]


def is_configured() -> bool:
    """Whether top-ups can be taken, for the UI to say so plainly."""
    return bool(RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET)


def webhook_is_configured() -> bool:
    """Whether a payment could actually be credited.

    Separate from :func:`is_configured` because the failure it describes is
    worse and quieter: checkout would work, the customer would be charged, and
    nothing would credit them.
    """
    return bool(RAZORPAY_WEBHOOK_SECRET)
