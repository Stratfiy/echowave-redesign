"""The onboarding checklist: six steps, each paying part of the Free credits."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from api.db import db_client
from api.db.models import UserModel
from api.services.auth.depends import get_user
from api.services.billing import onboarding_credits

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
