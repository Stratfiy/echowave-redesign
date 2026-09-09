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

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from loguru import logger
from pydantic import BaseModel, Field

from api.constants import (
    GST_RATE_BASIS_POINTS,
    MAX_TOPUP_PAISE,
    MIN_BALANCE_PAISE,
    MIN_TOPUP_PAISE,
    REQUIRE_MANDATE_FOR_NUMBERS,
    TOPUP_INCREMENT_PAISE,
    UI_APP_URL,
)
from api.db import db_client
from api.db.models import UserModel
from api.enums import OrganizationRole, PostHogEvent
from api.services.auth.depends import get_user, require_organization_role
from api.services.billing import (
    auto_topup,
    auto_topup_runner,
    billing_profile,
    document_email,
    documents,
    payments,
)
from api.services.billing.tax import TaxError
from api.services.posthog_client import capture_event

router = APIRouter(prefix="/billing", tags=["billing"])


class TopupRequest(BaseModel):
    amount_paise: int = Field(
        ...,
        ge=MIN_TOPUP_PAISE,
        le=MAX_TOPUP_PAISE,
        multiple_of=TOPUP_INCREMENT_PAISE,
        description=(
            "Credit to buy, in paise, net of GST. ₹500 is 50000. Bought in "
            "steps of TOPUP_INCREMENT_PAISE. Tax is added on top of this at "
            "checkout."
        ),
    )


