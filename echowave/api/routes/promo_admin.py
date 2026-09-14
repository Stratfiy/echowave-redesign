"""Staff: create, change and revoke promo codes, and see what they cost (KAN-134).

Every write is a billing audit row: a code is a price change for whoever
holds it. Superuser only, and under the ``admin`` tag so the public OpenAPI
document never lists it.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from loguru import logger
from pydantic import BaseModel, Field

from api.db import db_client
from api.db.models import BillingAuditLogModel, UserModel
from api.enums import BillingAuditAction
from api.services.auth.depends import get_superuser
from api.services.billing import promo_codes

router = APIRouter(
    prefix="/admin/billing/promo-codes",
    tags=["admin-promo-codes"],
    dependencies=[Depends(get_superuser)],
)


class PromoCreate(BaseModel):
    code: str = Field(..., min_length=3, max_length=32)
    kind: str = Field(..., description="percent | amount | bonus_credits")
    value: int = Field(..., gt=0, description="Percent, minor units, or credits")
    currency: str | None = Field(None, max_length=3)
    applies_to: str = Field("any", max_length=48)
    valid_from: datetime | None = None
    valid_until: datetime | None = None
    max_redemptions: int | None = Field(None, ge=1)
    max_per_account: int = Field(1, ge=1)
    first_payment_only: bool = False
    note: str | None = None


class PromoUpdate(BaseModel):
    kind: str | None = None
    value: int | None = Field(None, gt=0)
    currency: str | None = Field(None, max_length=3)
    applies_to: str | None = Field(None, max_length=48)
    valid_from: datetime | None = None
    valid_until: datetime | None = None
    max_redemptions: int | None = Field(None, ge=1)
    max_per_account: int | None = Field(None, ge=1)
    first_payment_only: bool | None = None
    active: bool | None = None
    note: str | None = None


def _audit(
    session, *, actor: UserModel, action: BillingAuditAction, old: dict, new: dict
) -> None:
    session.add(
        BillingAuditLogModel(
            organization_id=None,
            actor_user_id=actor.id,
            action=action.value,
            old_value=old,
            new_value=new,
            note=f"promo code {new.get('code') or old.get('code')}",
        )
    )


@router.get("")
async def list_promo_codes() -> dict[str, Any]:
    """Every code, newest first, with redemptions, discount given and bonus
    credits granted."""
    async with db_client.async_session() as session:
        rows = await promo_codes.report(session)
    return {"promo_codes": rows, "kinds": list(promo_codes.KINDS)}


@router.post("")
async def create_promo_code(
    request: PromoCreate, user: UserModel = Depends(get_superuser)
) -> dict[str, Any]:
    async with db_client.async_session() as session:
        try:
            promo = await promo_codes.create(
                session, **request.model_dump(), created_by=user.id
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        view = promo_codes.as_dict(promo)
        _audit(
            session,
            actor=user,
            action=BillingAuditAction.PROMO_CODE_CREATED,
            old={},
            new=view,
        )
        await session.commit()
    logger.info("Promo code {} created by staff user {}", view["code"], user.id)
    return view


@router.put("/{promo_id}")
async def update_promo_code(
    promo_id: int, request: PromoUpdate, user: UserModel = Depends(get_superuser)
) -> dict[str, Any]:
    fields = request.model_dump(exclude_unset=True)
    if not fields:
        raise HTTPException(status_code=400, detail="Nothing to change")
    async with db_client.async_session() as session:
        before = await session.get(promo_codes.PromoCodeModel, promo_id)
        if before is None:
            raise HTTPException(status_code=404, detail="Promo code not found")
        old = promo_codes.as_dict(before)
        try:
            promo = await promo_codes.update(session, promo_id=promo_id, **fields)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        view = promo_codes.as_dict(promo)
        _audit(
            session,
            actor=user,
            action=BillingAuditAction.PROMO_CODE_CHANGED,
            old=old,
            new=view,
        )
        await session.commit()
    return view


@router.post("/{promo_id}/revoke")
async def revoke_promo_code(
    promo_id: int, user: UserModel = Depends(get_superuser)
) -> dict[str, Any]:
    """Stop new redemptions. Nothing already granted is clawed back."""
    async with db_client.async_session() as session:
        before = await session.get(promo_codes.PromoCodeModel, promo_id)
        if before is None:
            raise HTTPException(status_code=404, detail="Promo code not found")
        old = promo_codes.as_dict(before)
        promo = await promo_codes.revoke(session, promo_id=promo_id)
        view = promo_codes.as_dict(promo)
        _audit(
            session,
            actor=user,
            action=BillingAuditAction.PROMO_CODE_REVOKED,
            old=old,
            new=view,
        )
        await session.commit()
    logger.info("Promo code {} revoked by staff user {}", view["code"], user.id)
    return view
