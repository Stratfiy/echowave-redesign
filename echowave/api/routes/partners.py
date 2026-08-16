"""Applying to be a partner, from the customer's side.

Deliberately open to any member rather than admin-gated. Asking is not
spending, granting anything, or binding the account to anything — the answer
is a staff decision either way — and the person at an agency who notices we
have a partner programme is rarely the one holding the billing profile.

The staff half lives in `partner_admin.py`, behind `get_superuser`.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from api.constants import UI_APP_URL
from api.db import db_client
from api.db.models import UserModel
from api.enums import PartnerKind, PartnerStatementStatus
from api.services.auth.depends import get_user
from api.services.partners import applications as partners
from api.services.partners import earnings
from api.services.partners.applications import PartnerError

router = APIRouter(prefix="/partners", tags=["partners"])


class PartnerApplicationRequest(BaseModel):
    kind: str = Field(..., description="developer, agency or reseller")
    expected_minutes_per_month: int | None = Field(
        None,
        ge=0,
        description="Their own forecast. The number a commission is quoted against.",
    )
    company_website: str | None = None
    note: str | None = Field(
        None, description="Anything the fixed answers cannot carry."
    )


def _organization_id(user: UserModel) -> int:
    organization_id = user.selected_organization_id
    if not organization_id:
        raise HTTPException(status_code=400, detail="No organization selected")
    return organization_id


def _view(application) -> dict[str, Any]:
    return {
        "id": application.id,
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
        # Written for the applicant, so it is the one staff-authored field
        # that comes back to them.
        "decision_note": application.decision_note,
    }


@router.get("/application")
async def get_application(user: UserModel = Depends(get_user)) -> dict[str, Any]:
    """This account's most recent application, and the arrangement it is on.

    The commission is reported as a percentage and a basis and nothing else —
    a partner may see what they are paid, which is their own contract, and not
    the rate card it is computed against.
    """
    organization_id = _organization_id(user)
    async with db_client.async_session() as session:
        application = await partners.latest_for(session, organization_id)
        commission = await partners.live_commission(session, organization_id)
        organization = await db_client.get_organization_by_id(organization_id)
        return {
            "kinds": [k.value for k in PartnerKind],
            "application": _view(application) if application else None,
            # Present only once approved, because that is when it is minted.
            # The screen uses its presence to decide whether there is a link
            # worth showing at all.
            "referral_code": getattr(organization, "referral_code", None),
            "commission": (
                {
                    "commission_bps": commission.commission_bps,
                    "basis": commission.basis,
                    "effective_from": commission.effective_from.isoformat(),
                }
                if commission
                else None
            ),
        }


@router.get("/referrals")
async def get_referrals(user: UserModel = Depends(get_user)) -> dict[str, Any]:
    """This partner's code, the link to hand out, and who came through it.

    The accounts are named and counted and nothing more. A partner is owed the
    ability to check their own attribution — "did the client I introduced
    actually land" is the question this answers — but what those accounts spend
    is on the statement as one figure per account, and nothing here exposes
    their usage, their balance or who works there.
    """
    organization_id = _organization_id(user)
    async with db_client.async_session() as session:
        organization = await db_client.get_organization_by_id(organization_id)
        code = getattr(organization, "referral_code", None)
        accounts = await earnings.referred_accounts(session, organization_id)
        return {
            "referral_code": code,
            # Built here rather than in the browser so the link is the same
            # one in an email, a slide and the dashboard. UI_APP_URL is the
            # deployment's own app host.
            "referral_link": f"{UI_APP_URL}/auth/signup?ref={code}" if code else None,
            "accounts": [
                {
                    "name": account.billing_name or f"Account {account.id}",
                    "referred_at": account.referred_at.isoformat()
                    if account.referred_at
                    else None,
                }
                for account in accounts
            ],
        }


@router.get("/statements")
async def get_statements(user: UserModel = Depends(get_user)) -> dict[str, Any]:
    """What this partner has earned, per period, with the breakdown.

    Only statements that have been issued or paid. A draft is our working
    number — regenerated whenever a late rollup or a recost changes it — and
    showing a partner a figure that moves under them is how a number becomes
    an argument.
    """
    organization_id = _organization_id(user)
    async with db_client.async_session() as session:
        statements = [
            statement
            for statement in await earnings.statements_for(session, organization_id)
            if statement.status != PartnerStatementStatus.DRAFT.value
        ]
        return {
            "statements": [
                {
                    "id": statement.id,
                    "period_start": statement.period_start.isoformat(),
                    "period_end": statement.period_end.isoformat(),
                    "basis": statement.basis,
                    "amount_paise": statement.amount_paise,
                    "basis_amount_paise": statement.basis_amount_paise,
                    "status": statement.status,
                    "paid_at": statement.paid_at.isoformat()
                    if statement.paid_at
                    else None,
                    "payment_reference": statement.payment_reference,
                    "lines": [
                        {
                            "account": line.referred_name or "Closed account",
                            "basis_amount_paise": line.basis_amount_paise,
                            "amount_paise": line.amount_paise,
                        }
                        for line in await earnings.lines_for(session, statement.id)
                    ],
                }
                for statement in statements
            ]
        }


@router.post("/application")
async def submit_application(
    request: PartnerApplicationRequest, user: UserModel = Depends(get_user)
) -> dict[str, Any]:
    """Ask to be treated as a partner."""
    organization_id = _organization_id(user)
    async with db_client.async_session() as session:
        try:
            application = await partners.submit(
                session,
                organization_id=organization_id,
                user_id=user.id,
                kind=request.kind,
                expected_minutes_per_month=request.expected_minutes_per_month,
                note=request.note,
                company_website=request.company_website,
            )
        except PartnerError as exc:
            # The service writes these for the person who will read them.
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        await session.commit()
        return {"application": _view(application)}