class BillingProfileRequest(BaseModel):
    """Who the invoice is made out to.

    Replaces the profile wholesale rather than patching field by field: a
    partial update leaving a stale state code beside a new GSTIN is exactly the
    inconsistency that changes which tax the customer is charged.
    """

    legal_name: str | None = Field(None, max_length=256)
    gstin: str | None = Field(None, max_length=15)
    address_line1: str | None = Field(None, max_length=256)
    address_line2: str | None = Field(None, max_length=256)
    city: str | None = Field(None, max_length=128)
    state_code: str | None = Field(
        None, max_length=2, description="Two-digit GST state code, e.g. 29"
    )
    postal_code: str | None = Field(None, max_length=16)
    country_code: str = Field("IN", min_length=2, max_length=2)
    billing_email: str | None = Field(None, max_length=320)


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
        profile = await billing_profile.get_profile(
            session, organization_id=organization_id
        )
        # This account's floor, not the deployment's. It is higher until the
        # first payment lands, and the picker has to offer what the API will
        # actually accept — otherwise a new account is shown ₹100, pays, and
        # is refused.
        min_topup = await payments.minimum_topup_paise(
            session, organization_id=organization_id
        )
    return {
        "balance_paise": balance,
        "topups_enabled": payments.is_configured() and payments.webhook_is_configured(),
        "min_topup_paise": min_topup,
        "max_topup_paise": MAX_TOPUP_PAISE,
        # The step the amount picker moves in, and the balance at which calling
        # stops. Both belong to the screen that has to explain why a call was
        # refused and what to do about it.
        "topup_increment_paise": TOPUP_INCREMENT_PAISE,
        "min_balance_paise": MIN_BALANCE_PAISE,
        # Whether calling is possible right now. Derived here rather than left
        # to the client to compare two numbers, so one definition of "blocked"
        # serves the banner, the button and the runtime that refuses the call.
        "calling_blocked": balance < MIN_BALANCE_PAISE,
        # So the screen can show what will actually be charged before the
        # customer clicks pay, rather than surprising them at the card form.
        "gst_rate_basis_points": 0 if profile.is_export else GST_RATE_BASIS_POINTS,
        "is_export": profile.is_export,
        "billing_profile_complete": profile.is_complete,
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

    def _report(event: str, **extra: Any) -> None:
        """One line per outcome of the pay attempt.

        Nothing tracked any of this, so a customer who tried to pay and could
        not looked exactly like one who never tried — the difference between a
        pricing problem and a broken checkout, and we could not see it.

        The amount rides along because a refused ₹500 and a refused ₹50,000 are
        different events commercially. Nothing identifying does: no order id
        against a person, no card, no contact.
        """
        capture_event(
            distinct_id=str(organization_id),
            event=event,
            properties={
                "organization_id": organization_id,
                "amount_paise": request.amount_paise,
                **extra,
            },
        )

    if not payments.webhook_is_configured():
        # Refused before the customer can pay, on purpose: without a webhook
        # secret the payment would succeed and never credit them. Counted,
        # because it is a configuration fault that presents as lost revenue.
        _report(PostHogEvent.TOPUP_FAILED, reason="webhook_not_configured")
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
            _report(PostHogEvent.TOPUP_FAILED, reason="payment_not_configured")
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except payments.PaymentError as exc:
            _report(PostHogEvent.TOPUP_FAILED, reason="payment_error")
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        await session.commit()

    # Started, not succeeded. The customer still has to get through Razorpay's
    # checkout, and the credit lands on the webhook — see the settlement path
    # for TOPUP_SUCCEEDED. Counting this as a completed payment would show a
    # funnel with no drop-off and hide the step most likely to have one.
    _report(PostHogEvent.TOPUP_STARTED, gross_paise=order.gross_paise)

    return {
        "order_id": order.order_id,
        # Credit bought, net of tax.
        "amount_paise": order.amount_paise,
        # What checkout must charge. The browser passes this to Razorpay; using
        # the net figure would collect 18% too little.
        "gross_paise": order.gross_paise,
        "tax_paise": order.tax_paise,
        "currency": order.currency,
        "key_id": order.key_id,
    }


@router.get("/profile")
async def get_billing_profile(user: UserModel = Depends(get_user)) -> dict[str, Any]:
    """Who this account's invoices are made out to."""
    organization_id = _organization_id(user)
    async with db_client.async_session() as session:
        profile = await billing_profile.get_profile(
            session, organization_id=organization_id
        )
    return {
        "profile": {
            "legal_name": profile.legal_name,
            "gstin": profile.gstin,
            "address_line1": profile.address_line1,
            "address_line2": profile.address_line2,
            "city": profile.city,
            "state_code": profile.state_code,
            "postal_code": profile.postal_code,
            "country_code": profile.country_code,
            "billing_email": profile.billing_email,
        },
        "is_complete": profile.is_complete,
        "is_export": profile.is_export,
        "gst_rate_basis_points": GST_RATE_BASIS_POINTS,
    }


@router.put("/profile")
async def save_billing_profile(
    request: BillingProfileRequest,
    user: UserModel = Depends(require_organization_role(OrganizationRole.ADMIN)),
) -> dict[str, Any]:
    """Set who invoices are made out to, and therefore which tax applies."""
    organization_id = _organization_id(user)
    async with db_client.async_session() as session:
        try:
            profile = await billing_profile.save_profile(
                session,
                organization_id=organization_id,
                **request.model_dump(),
            )
        except TaxError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        await session.commit()

    return {
        "profile": {
            "legal_name": profile.legal_name,
            "gstin": profile.gstin,
            "address_line1": profile.address_line1,
            "address_line2": profile.address_line2,
            "city": profile.city,
            "state_code": profile.state_code,
            "postal_code": profile.postal_code,
            "country_code": profile.country_code,
            "billing_email": profile.billing_email,
        },
        "is_complete": profile.is_complete,
        "is_export": profile.is_export,
    }


@router.get("/documents")
async def list_tax_documents(user: UserModel = Depends(get_user)) -> dict[str, Any]:
    """Receipt vouchers and tax invoices issued to this account."""
    organization_id = _organization_id(user)
    async with db_client.async_session() as session:
        issued = await documents.list_documents(
            session, organization_id=organization_id
        )
    return {
        "documents": [
            {
                "id": d.id,
                "kind": d.kind,
                "number": d.number,
                "issued_at": d.issued_at,
                "period_start": d.period_start,
                "period_end": d.period_end,
                "taxable_paise": d.taxable_paise,
                "cgst_paise": d.cgst_paise,
                "sgst_paise": d.sgst_paise,
                "igst_paise": d.igst_paise,
                "total_paise": d.total_paise,
                "supply_type": d.supply_type,
            }
            for d in issued
        ]
    }


def _document_view(document) -> dict[str, Any]:
    """One issued document as a dict.

    Shared by the JSON route and the printable one so there is a single
    description of what a document contains — two would drift, and the pair
    that must never disagree is the one a customer reads and the one they file.
    """
    return {
        "id": document.id,
        "kind": document.kind,
        "number": document.number,
        "financial_year": document.financial_year,
        "issued_at": document.issued_at.isoformat() if document.issued_at else None,
        "period_start": (
            document.period_start.isoformat() if document.period_start else None
        ),
        "period_end": (
            document.period_end.isoformat() if document.period_end else None
        ),
        "taxable_paise": int(document.taxable_paise),
        "cgst_paise": int(document.cgst_paise or 0),
        "sgst_paise": int(document.sgst_paise or 0),
        "igst_paise": int(document.igst_paise or 0),
        "total_paise": int(document.total_paise),
        "supply_type": document.supply_type,
        "place_of_supply": document.place_of_supply,
        "rate_basis_points": document.rate_basis_points,
        "supplier": document.supplier_snapshot,
        "customer": document.customer_snapshot,
        "line_items": document.line_items,
    }


async def _load_document(document_id: int, user: UserModel):
    """Fetch one document for its owner, or 404.

    Org-scoped deliberately: a document id is a small integer, and an invoice
    names a legal entity and states what it spends.
    """
    organization_id = _organization_id(user)
    async with db_client.async_session() as session:
        document = await documents.get_document(
            session, organization_id=organization_id, document_id=document_id
        )
    if document is None:
        raise HTTPException(status_code=404, detail="Document not found")
    return document


@router.get("/documents/{document_id}")
async def get_tax_document(
    document_id: int, user: UserModel = Depends(get_user)
) -> dict[str, Any]:
    """One document in full, including both parties as of issue."""
    return _document_view(await _load_document(document_id, user))


@router.post("/documents/{document_id}/email")
async def email_tax_document_again(
    document_id: int, user: UserModel = Depends(get_user)
) -> dict[str, Any]:
    """Send a document to the account's billing email, on request.

    Exists because the send at issue time is a single best-effort attempt with
    no retry, and it has one failure mode nobody can recover from: a document
    issued before the account filled in its billing profile has no address on
    it, so it is never sent and there is no way to ask for it again. Four were
    issued that way on the live deployment before anybody noticed -- correct
    documents, downloadable, that the paying customer never received.

    Re-sends rather than re-issues. The document is unchanged: same number,
    same snapshot, same PDF. Only the delivery is repeated, which is why this
    is safe to call more than once.
    """
    document = await _load_document(document_id, user)

    if not document_email.email_is_configured():
        raise HTTPException(
            status_code=409,
            detail="Email is not configured on this deployment.",
        )

    async with db_client.async_session() as session:
        recipient = await document_email.resolve_recipient(session, document)

    if not recipient:
        raise HTTPException(
            status_code=409,
            detail=(
                "No billing email on file. Add one to your billing profile, "
                "then send this document again."
            ),
        )

    result = await document_email.send_document(
        document,
        recipient=recipient,
        pdf_bytes=document_email.render_pdf(document),
    )
    if not result.ok:
        raise HTTPException(
            status_code=502,
            detail=f"Could not send the document: {result.error}",
        )

    return {"sent": True, "to": recipient, "number": document.number}


@router.get("/documents/{document_id}/pdf")
async def get_tax_document_pdf(
    document_id: int, user: UserModel = Depends(get_user)
) -> Response:
    """The same document as :func:`get_tax_document`, rendered to PDF.

    Org-scoped the same way — a document id is a small integer.
    """
    document = await _load_document(document_id, user)

    # Deferred, like the one in tasks/email_tax_document.py: this router is on
    # the API's import path, and document_pdf builds its styles from reportlab
    # at import time. A module-level import would let a missing or broken PDF
    # library stop the whole API from starting rather than just failing this
    # one download.
    from api.services.billing.document_pdf import render_document_pdf

    pdf_bytes = render_document_pdf(document)
    filename = document.number.replace("/", "-")

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}.pdf"'},
    )


