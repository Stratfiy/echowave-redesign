"""Spend caps a customer sets on the workspace or on one agent (S-1).

Admin and above: a cap decides what the workspace may spend, which is the
same standing that buys the credits it caps.
"""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from api.db import db_client
from api.db.models import UserModel
from api.enums import OrganizationRole
from api.services.auth.depends import require_organization_role
from api.services.billing import budgets
from api.services.billing.credits import PAISE_PER_CREDIT

router = APIRouter(prefix="/organizations/budgets", tags=["budgets"])

_admin = require_organization_role(OrganizationRole.ADMIN)


class BudgetPolicyRequest(BaseModel):
    workflow_id: int | None = Field(
        default=None, description="One agent, or null for the whole workspace"
    )
    window_kind: Literal["calendar_month", "lifetime"] = "calendar_month"
    amount_credits: int = Field(ge=0)
    warn_percent: int = Field(default=80, ge=0, le=100)
    hard_stop: bool = True


class BudgetPolicyResponse(BaseModel):
    id: int
    workflow_id: int | None
    window_kind: str
    amount_credits: int
    warn_percent: int
    hard_stop: bool
    spent_credits: int
    window_start: str
    window_end: str | None
    exhausted: bool
    warned: bool


class BudgetIncidentResponse(BaseModel):
    id: int
    policy_id: int
    workflow_id: int | None
    threshold: str
    window_start: str
    window_end: str | None
    limit_credits: int
    observed_credits: int
    status: str
    created_at: str | None


def _standing(standing: budgets.Standing) -> BudgetPolicyResponse:
    p = standing.policy
    return BudgetPolicyResponse(
        id=p.id,
        workflow_id=p.workflow_id,
        window_kind=p.window_kind,
        amount_credits=int(p.amount_paise) // PAISE_PER_CREDIT,
        warn_percent=int(p.warn_percent),
        hard_stop=bool(p.hard_stop),
        spent_credits=standing.observed_paise // PAISE_PER_CREDIT,
        window_start=standing.window_start.isoformat(),
        window_end=standing.window_end.isoformat() if standing.window_end else None,
        exhausted=standing.exhausted,
        warned=standing.warned,
    )


async def _standings(session, organization_id: int) -> list[BudgetPolicyResponse]:
    out: list[BudgetPolicyResponse] = []
    for policy in await budgets.list_policies(session, organization_id=organization_id):
        start, end = budgets.window_bounds(
            policy.window_kind,
            at=__import__("datetime").datetime.now(__import__("datetime").UTC),
            opened_at=policy.created_at,
        )
        observed = await budgets.spend_paise(
            session,
            organization_id=organization_id,
            workflow_id=policy.workflow_id,
            start=start,
            end=end,
        )
        out.append(_standing(budgets.Standing(policy, start, end, observed)))
    return out


@router.get("", response_model=list[BudgetPolicyResponse])
async def list_budget_policies(user: Annotated[UserModel, Depends(_admin)]):
    async with db_client.async_session() as session:
        return await _standings(session, user.selected_organization_id)


@router.put("", response_model=list[BudgetPolicyResponse])
async def set_budget_policy(
    body: BudgetPolicyRequest, user: Annotated[UserModel, Depends(_admin)]
):
    """Create the cap for a scope and window, or edit the live one."""
    async with db_client.async_session() as session:
        try:
            await budgets.set_policy(
                session,
                organization_id=user.selected_organization_id,
                workflow_id=body.workflow_id,
                window_kind=body.window_kind,
                amount_credits=body.amount_credits,
                warn_percent=body.warn_percent,
                hard_stop=body.hard_stop,
                created_by=user.id,
            )
        except budgets.BudgetError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        await session.commit()
        return await _standings(session, user.selected_organization_id)


@router.delete("/{policy_id}", status_code=204)
async def remove_budget_policy(
    policy_id: int, user: Annotated[UserModel, Depends(_admin)]
):
    async with db_client.async_session() as session:
        removed = await budgets.remove_policy(
            session, organization_id=user.selected_organization_id, policy_id=policy_id
        )
        if not removed:
            raise HTTPException(status_code=404, detail="No such spend cap")
        await session.commit()


@router.get("/incidents", response_model=list[BudgetIncidentResponse])
async def list_budget_incidents(
    user: Annotated[UserModel, Depends(_admin)], open_only: bool = True
):
    async with db_client.async_session() as session:
        rows = await budgets.list_incidents(
            session, organization_id=user.selected_organization_id, open_only=open_only
        )
    return [
        BudgetIncidentResponse(
            id=r.id,
            policy_id=r.policy_id,
            workflow_id=r.workflow_id,
            threshold=r.threshold,
            window_start=r.window_start.isoformat(),
            window_end=r.window_end.isoformat() if r.window_end else None,
            limit_credits=int(r.amount_limit_paise) // PAISE_PER_CREDIT,
            observed_credits=int(r.amount_observed_paise) // PAISE_PER_CREDIT,
            status=r.status,
            created_at=r.created_at.isoformat() if r.created_at else None,
        )
        for r in rows
    ]


@router.post("/incidents/{incident_id}/dismiss", status_code=204)
async def dismiss_budget_incident(
    incident_id: int, user: Annotated[UserModel, Depends(_admin)]
):
    async with db_client.async_session() as session:
        done = await budgets.dismiss_incident(
            session,
            organization_id=user.selected_organization_id,
            incident_id=incident_id,
        )
        if not done:
            raise HTTPException(status_code=404, detail="No such incident")
        await session.commit()
