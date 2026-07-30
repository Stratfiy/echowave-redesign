"""Prepaid top-ups.

Two audiences in one file, with deliberately different gates:

* ``/billing/*`` is the signed-in customer buying credit and looking at what
  they bought. Ordinary user auth, scoped to their selected organization.
* ``/billing/razorpay/webhook`` is Razorpay's server. It carries no session and
  cannot, so it is **unauthenticated but signature-gated** — the HMAC over the
  raw body is the whole of its authentication. This is the one route in the
  product where a missing environment variable must produce a hard refusal
  rather than a degraded mode: an unverified credit endpoint is an open one.

The webhook reads ``await request.body()`` rather than taking a parsed model.
Signature verification is over the exact bytes Razorpay signed, and FastAPI
re-serialising the JSON would change them — different key order, different
whitespace — and fail every request.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from loguru import logger
from pydantic import BaseModel, Field

from api.constants import MAX_TOPUP_PAISE, MIN_TOPUP_PAISE
from api.db import db_client
from api.db.models import UserModel
from api.services.auth.depends import get_user
from api.services.billing import payments

router = APIRouter(prefix="/billing", tags=["billing"])


class TopupRequest(BaseModel):
    amount_paise: int = Field(
        ...,
        ge=MIN_TOPUP_PAISE,
        le=MAX_TOPUP_PAISE,
        description="Amount to add, in paise. ₹500 is 50000.",
    )


def _organization_id(user: UserModel) -> int:
    if not user.selected_organization_id:
        raise HTTPException(status_code=400, detail="No organization selected")
    return user.selected_organization_id


@router.get("/balance")
async def get_balance(user: UserModel = Depends(get_user)) -> dict[str, Any]:
    """Current credit, and whether we can sell more of it.

    ``topups_enabled`` is false when Razorpay keys are missing *or* when the
    webhook secret is — checkout without a webhook charges the customer and
    credits nobody, which is worse than an honest "unavailable".
    """
    organization_id = _organization_id(user)
    async with db_client.async_session() as session:
        balance = await payments.current_balance_paise(
            session, organization_id=organization_id
        )
    return {
        "balance_paise": balance,
        "topups_enabled": payments.is_configured() and payments.webhook_is_configured(),
        "min_topup_paise": MIN_TOPUP_PAISE,
        "max_topup_paise": MAX_TOPUP_PAISE,
    }


@router.post("/topup")
async def create_topup(
    request: TopupRequest, user: UserModel = Depends(get_user)
) -> dict[str, Any]:
    """Open a Razorpay order for this account.

    Refuses when no webhook secret is set. The order would otherwise succeed,
    the customer would pay, and nothing would ever credit them — a failure that
    is invisible until someone complains.
    """
    organization_id = _organization_id(user)

    if not payments.webhook_is_configured():
        raise HTTPException(
            status_code=503,
            detail="Top-ups are temporarily unavailable. Please contact support.",
        )

    async with db_client.async_session() as session:
        try:
            order = await payments.create_topup_order(
                session,
                organization_id=organization_id,
                amount_paise=request.amount_paise,
                created_by=user.id,
            )
        except payments.PaymentNotConfigured as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except payments.PaymentError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        await session.commit()

    return {
        "order_id": order.order_id,
        "amount_paise": order.amount_paise,
        "currency": order.currency,
        "key_id": order.key_id,
    }


@router.get("/payments")
async def list_payments(user: UserModel = Depends(get_user)) -> dict[str, Any]:
    """This account's top-up history."""
    organization_id = _organization_id(user)
    async with db_client.async_session() as session:
        rows = await payments.list_payments(session, organization_id=organization_id)
    return {"payments": rows}


@router.post("/razorpay/webhook", include_in_schema=False)
async def razorpay_webhook(
    request: Request,
    x_razorpay_signature: str | None = Header(default=None),
) -> dict[str, Any]:
    """Credit an account from a verified Razorpay event.

    Returns 400 on a bad signature and 2xx on everything we accept, including
    events we deliberately ignore — Razorpay retries any non-2xx, so returning
    an error for an event we will never act on retries it forever.

    Kept out of the OpenAPI schema: it is not part of the customer API, and
    publishing it only advertises an endpoint to probe.
    """
    raw_body = await request.body()

    async with db_client.async_session() as session:
        try:
            result = await payments.handle_webhook(
                session, raw_body=raw_body, signature=x_razorpay_signature
            )
        except payments.PaymentError as exc:
            logger.warning("Rejected a Razorpay webhook: {}", exc)
            # Deliberately vague. A precise reason tells whoever is probing
            # which part of the request to change next.
            raise HTTPException(status_code=400, detail="Invalid webhook") from exc
        await session.commit()

    return result
