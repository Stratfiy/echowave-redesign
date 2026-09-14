"""Invite a business: this account's referral link and who came through it.

Friend referral (KAN-133). Every account has a code; reading this mints it.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from api.db import db_client
from api.db.models import UserModel
from api.services.auth.depends import get_user
from api.services.billing import referral_rewards

router = APIRouter(prefix="/referrals", tags=["referrals"])


@router.get("")
async def get_referrals(user: UserModel = Depends(get_user)) -> dict[str, Any]:
    """The code, the link, the terms, and the accounts that signed up
    through it with whether each has paid."""
    if not user.selected_organization_id:
        raise HTTPException(status_code=400, detail="No organization selected")
    async with db_client.async_session() as session:
        state = await referral_rewards.state(
            session, organization_id=user.selected_organization_id
        )
        await session.commit()
    return state
