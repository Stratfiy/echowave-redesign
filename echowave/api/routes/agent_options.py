"""The model choice as the create wizard asks it: a preset, a voice, with a price.

Thin — the vocabulary and the pricing live in
``services/configuration/agent_options.py``, because the wizard is not the only
thing that will want to ask "what does this cost a minute" in words a
non-technical buyer can act on.
"""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from api.db import db_client
from api.db.models import UserModel
from api.enums import CostComponent
from api.services.auth.depends import get_user
from api.services.configuration import (
    agent_options,
    managed_tiers,
    model_presets,
    voice_samples,
)
from api.services.configuration.registry import ServiceProviders
from api.services.telephony import carriage

router = APIRouter(prefix="/agent-options", tags=["agent-options"])


def _carriage_view(basis: carriage.CarriageBasis) -> dict[str, Any]:
    return {
        "provider": basis.provider,
        "reason": basis.reason,
        "explanation": basis.explanation,
        "included": basis.provider is not None,
    }


@router.get("/carriage")
async def get_carriage_basis(user: UserModel = Depends(get_user)) -> dict[str, Any]:
    """Whether a per-minute quote for this account should carry telephony.

    Its own endpoint because two screens need the same answer and neither can
    derive it: the default outbound configuration's provider is *not* the
    answer on its own. An account dialling on its own Twilio is billed by
    Twilio, and adding a carriage line to its quote charges twice for one phone
    call — the same double charge ``carriage.py`` exists to prevent on the
    invoice, appearing instead on the estimate.
    """
    organization_id = user.selected_organization_id
    if organization_id is None:
        raise HTTPException(status_code=400, detail="No organization selected")

    async with db_client.async_session() as session:
        return _carriage_view(
            await carriage.billable_carrier(session, organization_id=organization_id)
        )