@router.get("/payments")
async def list_payments(user: UserModel = Depends(get_user)) -> dict[str, Any]:
    """This account's top-up history."""
    organization_id = _organization_id(user)
    async with db_client.async_session() as session:
        rows = await payments.list_payments(session, organization_id=organization_id)
    return {"payments": rows}


# ---------------------------------------------------------------------------
# Autopay
#
# The mandate is the standing authorisation the monthly number rental is
# collected under. These routes create it and report on it; nothing here moves
# money, and nothing here can mark a mandate authorised — only the
# signature-verified webhook does that, because the state is the difference
# between a number we can bill for and one we cannot.
# ---------------------------------------------------------------------------


def _mandate_view(mandate) -> dict[str, Any] | None:
    if mandate is None:
        return None
    from api.enums import MandateStatus

    return {
        "id": mandate.id,
        "status": mandate.status,
        "authorised": mandate.status in MandateStatus.authorised(),
        # Where the customer completes the authorisation. Provider-hosted and
        # short-lived, so it is returned every time rather than cached anywhere.
        "authorisation_url": mandate.short_url,
        "price_paise": mandate.price_paise,
        "authorised_at": (
            mandate.authorised_at.isoformat() if mandate.authorised_at else None
        ),
        "last_charged_at": (
            mandate.last_charged_at.isoformat() if mandate.last_charged_at else None
        ),
        "last_failure_reason": mandate.last_failure_reason,
    }


