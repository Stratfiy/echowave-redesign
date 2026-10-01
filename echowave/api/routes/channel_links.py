"""Settings → Decibyl in your apps (DCH-1, KAN-277): link WhatsApp,
Telegram, Slack or Teams to the signed-in member with a one-time code."""

from __future__ import annotations

import os
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from api.db import db_client
from api.db.models import UserModel
from api.enums import ORGANIZATION_ROLE_RANK, OrganizationRole
from api.services import features
from api.services.auth.depends import get_user, require_organization_role
from api.services.messaging.channels import base, dispatch, identities, slack, telegram

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


async def _slack_setup(user: UserModel, organization_id: int) -> dict[str, Any]:
    """Whether this organisation has added Decibyl to a Slack workspace yet.

    A member cannot link Slack until it has: the code has nowhere to go. So
    the screen leads with "Add Decibyl to your Slack" for an admin, and says
    an admin must do it for everybody else. Admins also see the exact
    redirect URL the Slack app must have registered.
    """
    installation = await slack.installation_for(organization_id)
    membership = await db_client.get_membership(user.id, organization_id)
    rank = ORGANIZATION_ROLE_RANK.get(membership.role if membership else "", -1)
    is_admin = rank >= ORGANIZATION_ROLE_RANK[OrganizationRole.ADMIN.value]
    return {
        "installed": installation is not None,
        "workspace": (installation.team_name or None) if installation else None,
        "can_install": is_admin,
        "redirect_uri": slack.redirect_uri() if is_admin else None,
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
        "slack": await _slack_setup(user, organization_id),
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


@router.get("/slack/install")
async def slack_install(
    user: UserModel = Depends(require_organization_role(OrganizationRole.ADMIN)),
) -> dict[str, Any]:
    """The "Add to Slack" link for this workspace. Admins only: it puts an app
    into the company's Slack on the organisation's behalf."""
    organization_id = _organization_id(user)
    if not dispatch.channel_on(base.SLACK, organization_id):
        raise HTTPException(status_code=404, detail="Not Found")
    url = slack.install_url(
        organization_id=organization_id,
        user_id=user.id,
        redirect_uri=slack.redirect_uri(),
    )
    if url is None:
        raise HTTPException(status_code=503, detail="Slack is not set up yet.")
    return {"url": url}
