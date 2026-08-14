"""Refer a friend: your code, who used it, and what it earned.

Customer-facing and organization-scoped from the session, never from the
request body — the code identifies who gets paid, so it is not something a
client gets to name.

The award itself is made at settlement, in ``services/billing/payments.py``.
Nothing here credits anything; this reports.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from api.db import db_client
from api.db.models import UserModel
from api.services.auth.depends import get_user_with_selected_organization
from api.services.billing import referrals

router = APIRouter(prefix="/referrals", tags=["referrals"])


class ApplyCodeRequest(BaseModel):
    code: str = Field(..., max_length=32)


@router.get("")
async def get_referrals(
    user: UserModel = Depends(get_user_with_selected_organization),
) -> dict[str, Any]:
    """This account's code and what it has earned, minting a code on first ask."""
    organization_id = user.selected_organization_id
    async with db_client.async_session() as session:
        await referrals.ensure_code(session, organization_id=organization_id)
        await session.commit()
        return await referrals.summary(session, organization_id=organization_id)


@router.post("/apply")
async def apply_referral_code(
    request: ApplyCodeRequest,
    user: UserModel = Depends(get_user_with_selected_organization),
) -> dict[str, Any]:
    """Record who referred this account.

    Awards nothing — the reward is earned when this account first pays, and
    held for a week after that. Kept separate because the two fail for
    different reasons and at different times: a wrong code is the customer's
    mistake to fix now, a missing award is ours to fix later.
    """
    organization_id = user.selected_organization_id
    async with db_client.async_session() as session:
        try:
            referrer = await referrals.attribute(
                session, organization_id=organization_id, code=request.code
            )
        except referrals.ReferralError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        await session.commit()

    return {
        "applied": True,
        # Never the referrer's identity. Knowing a code is valid is one thing;
        # learning whose account it is would turn the endpoint into a way to
        # enumerate customers.
        "referrer_organization_id": referrer.id,
        "share_bps": referrals.REFERRAL_SHARE_BPS,
        "hold_days": referrals.HOLD_DAYS,
    }
