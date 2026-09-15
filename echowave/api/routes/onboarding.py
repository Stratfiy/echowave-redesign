"""The onboarding checklist: six steps, each paying part of the Free credits."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from api.db import db_client
from api.db.models import UserModel
from api.services.auth.depends import get_user
from api.services.billing import onboarding_credits
from api.services.workflow import home_openers

router = APIRouter(prefix="/onboarding", tags=["onboarding"])


@router.get("/credits")
async def get_onboarding_credits(user: UserModel = Depends(get_user)) -> dict[str, Any]:
    """Where this account stands on the six steps, paying any that are done.

    Settling on read is deliberate: whichever path completed a step, opening
    Home catches up, so a step can never be done and quietly unpaid.
    """
    organization_id = user.selected_organization_id
    if organization_id is None:
        raise HTTPException(status_code=400, detail="Select an organization first")
    async with db_client.async_session() as session:
        granted = await onboarding_credits.settle(
            session, organization_id=organization_id
        )
        if granted:
            await session.commit()
        result = await onboarding_credits.state(
            session, organization_id=organization_id
        )
    result["just_granted"] = granted
    return result


class DoorAnswers(BaseModel):
    """The three questions at the door. Kept on the account so Home's first
    cards and Decibyl know who they are talking to; the lead service gets
    its own copy from the browser as before."""

    role: str = Field(default="", max_length=64)
    business: str = Field(default="", max_length=64)
    heard: str = Field(default="", max_length=64)


@router.post("/door")
async def record_door(
    body: DoorAnswers, user: UserModel = Depends(get_user)
) -> dict[str, Any]:
    organization_id = user.selected_organization_id
    if organization_id is None:
        raise HTTPException(status_code=400, detail="Select an organization first")
    await home_openers.remember_door(
        organization_id, role=body.role.strip(), business=body.business.strip()
    )
    return {"ok": True}
