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
    MIN_TOPUP_PAISE,
    REQUIRE_MANDATE_FOR_NUMBERS,
)
from api.db import db_client
from api.db.models import UserModel
from api.enums import OrganizationRole
from api.services.auth.depends import get_user, require_organization_role
from api.services.billing import billing_profile, document_email, documents, payments
from api.services.billing.tax import TaxError

router = APIRouter(prefix="/billing", tags=["billing"])


class TopupRequest(BaseModel):
    amount_paise: int = Field(
        ...,
        ge=MIN_TOPUP_PAISE,
        le=MAX_TOPUP_PAISE,
        description=(
            "Credit to buy, in paise, net of GST. ₹500 is 50000. Tax is added "
            "on top of this at checkout."
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
    return {
        "balance_paise": balance,
        "topups_enabled": payments.is_configured() and payments.webhook_is_configured(),
        "min_topup_paise": MIN_TOPUP_PAISE,
        "max_topup_paise": MAX_TOPUP_PAISE,
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

    async with db_client.async_session() as session:
        mandate = await mandate_service.get_mandate(
            session, organization_id=organization_id
        )
    return {
        "mandate": _mandate_view(mandate),
        "required_for_numbers": REQUIRE_MANDATE_FOR_NUMBERS,
        "configured": mandate_service.is_configured(),
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
            logger.error("Could not create a mandate for org {}: {}", organization_id, exc)
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

    voucher_id = result.get("receipt_voucher_id")
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

    return result