@router.get("/mandate")
async def get_mandate(user: UserModel = Depends(get_user)) -> dict[str, Any]:
    """This account's autopay mandate, if it has one.

    Returns ``mandate: null`` rather than a 404 for an account that has never
    started one: "you have no mandate" is the normal state before a first
    number, not an error.
    """
    organization_id = _organization_id(user)
    from api.services.billing import mandates as mandate_service
    from api.services.billing import rentals

    async with db_client.async_session() as session:
        mandate = await mandate_service.get_mandate(
            session, organization_id=organization_id
        )
        # The price the buy-a-number screen quotes. Served from the same
        # resolver the charge itself uses, because the screen previously
        # carried its own hardcoded figure and it drifted: it read Rs349 for
        # months while every account was charged NUMBER_RENTAL_PRICE_PAISE
        # (Rs559). A quote a customer is shown before authorising a standing
        # instruction has to come from the thing that bills them.
        number_price_paise = await rentals.next_number_price_paise(
            session, organization_id=organization_id
        )
    return {
        "mandate": _mandate_view(mandate),
        "required_for_numbers": REQUIRE_MANDATE_FOR_NUMBERS,
        "configured": mandate_service.is_configured(),
        "number_price_paise": number_price_paise,
    }


@router.post("/mandate")
async def create_mandate(
    user: UserModel = Depends(require_organization_role(OrganizationRole.ADMIN)),
) -> dict[str, Any]:
    """Start autopay, and hand back the link where it is authorised.

    Idempotent: an account that already has a live mandate gets that one back
    rather than a second. A customer who closes the authorisation page and
    comes back must not end up with two banks collecting for the same number.
    """
    organization_id = _organization_id(user)
    from api.services.billing import mandates as mandate_service

    async with db_client.async_session() as session:
        try:
            mandate = await mandate_service.create_rental_mandate(
                session, organization_id=organization_id
            )
        except mandate_service.MandateNotConfigured as exc:
            # 503, not 400: nothing the customer did is wrong, and the
            # distinction is what stops a support conversation starting with
            # "your card was declined".
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except mandate_service.MandateError as exc:
            logger.error(
                "Could not create a mandate for org {}: {}", organization_id, exc
            )
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        await session.commit()
        return {"mandate": _mandate_view(mandate)}


