"""The model choice as the create wizard asks it: a voice and a brain, with a price.

Thin — the vocabulary and the pricing live in
``services/configuration/agent_options.py``, because the wizard is not the only
thing that will want to ask "what does this cost a minute" in words a
non-technical buyer can act on.
"""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from api.db import db_client
from api.db.models import UserModel
from api.services.auth.depends import get_user
from api.services.configuration import agent_options, voice_samples

router = APIRouter(prefix="/agent-options", tags=["agent-options"])


@router.get("")
async def get_agent_options(user: UserModel = Depends(get_user)) -> dict[str, Any]:
    """Voices and brains, each with what it costs a minute.

    Priced per brain rather than per combination: every managed voice resolves
    to the same tier and the same vendor rate, so the voice does not move the
    number and pricing the cross-product would be seven identical answers per
    tier.
    """
    organization_id = user.selected_organization_id
    if organization_id is None:
        raise HTTPException(status_code=400, detail="No organization selected")

    brains = agent_options.brains()
    async with db_client.async_session() as session:
        priced = []
        for brain in brains:
            paise = await agent_options.price_per_minute(
                session, organization_id=organization_id, brain=brain.tier
            )
            priced.append(
                {
                    "tier": brain.tier,
                    "label": brain.label,
                    "blurb": brain.blurb,
                    "paise_per_minute": paise,
                }
            )

    # A sample URL per voice, or null where nothing has been recorded yet. The
    # picker renders a play button only where there is something to play, so a
    # deployment that has never run the generation script degrades to names
    # rather than to buttons that fail when clicked.
    voice_list = []
    for voice in agent_options.voices():
        voice_list.append(
            {
                "voice_id": voice.voice_id,
                "name": voice.name,
                "gender": voice.gender,
                "description": voice.description,
                "is_default": voice.is_default,
                "sample_url": await voice_samples.sample_url(voice.voice_id, "en"),
                "sample_url_hi": await voice_samples.sample_url(voice.voice_id, "hi"),
            }
        )

    return {"brains": priced, "voices": voice_list}


@router.get("/minutes")
async def get_approximate_minutes(
    balance_paise: int = Query(..., ge=0, description="Balance to convert, in paise"),
    brain: str = Query("default", description="Language-model tier"),
    user: UserModel = Depends(get_user),
) -> dict[str, Any]:
    """Roughly how many minutes a balance buys on this brain.

    An estimate to show, never an entitlement to bill against: it moves with
    the rate card and with how much the agent actually says. ``minutes`` is
    null when the stack cannot be priced, because a zero would read as free.
    """
    organization_id = user.selected_organization_id
    if organization_id is None:
        raise HTTPException(status_code=400, detail="No organization selected")

    async with db_client.async_session() as session:
        paise = await agent_options.price_per_minute(
            session, organization_id=organization_id, brain=brain
        )

    return {
        "paise_per_minute": paise,
        "minutes": agent_options.approximate_minutes(balance_paise, paise),
    }
