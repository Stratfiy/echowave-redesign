"""The native app's push tokens over HTTP (MOBILE.md, flag ``mobile_push``).

Thin: resolve the signed-in person and their workspace, hand over to
``services/identity/mobile_push.py``. A 404 while the flag is off for the
workspace. No route takes another person's id or token: what is read and
revoked is always the caller's own.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from api.db.models import UserModel
from api.services import features
from api.services.auth.depends import get_user
from api.services.identity import mobile_push

router = APIRouter(prefix="/me/mobile-push", tags=["identity"])

_on = Depends(features.require(mobile_push.FLAG, per_organization=True))


class MobileDevice(BaseModel):
    id: int
    platform: str
    label: str
    app_version: str | None = None
    created_at: datetime
    last_seen_at: datetime
    last_success_at: datetime | None = None
    last_failure_at: datetime | None = None
    state: Literal["active", "failing", "revoked"]


class MobileDevices(BaseModel):
    devices: list[MobileDevice]


class MobileTokenRegistration(BaseModel):
    model_config = ConfigDict(extra="forbid")
    token: str = Field(max_length=255)
    platform: Literal["ios", "android"]
    device_label: str | None = Field(default=None, max_length=80)
    app_version: str | None = Field(default=None, max_length=32)


class MobileTokenRemoval(BaseModel):
    model_config = ConfigDict(extra="forbid")
    token: str = Field(max_length=255)


class MobileTokenRemoved(BaseModel):
    removed: bool


@router.get("/devices", response_model=MobileDevices, dependencies=[_on])
async def list_mobile_devices(
    user: Annotated[UserModel, Depends(get_user)],
) -> MobileDevices:
    """The person's own phones running the app."""
    return MobileDevices(devices=await mobile_push.devices(user.id))


@router.post("/tokens", response_model=MobileDevice, dependencies=[_on])
async def register_mobile_token(
    body: MobileTokenRegistration, user: Annotated[UserModel, Depends(get_user)]
) -> MobileDevice:
    """This phone, after the person allowed notifications. Sent again on
    every app open; the same token is updated, never duplicated."""
    if not user.selected_organization_id:
        raise HTTPException(status_code=400, detail="No organization selected")
    try:
        view = await mobile_push.register(
            user.id,
            user.selected_organization_id,
            token=body.token,
            platform=body.platform,
            device_label=body.device_label,
            app_version=body.app_version,
        )
    except mobile_push.Invalid as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return MobileDevice(**view)


@router.post("/tokens/remove", response_model=MobileTokenRemoved, dependencies=[_on])
async def unregister_mobile_token(
    body: MobileTokenRemoval, user: Annotated[UserModel, Depends(get_user)]
) -> MobileTokenRemoved:
    """Stop pushing to this phone: sign-out, or push turned off on it.
    Saying so twice is the same as once."""
    return MobileTokenRemoved(removed=await mobile_push.unregister(user.id, body.token))


@router.delete("/devices/{device_id}", response_model=MobileDevices, dependencies=[_on])
async def remove_mobile_device(
    device_id: int, user: Annotated[UserModel, Depends(get_user)]
) -> MobileDevices:
    """Revoke one of the person's phones from their device list."""
    if not await mobile_push.remove(user.id, device_id):
        raise HTTPException(status_code=404, detail="Not found")
    return MobileDevices(devices=await mobile_push.devices(user.id))
