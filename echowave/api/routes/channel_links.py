"""Settings → Decibyl in your apps (DCH-1, KAN-277): link WhatsApp,
Telegram, Slack or Teams to the signed-in member with a one-time code."""

from __future__ import annotations

import os
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from api.db.models import UserModel
from api.services import features
from api.services.auth.depends import get_user
from api.services.messaging.channels import base, dispatch, identities, telegram

router = APIRouter(prefix="/channel-links", tags=["channel-links"])


class StartLink(BaseModel):
    channel: str


def _organization_id(user: UserModel) -> int:
    if not user.selected_organization_id:
        raise HTTPException(status_code=400, detail="No organization selected")
    return int(user.selected_organization_id)


def _how_to(channel: str, code: str) -> dict[str, Any]:
    """Where to send the code, as a link where the platform has one."""
    if channel == base.TELEGRAM:
        name = telegram.bot_username()
        return {
            "link": f"https://t.me/{name}?start={code}" if name else None,
            "instructions": f"Open the Decibyl bot in Telegram and send {code}.",
        }
    if channel == base.WHATSAPP:


        number = os.getenv("WHATSAPP_DISPLAY_NUMBER", "").strip().lstrip("+")
        return {
            "link": f"https://wa.me/{number}?text={quote('LINK ' + code)}"
            if number
            else None,
            "instructions": f"Send LINK {code} to Decibyl's WhatsApp number.",
        }
    return {
        "link": None,
        "instructions": f"Send {code} to the Decibyl app in {channel.capitalize()}.",
    }


@router.get("")
async def list_links(user: UserModel = Depends(get_user)) -> dict[str, Any]:
    organization_id = _organization_id(user)
    linked = await identities.for_member(
        organization_id=organization_id, user_id=user.id
    )
    return {
        "enabled": features.is_on("decibyl_channels", organization_id),
        "channels": [
            {
                "channel": channel,
                "available": dispatch.channel_on(channel, organization_id)
                and dispatch.adapter_for(channel).enabled(organization_id),
            }
            for channel in base.CHANNELS
        ],
        "linked": [
            {
                "id": i.id,
                "channel": i.channel,
                "display_name": i.display_name,
                # The number or chat id, trimmed: enough to recognise it.
                "handle": i.external_id[-4:],
            }
            for i in linked
        ],
    }


@router.post("/start")
async def start_link(
    body: StartLink, user: UserModel = Depends(get_user)
) -> dict[str, Any]:
    organization_id = _organization_id(user)
    if body.channel not in base.CHANNELS:
        raise HTTPException(status_code=400, detail="Unknown app.")
    if not dispatch.channel_on(body.channel, organization_id):
        raise HTTPException(status_code=404, detail="Not Found")
    code = await identities.start_link(
        organization_id=organization_id, user_id=user.id, channel=body.channel
    )
    return {
        "code": code,
        "expires_in_seconds": int(identities.CODE_TTL.total_seconds()),
        **_how_to(body.channel, code),
    }


@router.delete("/{identity_id}")
async def unlink(identity_id: int, user: UserModel = Depends(get_user)) -> dict:
    organization_id = _organization_id(user)
    if not await identities.unlink(
        organization_id=organization_id, user_id=user.id, identity_id=identity_id
    ):
        raise HTTPException(status_code=404, detail="Not linked.")
    return {"unlinked": identity_id}