@router.get("/plan")
async def get_plan(user: UserModel = Depends(get_user)) -> dict[str, Any]:
    """Every plan on sale, what each includes, and which one this account is on.

    The prices come from the plans table and are returned rather than hardcoded
    in the client, because a screen quoting a figure the server would not
    collect is the class of bug the create-agent wizard already had.

    ``gross_paise`` is what the bank will actually be told to take: an export
    account with an LUT on file is charged the net figure, everyone else pays it
    plus GST. Null rather than guessed when the profile cannot be taxed, so the
    screen says "complete your billing details" instead of quoting a number that
    changes at the card form.
    """
    from api.services.billing import mandates as mandate_service
    from api.services.billing import subscription_plans
    from api.services.billing.tax import TaxError, gross_up

    organization_id = _organization_id(user)
    async with db_client.async_session() as session:
        await subscription_plans.ensure_seeded(session)
        await session.commit()
        plans = await subscription_plans.list_plans(session)
        mandate = await mandate_service.get_mandate(
            session,
            organization_id=organization_id,
            purpose=mandate_service.PURPOSE_STARTER_PLAN,
        )
        profile = await billing_profile.get_profile(
            session, organization_id=organization_id
        )
        # How many numbers the account already holds against its plan, so the
        # screen can say "1 of 1 used" rather than leaving the customer to
        # discover the entitlement is spent when the next one bills separately.
        from api.services.billing import rentals

        numbers_used = 0
        covered = 0
        if mandate is not None:
            covered = await rentals.numbers_a_mandate_covers(session, mandate=mandate)
            numbers_used = await rentals.numbers_on_mandate(
                session, mandate_id=mandate.id
            )
        next_number_paise = await rentals.next_number_price_paise(
            session, organization_id=organization_id
        )

    def _priced(plan) -> dict[str, Any]:
        try:
            gross_paise: int | None = gross_up(
                taxable_paise=plan.price_paise,
                country_code=profile.country_code,
                state_code=profile.state_code,
            )
        except TaxError:
            gross_paise = None
        return {
            # Every figure net, like the ledger, except gross_paise which says
            # so in its name.
            "code": plan.code,
            "label": plan.label,
            "blurb": plan.blurb,
            "price_paise": plan.price_paise,
            "gross_paise": gross_paise,
            "balance_paise": plan.balance_paise,
            "included_numbers": plan.included_numbers,
            "extra_number_paise": plan.extra_number_price_paise,
            "period": "monthly",
            "is_current": mandate is not None
            and (mandate.plan_code or subscription_plans.STARTER) == plan.code,
        }

    priced = [_priced(plan) for plan in plans]
    current = next((p for p in priced if p["is_current"]), None)

    return {
        "plans": priced,
        # The plan this account is on, hoisted so a screen does not have to
        # scan. Null when they are on none.
        "current": current,
        # Kept for the shape the previous single-plan client expects: the
        # account's plan if they have one, else the first on sale.
        "plan": current or (priced[0] if priced else None),
        "numbers": {
            "included": covered,
            "used": numbers_used,
            "remaining": max(0, covered - numbers_used),
            # What number N+1 costs a month, which is the figure the "add
            # another number" screen has to show.
            "extra_paise": next_number_paise,
        },
        "mandate": _mandate_view(mandate),
        "configured": mandate_service.is_configured(),
        "billing_profile_complete": profile.is_complete,
    }


class SubscribeRequest(BaseModel):
    """Which plan to start. Omitted means the starter plan, which is what the
    single-plan client sent before there was a choice."""

    plan_code: str | None = None


