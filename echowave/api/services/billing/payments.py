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
    """

    order_id: str
    amount_paise: int
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

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            response = await client.post(
                f"{RAZORPAY_API_BASE}/orders",
                auth=(key_id, key_secret),
                json={
                    "amount": amount_paise,
                    "currency": "INR",
                    # Lets Razorpay's own dashboard show which account paid,
                    # which is the first thing anyone reconciling looks for.
                    "notes": {"organization_id": str(organization_id)},
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
            status="created",
            created_by=created_by,
        )
    )
    await session.flush()

    logger.info(
        "Top-up order {} created for org {} ({} paise)",
        order_id,
        organization_id,
        amount_paise,
    )
    return TopupOrder(
        order_id=order_id,
        amount_paise=amount_paise,
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
    return hmac.compare_digest(expected, signature)


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

    if event_type not in {"payment.captured", "payment.failed"}:
        # Acknowledged, not acted on. Subscriptions, refunds, settlement events
        # all arrive here; treating them as errors would have Razorpay retry
        # them indefinitely.
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
    # Credit the lesser of the two, which is safe in both directions: a payload
    # claiming more than the order is capped at the order (an inflated amount
    # would otherwise be free credit), and a partial capture is capped at what
    # was actually paid.
    credited = min(paid_paise, payment.amount_paise) if paid_paise else 0
    if paid_paise != payment.amount_paise:
        logger.error(
            "Razorpay webhook amount {} does not match order {} amount {}; "
            "crediting {}",
            paid_paise,
            order_id,
            payment.amount_paise,
            credited,
        )

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

    logger.info(
        "Credited org {} with {} paise from Razorpay payment {}",
        payment.organization_id,
        credited,
        payment_id,
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
            "amount_paise": r.amount_paise,
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
