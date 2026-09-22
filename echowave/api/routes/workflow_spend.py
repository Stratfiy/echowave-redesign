"""What an agent's run costs, what it cannot exceed, and the cap on it (OP-5)."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from api.db import db_client
from api.db.models import UserModel
from api.enums import OrganizationRole
from api.services.auth.depends import get_user, require_organization_role
from api.services.billing import budgets, run_estimate

router = APIRouter(prefix="/workflow", tags=["workflow-spend"])

_admin = require_organization_role(OrganizationRole.ADMIN)


class SpendCapRequest(BaseModel):
    credits_per_month: int | None = Field(
        default=None,
        ge=0,
        description="The agent's monthly cap in credits, hard stop; null removes it.",
    )


@router.get("/{workflow_id}/spend")
async def workflow_spend(
    workflow_id: int,
    user: Annotated[UserModel, Depends(get_user)],
    items_per_run: int = Query(
        run_estimate.DEFAULT_ITEMS_PER_RUN,
        ge=1,
        le=500,
        description="How many items one run handles, for the estimate.",
    ),
) -> dict[str, Any]:
    """The estimate per run and per month with its assumptions, the hard
    maximum a run cannot exceed, and the agent's cap if one is set."""
    organization_id = user.selected_organization_id
    if not organization_id:
        raise HTTPException(status_code=400, detail="No organization selected")
    async with db_client.async_session() as session:
        out = await run_estimate.for_workflow(
            session,
            organization_id=organization_id,
            workflow_id=workflow_id,
            items_per_run=items_per_run,
        )
    if out is None:
        raise HTTPException(status_code=404, detail="Workflow not found")
    return out


@router.put("/{workflow_id}/spend/cap")
async def set_workflow_spend_cap(
    workflow_id: int,
    request: SpendCapRequest,
    user: Annotated[UserModel, Depends(_admin)],
) -> dict[str, Any]:
    """Set or remove the agent's monthly cap. A hard stop: the agent's runs
    refuse once it is used up, until the next month or a raised cap."""
    organization_id = user.selected_organization_id
    if not organization_id:
        raise HTTPException(status_code=400, detail="No organization selected")
    if not budgets.enabled():
        raise HTTPException(status_code=409, detail="Spend caps are not enabled here.")
    async with db_client.async_session() as session:
        try:
            if request.credits_per_month is None:
                for policy in await budgets.policies_for(
                    session, organization_id=organization_id, workflow_id=workflow_id
                ):
                    if policy.workflow_id == workflow_id and policy.window_kind == (
                        budgets.WINDOW_CALENDAR_MONTH
                    ):
                        await budgets.remove_policy(
                            session,
                            organization_id=organization_id,
                            policy_id=policy.id,
                        )
            else:
                await budgets.set_policy(
                    session,
                    organization_id=organization_id,
                    workflow_id=workflow_id,
                    window_kind=budgets.WINDOW_CALENDAR_MONTH,
                    amount_credits=request.credits_per_month,
                    hard_stop=True,
                    created_by=user.id,
                )
        except budgets.BudgetError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        await session.commit()
        out = await run_estimate.for_workflow(
            session, organization_id=organization_id, workflow_id=workflow_id
        )
    if out is None:
        raise HTTPException(status_code=404, detail="Workflow not found")
    return out