@router.post("/plan")
async def subscribe_to_plan(
    payload: SubscribeRequest | None = None,
    user: UserModel = Depends(require_organization_role(OrganizationRole.ADMIN)),
) -> dict[str, Any]:
    """Start the starter plan, and hand back the link where it is authorised.

    Nothing is collected here and no balance is granted here. The first cycle
    is collected by the customer's bank when it falls due, and the balance
    arrives with it — see ``services/billing/plans.py``. Idempotent for the
    same reason the rental mandate is: a customer who closes the authorisation
    page and comes back must not end up with two banks collecting for one plan.
    """
    organization_id = _organization_id(user)
    from api.services.billing import mandates as mandate_service

    async with db_client.async_session() as session:
        # An account already renting a number on its own mandate would end up
        # paying for that number twice — once in the rental mandate, once
        # inside the plan. Refused rather than silently reconciled, because
        # which of the two they meant to keep is their decision.
        rental = await mandate_service.get_mandate(
            session,
            organization_id=organization_id,
            purpose=mandate_service.PURPOSE_NUMBER_RENTAL,
        )
        if rental is not None:
            raise HTTPException(
                status_code=409,
                detail=(
                    "This account already has a number rental on autopay. "
                    "Cancel it before starting the plan, so the number is not "
                    "paid for twice."
                ),
            )

        from api.services.billing import subscription_plans

        await subscription_plans.ensure_seeded(session)
        plan = await subscription_plans.resolve(
            session, code=(payload.plan_code if payload else None)
        )
        if plan is None or not plan.enabled:
            raise HTTPException(status_code=404, detail="That plan is not on sale.")

        try:
            mandate = await mandate_service.create_plan_mandate(
                session, organization_id=organization_id, plan=plan
            )
        except mandate_service.MandateNotConfigured as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except mandate_service.MandateError as exc:
            logger.error(
                "Could not start the plan for org {}: {}", organization_id, exc
            )
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        await session.commit()
        return {"mandate": _mandate_view(mandate)}


@router.post("/mandate/cancel")
async def cancel_mandate(
    user: UserModel = Depends(require_organization_role(OrganizationRole.ADMIN)),
) -> dict[str, Any]:
    """Withdraw the standing authorisation.

    The number is not released here and the rental does not stop — the charge
    falls back to the prepaid balance and the dunning schedule, which is
    exactly what that schedule was written for. Releasing a number because
    someone turned autopay off would be a far worse surprise than a low-balance
    warning.
    """
    organization_id = _organization_id(user)
    from api.services.billing import mandates as mandate_service

    async with db_client.async_session() as session:
        try:
            mandate = await mandate_service.cancel_mandate(
                session, organization_id=organization_id
            )
        except mandate_service.MandateError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        await session.commit()
    if mandate is None:
        raise HTTPException(status_code=404, detail="No autopay mandate to cancel")
    return {"mandate": _mandate_view(mandate)}


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

    # A prepaid top-up reports its voucher at the top level; an autopay
    # collection reports it under "voucher", because that branch settles a
    # mandate rather than crediting an order. Both are money the customer's
    # account was debited for, and both owe them the document.
    voucher_id = result.get("receipt_voucher_id")
    if voucher_id is None:
        voucher_id = (result.get("voucher") or {}).get("id")
    if voucher_id is not None:
        # After commit, not inside the transaction: an enqueue must not survive
        # a rollback of the payment it is about, and the reverse (a committed
        # voucher whose email never got enqueued because Redis hiccupped) is a
        # missing email, not a missing receipt -- the acceptable failure mode.
        from api.tasks.arq import enqueue_job
        from api.tasks.email_tax_document import email_tax_document_job_id
        from api.tasks.function_names import FunctionNames

        await enqueue_job(
            FunctionNames.EMAIL_TAX_DOCUMENT,
            voucher_id,
            _job_id=email_tax_document_job_id(voucher_id),
        )

    # The customer authorised at their bank and, until this shipped, heard
    # nothing until money moved — which on a monthly cycle can be weeks, and
    # arrives as a debit. Sent after commit for the same reason the receipt is:
    # a confirmation must not survive a rollback of the authorisation it
    # confirms.
    if result.get("newly_authorised"):
        await _confirm_plan_authorisation(result)

    return result