@router.get("")
async def get_agent_options(
    languages: list[str] | None = Query(
        default=None,
        description=(
            "Languages the agent will serve, by name or subtag. Used to mark "
            "the preset that fits; the options themselves do not change."
        ),
    ),
    uses_tools: bool = Query(
        default=False,
        description="Whether the agent will call tools or read documents.",
    ),
    user: UserModel = Depends(get_user),
) -> dict[str, Any]:
    """Voices, brains and presets, each with what it costs a minute.

    Brains are priced per tier rather than per combination: every managed
    voice on a tier resolves to the same vendor rate, so the voice does not
    move the number. Presets are priced whole, and the one that fits the
    languages and tools given is marked recommended.
    """
    organization_id = user.selected_organization_id
    if organization_id is None:
        raise HTTPException(status_code=400, detail="No organization selected")

    brains = agent_options.brains()
    async with db_client.async_session() as session:
        # The carrier this account will actually be billed for, which is not
        # the same as the carrier it dials on. Without this the wizard quoted a
        # stack with no carriage at all — about 10% light for an account whose
        # numbers are ours, and the omission was never stated.
        basis = await carriage.billable_carrier(
            session, organization_id=organization_id
        )
        priced = []
        for brain in brains:
            paise = await agent_options.price_per_minute(
                session,
                organization_id=organization_id,
                brain=brain.tier,
                telephony_provider=basis.provider,
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
    #
    # Per voice tier, because a preset names its tier and the voices under
    # Basic are not the voices under Standard. ``voices`` stays as the default
    # tier's list for callers that ask no further.
    async def _voice_list(tier: str) -> list[dict[str, Any]]:
        # Samples are stored per model, so the tier has to be resolved to the
        # model that will actually speak. Looking one up by tier name would
        # miss every recording and show a list with no play buttons.
        upstream = managed_tiers.resolve(CostComponent.TTS, tier or None)
        out = []
        for voice in agent_options.voices(tier):
            out.append(
                {
                    "voice_id": voice.voice_id,
                    "name": voice.name,
                    "gender": voice.gender,
                    "description": voice.description,
                    "is_default": voice.is_default,
                    "sample_url": await voice_samples.sample_url(
                        voice.voice_id, "en", upstream.model
                    ),
                    "sample_url_hi": await voice_samples.sample_url(
                        voice.voice_id, "hi", upstream.model
                    ),
                }
            )
        return out

    voices_by_tier = {tier: await _voice_list(tier) for tier in managed_tiers.TTS_TIERS}
    voice_list = voices_by_tier.get("default", [])

    async with db_client.async_session() as session:
        # The preset chips, priced. On the same request as voices and brains
        # because the screen needs all of them to render one price, and three
        # round trips to draw one number is how a picker feels slow.
        presets = await agent_options.preset_options(
            session,
            organization_id=organization_id,
            telephony_provider=basis.provider,
            requirement=model_presets.Requirement(
                languages=tuple(languages or []), uses_tools=uses_tools
            ),
        )

    async with db_client.async_session() as session:
        # What the account can actually spend, so the picker can turn each
        # card's price into minutes.
        #
        # A price a minute is not a decision anybody can make. ₹25.79 reads as
        # "a bit more than ₹8.30" until it is stated as 97 minutes against 301
        # — which is the difference between a premium option and a month that
        # ends in four days, and it is the shape of question a first-time buyer
        # is actually asking.
        from api.services.billing.payments import current_balance_paise

        balance_paise = await current_balance_paise(
            session, organization_id=organization_id
        )

    return {
        "brains": priced,
        "voices": voice_list,
        "voices_by_tier": voices_by_tier,
        "presets": presets,
        "balance_paise": int(balance_paise),
        # What the numbers above do and do not contain. A price that excludes
        # the largest variable line has to say so on the screen, or the first
        # invoice is where the customer finds out.
        "telephony": _carriage_view(basis),
    }


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
        # Carriage included on the same terms as everywhere else. Leaving it
        # out here is worse than on a price card: a cheap minute divides into a
        # balance more times, so an omitted carrier line does not read as a
        # small price, it reads as more minutes than the balance will buy.
        basis = await carriage.billable_carrier(
            session, organization_id=organization_id
        )
        paise = await agent_options.price_per_minute(
            session,
            organization_id=organization_id,
            brain=brain,
            telephony_provider=basis.provider,
        )

    return {
        "paise_per_minute": paise,
        "minutes": agent_options.approximate_minutes(balance_paise, paise),
        "telephony": _carriage_view(basis),
    }


@router.get("/voice-sample")
async def get_voice_sample(
    voice_id: str = Query(..., max_length=128),
    provider: str = Query("decibyl", max_length=32),
    model: str = Query("", max_length=64),
    language: str = Query("en", max_length=8),
    user: UserModel = Depends(get_user),
) -> dict[str, Any]:
    """A URL for this voice, recorded now if nobody has asked before.

    What the play button calls. The voice list itself does not record -- it
    asks about forty voices at once and forty vendor calls to draw a list is
    not a page anybody waits for -- so the list shows a play button for every
    voice and the recording happens on the first press. One vendor call, once,
    for every customer who opens that picker afterwards.

    ``url`` is null when there is honestly nothing to play: a language the
    vendor does not speak, a model with no named speakers, no platform key,
    or a vendor that is down. The button reports that rather than failing.
    """
    if user.selected_organization_id is None:
        raise HTTPException(status_code=400, detail="No organization selected")

    resolved_provider, resolved_model = provider, model
    if provider == ServiceProviders.DECIBYL.value:
        # A managed tier names a tier, not a vendor. Resolve it so the sample
        # is recorded by whoever will actually speak on the call.
        upstream = managed_tiers.resolve(CostComponent.TTS, model or None)
        resolved_provider, resolved_model = upstream.provider, upstream.model

    url = await voice_samples.ensure_sample_url(
        provider=resolved_provider,
        model=resolved_model,
        voice_id=voice_id,
        language=language,
    )
    return {"voice_id": voice_id, "language": language, "url": url}


@router.get("/catalogue")
async def get_catalogue(user: UserModel = Depends(get_user)) -> dict[str, Any]:
    """The managed models on offer, per slot, each with what it costs a minute.

    Everything here is something Decibyl holds a key for, has priced, and an
    operator has put on sale. A model failing any of those is absent rather than
    disabled: an option we cannot price is not a choice with a caveat, it is one
    that would bill the customer wrongly.

    Anything outside this list is what a customer's own key is for — see
    ``GET /provider-keys/models``.
    """
    organization_id = user.selected_organization_id
    if organization_id is None:
        raise HTTPException(status_code=400, detail="No organization selected")

    async with db_client.async_session() as session:
        return {
            "catalogue": await agent_options.catalogue_options(
                session, organization_id=organization_id
            )
        }
