"""The partner queue, from our side.

Behind ``get_superuser`` at the router, so every route here is staff-only by
construction rather than one decorator at a time — the same shape as
``kyc_admin`` and the billing dashboard.

Granting a commission is the only thing on this router that costs money, and
it is the reason the queue exists rather than a settings field: somebody reads
what the applicant said they were and what volume they expect, and decides a
percentage against it. Nothing here is automatic.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from api.db import db_client
from api.db.models import PartnerApplicationModel, UserModel
from api.enums import CommissionBasis, PartnerApplicationStatus
from api.services.auth.depends import get_superuser
from api.services.partners import applications as partners
from api.services.partners import earnings
from api.services.partners.applications import PartnerError

router = APIRouter(
    prefix="/admin/partners",
    tags=["admin-partners"],
    dependencies=[Depends(get_superuser)],
)


class ApproveRequest(BaseModel):
    commission_bps: int = Field(
        ...,
        ge=0,
        le=10_000,
        description="Basis points, so 1250 is 12.5%",
    )
    basis: str = Field(
        ...,
        description=(
            "platform_fee (a share of what we keep — cannot go underwater) or "
            "total_spend (a share of everything the account is charged)"
        ),
    )
    note: str | None = Field(None, description="Why this rate. Shown to the applicant.")


class RejectRequest(BaseModel):
    note: str | None = Field(
        None, description="Why not. Shown to the applicant, so write it for them."
    )


class SetCommissionRequest(ApproveRequest):
    """A renegotiation with no new application behind it."""


async def _load(session, application_id: int) -> PartnerApplicationModel:
    application = await session.get(PartnerApplicationModel, application_id)
    if application is None:
        raise HTTPException(status_code=404, detail="Application not found")
    return application


def _queue_view(application: PartnerApplicationModel) -> dict[str, Any]:
    return {
        "id": application.id,
        "organization_id": application.organization_id,
        "kind": application.kind,
        "expected_minutes_per_month": application.expected_minutes_per_month,
        "company_website": application.company_website,
        "note": application.note,
        "status": application.status,
        "submitted_at": application.submitted_at.isoformat()
        if application.submitted_at
        else None,
        "decided_at": application.decided_at.isoformat()
        if application.decided_at
        else None,
        "decision_note": application.decision_note,
    }


@router.get("/queue")
async def get_queue(
    status: str = Query(PartnerApplicationStatus.PENDING.value),
) -> dict[str, Any]:
    """Applications waiting on us, oldest first."""
    async with db_client.async_session() as session:
        rows = await partners.queue(session, status=status)
        return {
            "applications": [_queue_view(r) for r in rows],
            "bases": [b.value for b in CommissionBasis],
        }


@router.post("/{application_id}/approve")
async def approve_application(
    application_id: int,
    request: ApproveRequest,
    user: UserModel = Depends(get_superuser),
) -> dict[str, Any]:
    """Grant it, at a commission."""
    async with db_client.async_session() as session:
        application = await _load(session, application_id)
        try:
            commission = await partners.approve(
                session,
                application=application,
                decided_by=user.id,
                commission_bps=request.commission_bps,
                basis=request.basis,
                note=request.note,
            )
        except PartnerError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        # Read before the commit: it expires every attribute on both rows, and
        # reading one back is an implicit reload that raises MissingGreenlet in
        # async. The decision itself had already succeeded, so this answered 500
        # on an application that really was approved.
        payload = {
            "application": _queue_view(application),
            "commission": {
                "commission_bps": commission.commission_bps,
                "basis": commission.basis,
                "effective_from": commission.effective_from.isoformat(),
            },
        }
        await session.commit()
        return payload


@router.post("/{application_id}/reject")
async def reject_application(
    application_id: int,
    request: RejectRequest,
    user: UserModel = Depends(get_superuser),
) -> dict[str, Any]:
    """Turn it down. The account keeps everything it already had."""
    async with db_client.async_session() as session:
        application = await _load(session, application_id)
        try:
            await partners.reject(
                session,
                application=application,
                decided_by=user.id,
                note=request.note,
            )
        except PartnerError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        # Read before the commit — see `approve_application` above.
        payload = {"application": _queue_view(application)}
        await session.commit()
        return payload


@router.get("/accounts/{organization_id}/commission")
async def get_commission_history(organization_id: int) -> dict[str, Any]:
    """Every rate this account has been on, newest first.

    The audit trail behind a statement: a partner asking "why is this number
    what it is" is answered from here, not from the live row.
    """
    async with db_client.async_session() as session:
        rows = await partners.history_for(session, organization_id)
        return {
            "commissions": [
                {
                    "id": r.id,
                    "commission_bps": r.commission_bps,
                    "basis": r.basis,
                    "application_id": r.application_id,
                    "effective_from": r.effective_from.isoformat(),
                    "effective_to": r.effective_to.isoformat()
                    if r.effective_to
                    else None,
                    "note": r.note,
                }
                for r in rows
            ]
        }


@router.put("/accounts/{organization_id}/commission")
async def set_commission(
    organization_id: int,
    request: SetCommissionRequest,
    user: UserModel = Depends(get_superuser),
) -> dict[str, Any]:
    """Renegotiate, without a new application.

    Closes the open rate and starts a new one rather than editing, so an
    already-issued statement still reproduces its own number.
    """
    async with db_client.async_session() as session:
        try:
            commission = await partners.set_commission(
                session,
                organization_id=organization_id,
                commission_bps=request.commission_bps,
                basis=request.basis,
                set_by=user.id,
                note=request.note,
            )
        except PartnerError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        await session.commit()
        return {
            "commission": {
                "commission_bps": commission.commission_bps,
                "basis": commission.basis,
                "effective_from": commission.effective_from.isoformat(),
            }
        }


# ---------------------------------------------------------------------------
# Statements and payouts
#
# The half that turns a percentage into a number somebody is paid. Generation
# is a staff action rather than a schedule, deliberately: a month is only ready
# to bill once its rollups have settled, and a cron that fires at midnight on
# the 1st bills a month whose last day may still be being costed. Regenerating
# a draft is free and is the ordinary way to pick up a late arrival.
# ---------------------------------------------------------------------------


class GenerateStatementsRequest(BaseModel):
    period_start: date = Field(..., description="First IST day of the period")
    period_end: date = Field(..., description="Last IST day, inclusive")


class MarkPaidRequest(BaseModel):
    payment_reference: str | None = Field(
        None, description="UTR or bank reference. The transfer happens outside this."
    )
    note: str | None = None


def _statement_view(statement, lines=()) -> dict[str, Any]:
    return {
        "id": statement.id,
        "partner_organization_id": statement.partner_organization_id,
        "period_start": statement.period_start.isoformat(),
        "period_end": statement.period_end.isoformat(),
        "basis": statement.basis,
        "amount_paise": statement.amount_paise,
        "basis_amount_paise": statement.basis_amount_paise,
        "status": statement.status,
        "generated_at": statement.generated_at.isoformat()
        if statement.generated_at
        else None,
        "issued_at": statement.issued_at.isoformat() if statement.issued_at else None,
        "paid_at": statement.paid_at.isoformat() if statement.paid_at else None,
        "payment_reference": statement.payment_reference,
        "lines": [
            {
                "referred_organization_id": line.referred_organization_id,
                "account": line.referred_name or "Closed account",
                "basis_amount_paise": line.basis_amount_paise,
                "amount_paise": line.amount_paise,
            }
            for line in lines
        ],
    }


@router.post("/statements/generate")
async def generate_statements(
    request: GenerateStatementsRequest,
) -> dict[str, Any]:
    """Accrue the period for every partner holding a commission.

    Runs across all of them rather than one at a time: the question staff ask
    at the end of a month is "what do we owe", not "what do we owe this
    partner", and generating them individually is how one gets missed.

    A partner whose accounts spent nothing still gets a statement, at zero.
    A missing statement and a zero one look identical in a list and mean
    opposite things — one of them is a month nobody ran.
    """
    async with db_client.async_session() as session:
        partner_ids = await partners.organizations_with_commission(session)
        generated, refused = [], []
        for organization_id in partner_ids:
            try:
                statement = await earnings.generate(
                    session,
                    partner_organization_id=organization_id,
                    period_start=request.period_start,
                    period_end=request.period_end,
                )
            except PartnerError as exc:
                # One partner with an already-issued statement must not stop
                # the other forty being generated.
                refused.append({"organization_id": organization_id, "reason": str(exc)})
                continue
            generated.append(_statement_view(statement))
        await session.commit()
        return {"generated": generated, "refused": refused}


@router.get("/statements")
async def list_statements(
    status: str | None = Query(
        None, description="draft, issued or paid. Omit for everything outstanding."
    ),
) -> dict[str, Any]:
    """What is owed, oldest period first."""
    async with db_client.async_session() as session:
        if status:
            rows = await earnings.by_status(session, status)
        else:
            rows = await earnings.outstanding(session)
        return {
            "statements": [
                _statement_view(row, await earnings.lines_for(session, row.id))
                for row in rows
            ]
        }


@router.post("/statements/{statement_id}/issue")
async def issue_statement(statement_id: int) -> dict[str, Any]:
    """Mark it sent. Freezes the number against regeneration."""
    async with db_client.async_session() as session:
        statement = await earnings.load(session, statement_id)
        if statement is None:
            raise HTTPException(status_code=404, detail="No such statement")
        try:
            await earnings.issue(session, statement)
        except PartnerError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        await session.commit()
        return {"statement": _statement_view(statement)}


@router.post("/statements/{statement_id}/mark-paid")
async def mark_statement_paid(
    statement_id: int, request: MarkPaidRequest
) -> dict[str, Any]:
    """Record that the money left. There is no payout rail here, deliberately."""
    async with db_client.async_session() as session:
        statement = await earnings.load(session, statement_id)
        if statement is None:
            raise HTTPException(status_code=404, detail="No such statement")
        try:
            await earnings.mark_paid(
                session,
                statement,
                payment_reference=request.payment_reference,
                note=request.note,
            )
        except PartnerError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        await session.commit()
        return {"statement": _statement_view(statement)}