async def _confirm_plan_authorisation(result: dict[str, Any]) -> None:
    """Tell the account what its bank has just agreed to pay us, monthly.

    Best-effort and self-contained: the authorisation is already recorded, and
    a mail server having a bad minute must not turn a 2xx into a retry storm
    from Razorpay.
    """
    from api.services.billing import plan_email, subscription_plans
    from api.services.messaging import announce

    organization_id = result.get("organization_id")
    mandate_id = result.get("mandate_id")
    if organization_id is None or mandate_id is None:
        return

    try:
        plan = None
        plan_code = result.get("plan_code")
        if plan_code:
            async with db_client.async_session() as session:
                # None when the row was renamed or withdrawn after somebody
                # subscribed to it. The confirmation is owed either way — the
                # bank does not care that our catalogue moved.
                plan = await subscription_plans.get_plan(session, code=plan_code)

        await announce.announce(
            organization_id=organization_id,
            kind=plan_email.KIND,
            sender="billing",
            notice=plan_email.compose(
                plan=plan,
                mandate_id=mandate_id,
                authorised_paise=int(result.get("price_paise") or 0),
                app_url=UI_APP_URL,
            ),
        )
    except Exception as exc:  # noqa: BLE001 — see docstring
        logger.error(
            "Could not confirm the plan authorisation for mandate {}: {}",
            mandate_id,
            exc,
        )


class AutoTopupRequest(BaseModel):
    """What the customer is choosing. Amounts are net of GST, like every other
    amount a customer enters — the card is charged that plus tax, exactly as a
    manual top-up is."""

    enabled: bool
    amount_paise: int = Field(
        ...,
        ge=MIN_TOPUP_PAISE,
        le=MAX_TOPUP_PAISE,
        description="Credit to buy each time, in paise, net of GST.",
    )
    trigger_days: int = Field(
        auto_topup.DEFAULT_TRIGGER_DAYS,
        ge=1,
        le=30,
        description=(
            "Top up when this many days of credit remain, judged on recent "
            "spending. The debit runs a clear day after we notify you, so this "
            "needs to be more than one."
        ),
    )
    trigger_paise: int = Field(
        auto_topup.DEFAULT_TRIGGER_PAISE,
        ge=0,
        description=(
            "Also top up below this balance outright, for when there is not "
            "enough recent spending to judge runway from."
        ),
    )
    monthly_cap_paise: int = Field(
        0,
        ge=0,
        description="Never spend more than this in a calendar month. 0 is no money cap.",
    )
    max_per_month: int = Field(
        auto_topup.DEFAULT_MAX_PER_MONTH,
        ge=1,
        le=31,
        description="Never top up more than this many times in a calendar month.",
    )


def _settings_response(row, token, pending) -> dict[str, Any]:
    return {
        "enabled": bool(row.enabled) if row else False,
        "amount_paise": int(row.amount_paise) if row else 0,
        "trigger_days": int(row.trigger_days)
        if row
        else auto_topup.DEFAULT_TRIGGER_DAYS,
        "trigger_paise": (
            int(row.trigger_paise) if row else auto_topup.DEFAULT_TRIGGER_PAISE
        ),
        "monthly_cap_paise": int(row.monthly_cap_paise) if row else 0,
        "max_per_month": (
            int(row.max_per_month) if row else auto_topup.DEFAULT_MAX_PER_MONTH
        ),
        # Why nothing is happening, when nothing is happening. A toggle that
        # reads "on" while a run of declines has stopped it is the worst of both.
        "paused_reason": row.paused_reason if row else None,
        "instrument": (
            {
                "method": token.method,
                "hint": token.instrument_hint,
                "max_amount_paise": token.max_amount_paise,
            }
            if token
            else None
        ),
        "pending": (
            {
                "amount_paise": pending.amount_paise,
                "notified_at": pending.notified_at,
                "charge_after": pending.charge_after,
                "status": pending.status,
            }
            if pending
            else None
        ),
        "notice_hours": auto_topup.NOTICE_HOURS,
        "minimum_paise": MIN_TOPUP_PAISE,
    }


@router.get("/auto-topup")
async def get_auto_topup(user: UserModel = Depends(get_user)) -> dict[str, Any]:
    """The standing instruction, the instrument behind it, and any debit due.

    All three together because they are one question — "will my balance be kept
    up, and how" — and answering it from three calls invites a screen that shows
    a confident toggle above a missing payment method.
    """
    organization_id = _organization_id(user)
    async with db_client.async_session() as session:
        row = await auto_topup_runner.load_settings(
            session, organization_id=organization_id
        )
        token = await payments.active_token(session, organization_id=organization_id)
        pending = await auto_topup_runner.pending_attempt(
            session, organization_id=organization_id
        )
        return _settings_response(row, token, pending)


@router.put("/auto-topup")
async def save_auto_topup(
    request: AutoTopupRequest,
    user: UserModel = Depends(require_organization_role(OrganizationRole.ADMIN)),
) -> dict[str, Any]:
    """Set it. Admin-gated, because it authorises money to leave.

    Saving always clears ``paused_reason``: a customer who has come here and
    changed something is telling us to try again, and leaving them paused with
    no way to resume would mean support tickets for a self-service action.
    """
    organization_id = _organization_id(user)
    async with db_client.async_session() as session:
        row = await auto_topup_runner.load_settings(
            session, organization_id=organization_id
        )
        if row is None:
            from api.db.models import AutoTopupSettingModel

            row = AutoTopupSettingModel(organization_id=organization_id)
            session.add(row)

        if request.enabled:
            token = await payments.active_token(
                session, organization_id=organization_id
            )
            if token is None:
                # Refused rather than saved-and-silent. An enabled setting with
                # no instrument behind it is a promise the product cannot keep,
                # and the customer would only find out by running out of credit.
                raise HTTPException(
                    status_code=400,
                    detail=(
                        "There is no saved payment method to charge. Make one "
                        "top-up and choose to save the method for automatic "
                        "top-ups, then switch this on."
                    ),
                )
            if (
                token.max_amount_paise is not None
                and request.amount_paise > token.max_amount_paise
            ):
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"Your bank authorised us for at most "
                        f"₹{token.max_amount_paise / 100:,.0f} per charge. "
                        "Choose a smaller amount, or authorise a new method."
                    ),
                )

        row.enabled = request.enabled
        row.amount_paise = request.amount_paise
        row.trigger_days = request.trigger_days
        row.trigger_paise = request.trigger_paise
        row.monthly_cap_paise = request.monthly_cap_paise
        row.max_per_month = request.max_per_month
        row.paused_reason = None
        await session.commit()

        token = await payments.active_token(session, organization_id=organization_id)
        pending = await auto_topup_runner.pending_attempt(
            session, organization_id=organization_id
        )
        return _settings_response(row, token, pending)


@router.post("/auto-topup/cancel-pending")
async def cancel_pending_auto_topup(
    user: UserModel = Depends(require_organization_role(OrganizationRole.ADMIN)),
) -> dict[str, Any]:
    """Stop a debit we have announced but not yet taken.

    The notice we send says the customer can stop it, so there has to be
    something that does. Only a scheduled attempt can be cancelled — once it is
    ``charging`` the money may already be moving, and cancelling a row would
    hide a charge rather than prevent one.
    """
    organization_id = _organization_id(user)
    async with db_client.async_session() as session:
        pending = await auto_topup_runner.pending_attempt(
            session, organization_id=organization_id
        )
        if pending is None:
            return {"cancelled": False, "detail": "Nothing is scheduled."}
        if pending.status != auto_topup_runner.SCHEDULED:
            raise HTTPException(
                status_code=409,
                detail="That top-up is already being charged and cannot be stopped.",
            )
        pending.status = auto_topup_runner.CANCELLED
        await session.commit()
        return {"cancelled": True}
