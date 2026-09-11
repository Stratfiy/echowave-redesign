"""The model choice, in words the person buying it uses.

The Models screen names vendors and models because someone bringing their own
keys has to see them. The person this product is aimed at — a clinic owner, a
dealership, a coaching centre — does not know Sarvam from OpenAI and should not
have to learn in order to answer a phone.

So the same choice is offered twice, in two vocabularies:

* **Models screen** — provider and model, for whoever is pointing us at an
  account they pay for.
* **Here** — a voice you can listen to and a brain that is Lite, Normal or
  Smart, with one price per minute underneath.

Both write the same thing: a managed slot naming a tier, which
``managed_resolution`` turns into a vendor at call time. Nothing about the
underlying arrangement changes; only what is said about it.

**The price is the point.** A tier the customer cannot price is a tier they
will not pick, so every option carries what it costs a minute on the current
rate card, and the caller can turn a balance into an approximate number of
minutes with it.
"""

from __future__ import annotations

from dataclasses import dataclass

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from api.schemas.ai_model_configuration import (
    DECIBYL_DEFAULT_VOICE,
    DECIBYL_GENDER_VOICES,
)
from api.services.billing.addons import DEFAULT_AGENT_ADDONS
from api.services.billing.estimator import estimate_cost_per_minute
from api.services.configuration import managed_tiers, voice_catalogue
from api.services.configuration.ai_model_configuration import (
    WORKFLOW_MODEL_CONFIGURATION_V2_OVERRIDE_KEY,
)
from api.services.configuration.registry import ServiceProviders


#: Voices we are happy to put in front of a first-time buyer, in the order they
#: should be shown. A short curated list beats the vendor's full catalogue:
#: seven names with no way to tell them apart is not a choice, it is a quiz.
#:
#: The character words are deliberately absent. Nobody here has listened to
#: these voices, and inventing "warm" or "authoritative" for a voice we have
#: not heard would be a claim a caller can immediately check. They belong in
#: `description` once somebody has sat with the samples — see
#: `docs/images/SHOTLIST.md` for the same discipline applied to screenshots.
@dataclass(frozen=True)
class VoiceOption:
    voice_id: str
    name: str
    gender: str | None
    description: str | None = None
    #: The one a caller hears if nobody chooses. Derived from position rather
    #: than hardcoded: the catalogue lists each model's speakers in the
    #: vendor's own order and the vendor's default is first — anushka on
    #: bulbul:v2, shubh on v3 — so this stays right when the tier moves.
    is_default: bool = False


@dataclass(frozen=True)
class BrainOption:
    tier: str
    label: str
    blurb: str


def brains() -> list[BrainOption]:
    """The language-model tiers, in the order they should be shown."""
    return [
        BrainOption(tier=tier, label=label, blurb=blurb)
        for tier, (label, blurb) in (
            (t, managed_tiers.LLM_TIER_LABELS[t]) for t in managed_tiers.LLM_TIERS
        )
    ]


def voices() -> list[VoiceOption]:
    """Managed voices, as the catalogue serves them.

    Resolved through the managed tier, so this is what a managed customer will
    really get rather than a list we maintain separately and forget to update.
    """
    catalogue = voice_catalogue.for_provider("decibyl", model=None)
    return [
        VoiceOption(
            voice_id=voice.voice_id,
            name=voice.name,
            gender=voice.gender,
            description=voice.description,
            is_default=index == 0,
        )
        for index, voice in enumerate(catalogue.voices)
    ]


def _priced(estimate, *, what: str) -> int | None:
    """The total, or ``None`` when some component of it has no rate on file.

    The estimator already reports an unpriced component; every caller here used
    to drop that and return the total anyway. A total missing its largest line
    is not a smaller price, it is a wrong one — a speech-to-speech card quoted
    Rs2.76 a minute against a real Rs25.79 that way, because the model resolved
    to no rate and what was left was the telephony and the platform fee.

    ``None`` rather than zero, and rather than a total: a screen can say "we
    cannot price this" and an operator can go and add the rate. Nothing
    downstream can mistake it for cheap.
    """
    if estimate.unpriced:
        logger.warning(
            "Refusing to quote {}: no rate on file for {}. The card will show "
            "no price until one is added at /superadmin/billing/rate-card.",
            what,
            ", ".join(estimate.unpriced),
        )
        return None
    return estimate.total_paise_per_minute


async def pipeline_estimate(
    session: AsyncSession,
    *,
    organization_id: int | None,
    brain: str,
    stt_tier: str = "default",
    tts_tier: str = "default",
    telephony_provider: str | None = None,
    marked_up: bool = True,
):
    """The itemised estimate for a managed pipeline, or ``None`` if unpriced.

    Split out from :func:`price_per_minute` because two screens want different
    depths of the same answer and pricing twice is how the two drift: the card
    needs one number, the breakdown bar beside it needs the lines that add up
    to that number. One estimate serves both.

    ``stt_tier`` and ``tts_tier`` are parameters rather than the constant
    ``"default"`` they used to be. A bundle names its own speech tiers, and
    hardcoding them here priced every bundle as though it ran the default pair
    — correct only for as long as every pipeline bundle happened to.
    """
    llm = managed_tiers.resolve("llm", brain)
    stt = managed_tiers.resolve("stt", stt_tier or "default")
    tts = managed_tiers.resolve("tts", tts_tier or "default")

    estimate = await estimate_cost_per_minute(
        session,
        organization_id=organization_id,
        # What a new agent runs on every call whether or not anybody
        # asked for it. Left out, every quote on this screen was short
        # by the QA fee — in the one direction a price must not be.
        addons=DEFAULT_AGENT_ADDONS,
        stt_provider=stt.provider,
        stt_model=stt.model,
        llm_provider=llm.provider,
        llm_model=llm.model,
        tts_provider=tts.provider,
        tts_model=tts.model,
        telephony_provider=telephony_provider,
        marked_up=marked_up,
    )
    return (
        estimate if _priced(estimate, what=f"the {brain} brain") is not None else None
    )


async def price_per_minute(
    session: AsyncSession,
    *,
    organization_id: int | None,
    brain: str,
    stt_tier: str = "default",
    tts_tier: str = "default",
    telephony_provider: str | None = None,
    marked_up: bool = True,
) -> int | None:
    """Paise per minute for a managed stack on this brain tier.

    Priced against what the tier resolves to today, because that is what the
    call will actually cost. The voice does not vary the price — every managed
    voice is the same tier and the same vendor rate — so it is not a parameter.

    ``None`` when any component of the stack has no rate on file. See
    :func:`_priced`.
    """
    estimate = await pipeline_estimate(
        session,
        organization_id=organization_id,
        brain=brain,
        stt_tier=stt_tier,
        tts_tier=tts_tier,
        telephony_provider=telephony_provider,
        marked_up=marked_up,
    )
    return None if estimate is None else estimate.total_paise_per_minute


async def realtime_price_per_minute(
    session: AsyncSession,
    *,
    organization_id: int | None,
    realtime_tier: str,
    telephony_provider: str | None = None,
    marked_up: bool = True,
) -> int | None:
    """Paise per minute for a speech-to-speech stack.

    One model replaces the transcriber and the voice, so there is one vendor
    to price. It is passed as the *llm* slot because that is how a realtime
    session is metered — the vendor bills it as language-model usage and the
    rate card prices it there, which is also why the estimator's realtime
    token assumption lives under that component.

    The tier names the vendor the way ``service_factory`` needs it and the rate
    card names it the way the pipeline records it;
    ``estimator.rate_card_provider`` is what makes those the same lookup.

    ``None`` when the model has no rate on file. See :func:`_priced`.
    """
    estimate = await realtime_estimate(
        session,
        organization_id=organization_id,
        realtime_tier=realtime_tier,
        telephony_provider=telephony_provider,
        marked_up=marked_up,
    )
    return None if estimate is None else estimate.total_paise_per_minute


async def realtime_estimate(
    session: AsyncSession,
    *,
    organization_id: int | None,
    realtime_tier: str,
    telephony_provider: str | None = None,
    marked_up: bool = True,
):
    """The itemised estimate for a speech-to-speech stack, or ``None``.

    The realtime counterpart of :func:`pipeline_estimate`, and it exists for
    the same reason: the card and the breakdown beside it must be two views of
    one calculation rather than two calculations.
    """
    upstream = managed_tiers.resolve(managed_tiers.REALTIME_COMPONENT, realtime_tier)
    estimate = await estimate_cost_per_minute(
        session,
        organization_id=organization_id,
        # What a new agent runs on every call whether or not anybody
        # asked for it. Left out, every quote on this screen was short
        # by the QA fee — in the one direction a price must not be.
        addons=DEFAULT_AGENT_ADDONS,
        llm_provider=upstream.provider,
        llm_model=upstream.model,
        telephony_provider=telephony_provider,
        marked_up=marked_up,
    )
    priced = _priced(estimate, what=f"the {realtime_tier} speech-to-speech tier")
    return estimate if priced is not None else None


#: The component lines a Simple-tab breakdown may name. Anything the estimator
#: itemises that is not in here is folded into "agent" rather than shown, so a
#: new line added to the estimator cannot leak a vendor onto this screen by
#: appearing on it unannounced.
_SIMPLE_LINE_LABELS: dict[str, str] = {
    "stt": "Transcription",
    "llm": "Brain",
    "tts": "Voice",
    "telephony": "Telephony",
    "platform": "Platform fee",
}


def _breakdown(estimate) -> dict | None:
    """What makes up a variant's price, with the vendors taken out.

    The Simple tab is the screen that deliberately does not name Sarvam or
    OpenAI — that is the whole reason it exists beside Advanced. So this
    carries the same figures the itemised bar on Advanced shows and none of the
    ``provider``/``model`` fields, because a breakdown that named them would
    undo the split by being the one place a vendor appears.

    Built from the estimate the card's own price came from, never a second
    call: a bar whose segments are priced separately from the headline above
    them is a bar that disagrees with it the first time a rate moves.
    """
    if estimate is None:
        return None
    lines = [
        {
            "component": line.component,
            "label": _SIMPLE_LINE_LABELS.get(line.component, "Agent"),
            "paise_per_minute": line.paise_per_minute,
        }
        for line in estimate.lines
    ]
    return {
        "agent_paise_per_minute": estimate.agent_paise_per_minute,
        "telephony_paise_per_minute": estimate.telephony_paise_per_minute,
        "platform_paise_per_minute": estimate.platform_paise_per_minute,
        "addon_paise_per_minute": estimate.addon_paise_per_minute,
        "pulse_seconds": estimate.pulse_seconds,
        "lines": lines,
    }


async def bundle_options(
    session: AsyncSession,
    *,
    organization_id: int | None,
    telephony_provider: str | None = None,
) -> list[dict]:
    """The Simple picker's cards, priced, with the residency badge on each.

    Every bundle carries **variants**, even when there is only one. A pipeline
    bundle has three — the brain is the customer's choice and it is the
    component that moves both the price and the badge — while a
    speech-to-speech bundle has exactly one. Making that uniform means the
    screen renders one shape instead of branching on architecture, and adding a
    fourth bundle later needs no new case.

    Priced through the same estimator the receipt reconciles against, never a
    second calculation. Every pricing bug found this week came from a parallel
    sum drifting from the first one.
    """
    from api.services.configuration import bundles as bundle_service
    from api.services.configuration import managed_resolution
    from api.services.configuration.residency import assess

    rows = await bundle_service.list_bundles(session, enabled_only=True)
    # Per tier, not per section. A bundle names one specific tier, and a tier
    # resolving to a vendor we hold no key for is a call that fails after it
    # connects -- so the card has to say so before it is bought, not after.
    keyed = await managed_resolution.tier_availability(session)
    out: list[dict] = []

    for row in rows:
        variants: list[dict] = []
        # One price a minute, everything included, when the bundle has one.
        # The estimate is still computed for the economics screen; the
        # customer sees the number on the tag.
        list_price = bundle_service.flat_rate_paise(row)

        if row.architecture == bundle_service.REALTIME:
            estimate = await realtime_estimate(
                session,
                organization_id=organization_id,
                realtime_tier=row.realtime_tier,
                telephony_provider=telephony_provider,
            )
            variants.append(
                {
                    "tier": row.realtime_tier,
                    "label": row.label,
                    "blurb": "",
                    "paise_per_minute": (
                        list_price
                        if list_price is not None
                        else None
                        if estimate is None
                        else estimate.total_paise_per_minute
                    ),
                    "breakdown": None
                    if list_price is not None
                    else _breakdown(estimate),
                    "india_only": assess(
                        architecture="realtime", realtime_tier=row.realtime_tier
                    ).india_only,
                    "available": keyed.get("realtime", {}).get(
                        row.realtime_tier or "default", False
                    ),
                }
            )
        else:
            # ``llm_tier`` pinned on the row means the bundle chose the brain;
            # null means the customer does, which is the Everyday case.
            tiers = [row.llm_tier] if row.llm_tier else list(managed_tiers.LLM_TIERS)
            for tier in tiers:
                label, blurb = managed_tiers.LLM_TIER_LABELS.get(
                    tier, (tier.title(), "")
                )
                estimate = await pipeline_estimate(
                    session,
                    organization_id=organization_id,
                    brain=tier,
                    stt_tier=row.stt_tier or "default",
                    tts_tier=row.tts_tier or "default",
                    telephony_provider=telephony_provider,
                )
                variants.append(
                    {
                        "tier": tier,
                        "label": label,
                        "blurb": blurb,
                        "paise_per_minute": (
                            list_price
                            if list_price is not None
                            else None
                            if estimate is None
                            else estimate.total_paise_per_minute
                        ),
                        "breakdown": None
                        if list_price is not None
                        else _breakdown(estimate),
                        "india_only": assess(
                            architecture="pipeline",
                            llm_tier=tier,
                            stt_tier=row.stt_tier,
                            tts_tier=row.tts_tier,
                        ).india_only,
                        # All three, because a pipeline call needs ears, a
                        # brain and a voice. One missing key is one silent
                        # failure.
                        "available": (
                            keyed.get("llm", {}).get(tier, False)
                            and keyed.get("stt", {}).get(
                                row.stt_tier or "default", False
                            )
                            and keyed.get("tts", {}).get(
                                row.tts_tier or "default", False
                            )
                        ),
                    }
                )

        out.append(
            {
                "slug": row.slug,
                "label": row.label,
                "blurb": row.blurb,
                "architecture": row.architecture,
                # A voice is only chosen on the pipeline path: a
                # speech-to-speech model brings its own and there is nothing to
                # pick. The screen reads this rather than re-deriving it from
                # the architecture string.
                "picks_voice": row.architecture == bundle_service.PIPELINE,
                # The bundle is buyable if any of its variants is. A bundle
                # whose every variant is unservable is shown disabled rather
                # than hidden -- see managed_availability on why unavailable
                # beats absent.
                "available": any(v.get("available") for v in variants),
                "variants": variants,
            }
        )
    return out


class SelectionError(ValueError):
    """A bundle choice that could not be saved as asked for."""


async def selected_bundle(*, organization_id: int | None) -> dict | None:
    """The Simple choice currently in force, or ``None`` if there is not one.

    Read from the account's stored managed configuration rather than kept in a
    second table. There is one answer to "what does this account run on" and it
    is the configuration the call itself resolves — a parallel record of the
    picker's last click is a record that goes stale the first time somebody
    saves from the Advanced tab.

    ``None`` when the account is on BYOK or has never saved: the Simple picker
    has nothing to restore, and it should show its defaults rather than claim a
    selection the account is not on.
    """
    from api.services.configuration.ai_model_configuration import (
        get_organization_ai_model_configuration_v2,
    )

    stored = await get_organization_ai_model_configuration_v2(organization_id)
    if stored is None or stored.mode != "decibyl" or stored.decibyl is None:
        return None
    managed = stored.decibyl
    realtime_tier = (managed.realtime_tier or "").strip()
    return {
        "bundle": managed.bundle or "",
        "tier": realtime_tier or managed.llm_tier,
        "voice": managed.voice,
    }


async def _bundle_configuration(
    session: AsyncSession,
    *,
    organization_id: int,
    bundle_slug: str,
    tier: str,
    voice: str,
):
    """A Simple choice, resolved and compiled, ready to be stored anywhere.

    Shared by the account default and the per-agent choice so the two cannot
    drift: both go through the same bundle lookup, the same tier check, the
    same compile. Returns the v2 configuration and its managed section.

    Everything the customer chose is resolved here from the bundle row rather
    than taken from the request: the client sends a slug, a tier and a voice,
    and the speech tiers behind them are looked up. A client that could name
    its own STT tier could name one nobody has priced, and the first anyone
    would know is a call billed against a rate that does not exist.

    Writes the same v2 managed shape the Advanced tab writes, so the two tabs
    remain two vocabularies for one stored answer rather than two stores.
    """
    from api.schemas.ai_model_configuration import (
        DecibylManagedAIModelConfiguration,
        OrganizationAIModelConfigurationV2,
        compile_ai_model_configuration_v2,
    )
    from api.services.configuration import bundles as bundle_service
    from api.services.configuration.ai_model_configuration import (
        get_organization_ai_model_configuration_v2,
    )

    # The account's model gateway service key, carried forward rather than
    # rewritten. It is minted once per organization at signup and is the only
    # copy: nothing here can mint another, so writing a configuration without
    # it does not "clear a field", it destroys the credential.
    #
    # This is not hypothetical. Saving a bundle used to build a fresh managed
    # configuration and let ``api_key`` take its empty default, which passed
    # every validator — an empty key is the ordinary case for a managed slot —
    # and then refused every call the account made with "You have invalid keys
    # in your model configuration". The stack was right, the tiers were right,
    # and the credential the gateway authenticates with was gone.
    #
    # ``merge_ai_model_configuration_v2_secrets`` does not cover this. It
    # restores a key the client sent back *masked*; a key that is simply absent
    # reads as a deliberate empty value and is written as one.
    existing = await get_organization_ai_model_configuration_v2(organization_id)
    service_key = ""
    if existing is not None and existing.decibyl is not None:
        service_key = existing.decibyl.api_key or ""

    rows = await bundle_service.list_bundles(session, enabled_only=True)
    row = next((r for r in rows if r.slug == bundle_slug), None)
    if row is None:
        raise SelectionError(f"{bundle_slug!r} is not a bundle on offer.")

    chosen = (tier or "").strip()
    if row.architecture == bundle_service.REALTIME:
        # One model hears and speaks, so there is exactly one tier it can be
        # and the request does not get to name a different one.
        if chosen and chosen != row.realtime_tier:
            raise SelectionError(f"{row.label} does not offer a {chosen!r} option.")
        managed = DecibylManagedAIModelConfiguration(
            api_key=service_key,
            bundle=row.slug,
            realtime_tier=row.realtime_tier,
            # Carried so a later switch back to a pipeline bundle does not land
            # on a tier nobody chose. It is not read while realtime_tier is set.
            llm_tier="default",
            voice=voice or DECIBYL_DEFAULT_VOICE,
        )
    else:
        offered = [row.llm_tier] if row.llm_tier else list(managed_tiers.LLM_TIERS)
        if chosen not in offered:
            raise SelectionError(f"{row.label} does not offer a {chosen!r} brain.")
        # The gender sentinels are not catalogue entries and must not be
        # checked against one: they name what to resolve at pipeline build,
        # from whichever vendor the tier is on then. Validating them here
        # against today's voice list is how "male" becomes unsaveable.
        if (
            voice
            and voice not in DECIBYL_GENDER_VOICES
            and voice not in {v.voice_id for v in voices()}
        ):
            raise SelectionError(f"{voice!r} is not a voice we offer.")
        managed = DecibylManagedAIModelConfiguration(
            api_key=service_key,
            bundle=row.slug,
            llm_tier=chosen,
            stt_tier=row.stt_tier or "default",
            tts_tier=row.tts_tier or "default",
            voice=voice or DECIBYL_DEFAULT_VOICE,
        )

    configuration = OrganizationAIModelConfigurationV2(
        version=2, mode="decibyl", decibyl=managed
    )
    # Compiled before it is stored, not after. Everything above is built from a
    # bundle row an operator owns, so a combination that cannot be flattened
    # into a runnable stack is a misconfigured bundle — and the place to find
    # that out is here, as a refused save, rather than on the first call the
    # account makes.
    try:
        compile_ai_model_configuration_v2(configuration)
    except ValueError as exc:
        raise SelectionError(str(exc)) from exc

    return configuration, managed


def _selection_view(managed) -> dict:
    return {
        "bundle": managed.bundle,
        "tier": (managed.realtime_tier or "").strip() or managed.llm_tier,
        "voice": managed.voice,
    }


async def save_bundle_selection(
    session: AsyncSession,
    *,
    organization_id: int,
    bundle_slug: str,
    tier: str,
    voice: str,
) -> dict:
    """Store a Simple choice as this account's default managed stack.

    Writes the same v2 managed shape the Advanced tab writes, so the two tabs
    remain two vocabularies for one stored answer rather than two stores.
    """
    from api.services.configuration.ai_model_configuration import (
        upsert_organization_ai_model_configuration_v2,
    )

    configuration, managed = await _bundle_configuration(
        session,
        organization_id=organization_id,
        bundle_slug=bundle_slug,
        tier=tier,
        voice=voice,
    )
    await upsert_organization_ai_model_configuration_v2(organization_id, configuration)
    return _selection_view(managed)


# ---------------------------------------------------------------------------
# The same choice, on one agent.
#
# Vapi and Bolna put the model on the assistant, not the account, and that is
# what a customer expects: the receptionist runs on the cheap fast brain, the
# collections agent on the careful one. The account default stays as the
# fallback for an agent that has not chosen, so nothing built before this
# existed changes behaviour.
# ---------------------------------------------------------------------------


def with_bundle_override(configurations: dict | None, configuration) -> dict:
    """The agent's configurations with this bundle as its model override.

    Pure, so it can be tested without a database. The legacy per-slot
    ``model_overrides`` is dropped: it and the v2 override are two answers to
    one question, and the settings screen already removes one when it writes
    the other.
    """
    from api.services.configuration.ai_model_configuration import (
        WORKFLOW_MODEL_CONFIGURATION_V2_OVERRIDE_KEY,
    )

    next_configurations = dict(configurations or {})
    next_configurations.pop("model_overrides", None)
    next_configurations[WORKFLOW_MODEL_CONFIGURATION_V2_OVERRIDE_KEY] = (
        configuration.model_dump()
        if hasattr(configuration, "model_dump")
        else dict(configuration)
    )
    return next_configurations


def selected_bundle_from_configurations(configurations: dict | None) -> dict | None:
    """The Simple choice an agent's own override expresses, or ``None``.

    ``None`` for an agent with no override, for a BYOK override, and for an
    override written in the v3 stack shape (the template voice choice) — that
    shape records a voice, not a bundle, and claiming a bundle for it would
    show a card the agent is not on.
    """
    from api.services.configuration.ai_model_configuration import (
        WORKFLOW_MODEL_CONFIGURATION_V2_OVERRIDE_KEY,
    )

    override = (configurations or {}).get(WORKFLOW_MODEL_CONFIGURATION_V2_OVERRIDE_KEY)
    if not isinstance(override, dict) or override.get("mode") != "decibyl":
        return None
    managed = override.get("decibyl")
    if not isinstance(managed, dict):
        return None
    realtime_tier = (managed.get("realtime_tier") or "").strip()
    return {
        "bundle": managed.get("bundle") or "",
        "tier": realtime_tier or managed.get("llm_tier") or "",
        "voice": managed.get("voice") or "",
    }


async def _workflow_configurations(workflow_id: int, organization_id: int) -> dict:
    """What the agent runs on now: its draft if it has one, else the release."""
    from api.db import db_client

    workflow = await db_client.get_workflow(
        workflow_id, organization_id=organization_id
    )
    if workflow is None:
        raise SelectionError("Agent not found.")
    draft = await db_client.get_draft_version(workflow_id)
    source = draft or workflow.released_definition
    if source is not None and source.workflow_configurations is not None:
        return dict(source.workflow_configurations)
    return dict(workflow.workflow_configurations or {})


async def selected_bundle_for_workflow(
    *, workflow_id: int, organization_id: int
) -> dict | None:
    """The agent's own Simple choice, falling back to the account's."""
    own = selected_bundle_from_configurations(
        await _workflow_configurations(workflow_id, organization_id)
    )
    if own is not None:
        return own
    return await selected_bundle(organization_id=organization_id)


async def save_workflow_bundle_selection(
    session: AsyncSession,
    *,
    workflow_id: int,
    organization_id: int,
    bundle_slug: str,
    tier: str,
    voice: str,
) -> dict:
    """Make this bundle the agent's own stack.

    Saved as a draft of the agent, the same way every other agent setting is,
    so it goes live when the agent is next published and nothing changes on a
    call that is already running.
    """
    from api.db import db_client

    configuration, managed = await _bundle_configuration(
        session,
        organization_id=organization_id,
        bundle_slug=bundle_slug,
        tier=tier,
        voice=voice,
    )
    current = await _workflow_configurations(workflow_id, organization_id)
    await db_client.update_workflow(
        workflow_id,
        name=None,
        workflow_definition=None,
        template_context_variables=None,
        workflow_configurations=with_bundle_override(current, configuration),
        organization_id=organization_id,
    )
    return _selection_view(managed)


# ---------------------------------------------------------------------------
# One slot at a time.
#
# The Advanced view of an agent's Models tab is three tiles — transcriber,
# brain, voice — or one for speech-to-speech, each with a pencil. Changing
# one must not touch the others: somebody swapping the voice has not asked
# for a different brain, and a preset that quietly did that is the mistake
# `apply_model_preset` already refuses to make. So the write is a copy of the
# stack with one section replaced.
# ---------------------------------------------------------------------------

STACK_SECTIONS = ("llm", "stt", "tts", "realtime", "embeddings")

#: Every slot a cascade needs. A speech-to-speech stack drops stt and tts; a
#: stack switched back to the cascade gets them again as managed defaults.
_CASCADE_DEFAULTS = {
    "stt": {
        "provider": ServiceProviders.DECIBYL.value,
        "model": "default",
        "api_key": "",
    },
    "tts": {
        "provider": ServiceProviders.DECIBYL.value,
        "model": "default",
        "api_key": "",
    },
}


def stack_from_configurations(effective) -> dict:
    """An effective configuration as the v3 stack shape an override stores.

    Pre-resolution: sections still say "decibyl" or carry
    ``use_platform_key``, which is what a stored override must say — a
    resolved vendor key in a workflow's JSON would be a copy of a secret in
    a second place. A section on the customer's own inline key keeps that
    key, exactly as the per-slot editor already stores it.
    """
    stack: dict = {"architecture": "realtime" if effective.is_realtime else "pipeline"}
    for name in STACK_SECTIONS:
        section = getattr(effective, name, None)
        if section is None:
            continue
        stack[name] = section.model_dump(mode="json", exclude_none=True)
    if not effective.is_realtime:
        stack.pop("realtime", None)
    return stack


#: What a slot's settings panel may tune besides the model itself, per slot.
#: Temperature and reply length are the two knobs Vapi puts under a model;
#: speed and language are what a voice has. Anything else a vendor accepts
#: is the per-slot editor's business, not the pencil's.
SLOT_TUNING: dict[str, tuple[str, ...]] = {
    "stt": ("language",),
    "llm": ("temperature", "max_tokens"),
    "tts": ("speed", "language"),
    "realtime": (),
}


def slot_tuning(component: str, section) -> dict:
    """The tunable values a section currently carries, for the panel to open on.

    Only the keys the panel can write, and only where the section has them:
    a vendor class without ``temperature`` contributes nothing rather than
    ``None``, so the panel shows the vendor's own default as blank.
    """
    if section is None:
        return {}
    out = {}
    for key in SLOT_TUNING.get(component, ()):
        value = getattr(section, key, None)
        if value is not None:
            out[key] = value
    return out


def with_model_slot(
    stack: dict,
    *,
    component: str,
    provider: str,
    model: str,
    voice: str | None = None,
    tuning: dict | None = None,
) -> dict:
    """The stack with one slot pointed at a managed catalogue model.

    Pure, so it is testable without a database. Writes the direct managed
    shape — a real vendor and model on ``use_platform_key`` — rather than a
    tier, because the person chose this model by name and a tier would let
    it move under them.

    ``tuning`` carries the slot's own knobs (see ``SLOT_TUNING``). A value
    given is written; a key absent or ``None`` leaves what the section has,
    so the panel can change the speed without restating the language. Keys
    the slot does not tune are ignored rather than stored.

    Choosing a speech-to-speech model switches the architecture and drops
    the transcriber and voice, which that model replaces; choosing any
    cascade slot on a realtime stack switches back and restores the two as
    managed defaults, so the stack is always one that can run.
    """
    if component not in ("stt", "llm", "tts", "realtime"):
        raise SelectionError(f"{component!r} is not a slot an agent has.")
    next_stack = {k: (dict(v) if isinstance(v, dict) else v) for k, v in stack.items()}
    section = dict(next_stack.get(component) or {})
    # Vendor-specific fields (voice, language, speed) carry over only within
    # the same vendor; another vendor's voice id names nothing here.
    if section.get("provider") != provider:
        section = {}
    section.update(
        {"provider": provider, "model": model, "api_key": "", "use_platform_key": True}
    )
    if component == "tts" and voice:
        section["voice"] = voice
    for key in SLOT_TUNING[component]:
        value = (tuning or {}).get(key)
        if value is not None:
            section[key] = value
    next_stack[component] = section

    if component == "realtime":
        next_stack["architecture"] = "realtime"
        next_stack.pop("stt", None)
        next_stack.pop("tts", None)
        # The schema wants an llm under both architectures; the realtime
        # model is the brain, so it stands in when none is recorded.
        next_stack.setdefault("llm", dict(section))
    else:
        if next_stack.get("architecture") == "realtime":
            next_stack["architecture"] = "pipeline"
            next_stack.pop("realtime", None)
        for name, default in _CASCADE_DEFAULTS.items():
            next_stack.setdefault(name, dict(default))
        if "llm" not in next_stack:
            next_stack["llm"] = {
                "provider": ServiceProviders.DECIBYL.value,
                "model": "default",
                "api_key": "",
            }
    return next_stack


async def bundle_economics(
    session: AsyncSession, *, telephony_provider: str | None = None
) -> list[dict]:
    """The same bundles, with what each one earns.

    The operator's version of :func:`bundle_options`. It asks the estimator the
    same question twice — once with the managed markup and once without — so
    ``price`` is exactly the number quoted to a customer and ``cost`` is
    exactly the vendor bill behind it. The margin is their difference, not a
    third calculation: a margin computed independently is a margin that drifts,
    and the drift only shows up in a month-end reconciliation.

    Priced at the **list** rate, with no account attached. Pricing a bundle
    against whichever account happened to be at hand would quote one
    customer's negotiated contract as though it were everybody's.

    Returned per variant rather than per bundle because on a pipeline bundle
    the brain is the customer's choice, and Lite and Smart do not earn the
    same thing.
    """
    from api.services.configuration import bundles as bundle_service

    priced = await bundle_options(
        session, organization_id=None, telephony_provider=telephony_provider
    )
    # The speech tiers each bundle runs on, which the customer-facing payload
    # above deliberately does not carry. The cost side has to price the same
    # stack the price side did, or the margin is the difference between two
    # different bundles.
    speech = {
        row.slug: (row.stt_tier or "default", row.tts_tier or "default")
        for row in await bundle_service.list_bundles(session, enabled_only=True)
    }
    out: list[dict] = []

    for bundle in priced:
        variants: list[dict] = []
        for variant in bundle["variants"]:
            price = variant["paise_per_minute"]
            if bundle["architecture"] == "realtime":
                cost = await realtime_price_per_minute(
                    session,
                    organization_id=None,
                    realtime_tier=variant["tier"],
                    telephony_provider=telephony_provider,
                    marked_up=False,
                )
            else:
                stt_tier, tts_tier = speech.get(bundle["slug"], ("default", "default"))
                cost = await price_per_minute(
                    session,
                    organization_id=None,
                    brain=variant["tier"],
                    stt_tier=stt_tier,
                    tts_tier=tts_tier,
                    telephony_provider=telephony_provider,
                    marked_up=False,
                )
            # Either half missing means there is no margin to state. Both come
            # from the same estimator and the same rate card, so in practice
            # they are missing together — but subtracting a present cost from
            # an absent price is exactly how a bundle nobody can price would
            # have reported a margin anyway.
            priced = price is not None and cost is not None
            variants.append(
                {
                    **variant,
                    "cost_paise_per_minute": cost,
                    "margin_paise_per_minute": (price - cost) if priced else None,
                    # Expressed against the price rather than the cost, because
                    # that is the margin an investor and a discount both read.
                    # Null rather than zero when nothing is priced yet: a bundle
                    # with no rate card behind it has no margin, and 0% would
                    # read as one we chose.
                    "margin_pct": (
                        round((price - cost) / price * 100, 1)
                        if priced and price
                        else None
                    ),
                }
            )
        out.append({**bundle, "variants": variants})
    return out


def approximate_minutes(balance_paise: int, paise_per_minute: int | None) -> int | None:
    """How long a balance lasts, roughly.

    Returned as a number to *show*, not to bill against. It moves with the rate
    card and with what the agent actually says, so it is an estimate in the
    honest sense — which is why the caller is expected to render it with a
    "roughly" in front of it rather than as an entitlement.

    ``None`` when the stack cannot be priced: a zero would read as "this is
    free", which is the one thing it never means. So does a ``None`` rate,
    which is how :func:`price_per_minute` reports a component with no rate on
    file — quoting a balance as minutes at a price that is missing its largest
    line would multiply the error rather than surface it.
    """
    if paise_per_minute is None or paise_per_minute <= 0:
        return None
    return balance_paise // paise_per_minute


def managed_stack_override(
    *,
    voice: str,
    llm_tier: str,
    realtime_tier: str = "",
    stt_tier: str = "default",
    tts_tier: str = "default",
) -> dict:
    """A bundle choice, as an agent-level model override.

    Written as a v3 stack with every slot still saying ``decibyl``. That
    matters: a slot naming a tier is resolved to a vendor at call time by
    ``managed_resolution``, so this records *the product choice* rather than
    pinning a vendor model that would then not move when the tier does.

    ``realtime_tier`` selects the speech-to-speech shape instead of the
    cascade. The two are mutually exclusive by construction rather than by
    validation: one model that hears and speaks replaces the transcriber and
    the voice, so emitting both would describe an agent that cannot exist.

    Returns an empty dict when nothing was chosen, so a caller that did not ask
    — an API client posting the old three-field body — inherits the
    organization default exactly as it did before.
    """
    managed = ServiceProviders.DECIBYL.value
    realtime = (realtime_tier or "").strip()

    if realtime:
        # No stt or tts slot at all. A realtime section that also named a
        # transcriber would be two answers to one question, and the compiler
        # would have to pick one.
        stack: dict[str, object] = {
            "architecture": "realtime",
            "realtime": {"provider": managed, "model": realtime, "api_key": ""},
            # The v3 schema requires an llm section under both architectures —
            # a realtime model *is* the language model, and this is the slot
            # that records which tier serves it.
            "llm": {"provider": managed, "model": realtime, "api_key": ""},
        }
        return {
            WORKFLOW_MODEL_CONFIGURATION_V2_OVERRIDE_KEY: {
                "version": 3,
                "stack": stack,
            }
        }

    if not (voice or "").strip() and not (llm_tier or "").strip():
        return {}

    stack = {
        "architecture": "pipeline",
        "llm": {
            "provider": managed,
            "model": (llm_tier or "default").strip(),
            "api_key": "",
        },
        "stt": {
            "provider": managed,
            "model": (stt_tier or "default").strip(),
            "api_key": "",
        },
        "tts": {
            "provider": managed,
            "model": (tts_tier or "default").strip(),
            "api_key": "",
            "voice": (voice or "").strip() or DECIBYL_DEFAULT_VOICE,
        },
    }
    return {
        WORKFLOW_MODEL_CONFIGURATION_V2_OVERRIDE_KEY: {"version": 3, "stack": stack}
    }


async def catalogue_options(
    session: AsyncSession, *, organization_id: int | None
) -> dict[str, list[dict]]:
    """Every managed model a customer may choose, per slot, with its price.

    The answer to "what does Decibyl provide", assembled from the one place
    that says so — the catalogue — rather than from the registry, which lists
    every vendor this codebase has ever integrated including the ones we hold
    no key for and have never priced.

    ``paise_per_minute`` is that component's own contribution to a minute, with
    the managed markup already on it: what the customer pays us for choosing it.
    It is not the price of a call, which also has the other slots, telephony and
    the platform fee in it — the picker shows the difference between two models,
    and the cost bar beside it shows the total.

    Priced through ``estimator.price_components``, which is built from the same
    line functions a full estimate is, so a model priced on this screen and the
    same model inside a stack estimate cannot disagree.
    """
    from api.services.billing.estimator import price_components
    from api.services.configuration import model_catalogue

    out: dict[str, list[dict]] = {}
    for component in ("stt", "llm", "tts", "realtime"):
        entries = await model_catalogue.sellable(session, component=component)
        if not entries:
            out[component] = []
            continue

        priced = await price_components(
            session,
            organization_id=organization_id,
            slots=[(component, entry.provider, entry.model) for entry in entries],
        )
        options: list[dict] = []
        for entry in entries:
            line = priced.get((component, entry.provider, entry.model))
            options.append(
                {
                    "provider": entry.provider,
                    "model": entry.model,
                    "label": entry.label,
                    # Null rather than zero when the rate vanished between the
                    # sellable check and here. Zero would read as free, which is
                    # the one thing it never means.
                    "paise_per_minute": line.paise_per_minute if line else None,
                    # True when priced against the vendor's default rather than
                    # this model — two models then show the same number, which
                    # reads as a broken calculator unless it is said.
                    "approximate": bool(line and line.rate_is_provider_fallback),
                }
            )
        # Cheapest first. A picker ordered by what a vendor happens to return is
        # a picker nobody can compare.
        options.sort(
            key=lambda o: (o["paise_per_minute"] is None, o["paise_per_minute"])
        )
        out[component] = options
    return out


async def model_row(
    session: AsyncSession,
    *,
    organization_id: int | None,
    workflow_id: int,
    workflow_configurations: dict | None,
) -> dict:
    """What this agent runs on, what each part costs, and how long each takes.

    The three cards at the top of the agent screen. Every competitor shows some
    version of this; the difference is where the latency comes from. Theirs is
    the vendor's datasheet. Ours is ``call_turn_metrics`` -- the median of this
    agent's own turns, on real calls, over Indian telephony -- which is a claim
    a datasheet cannot make and a competitor cannot copy.

    A managed slot is resolved to the vendor actually behind it before pricing,
    because "decibyl" is not a provider the rate card knows and a card that
    named it would be telling the customer nothing. Naming the real vendor here
    is the same decision the model-configuration defaults already took: a buyer
    comparing us against a platform that shows its stack will not accept
    "trust us".

    Latency is per stage and cost is per slot, so the two never have to be
    reconciled against a single blended number that means neither.
    """
    from api.db.workflow_latency_client import stage_latency
    from api.services.billing.estimator import price_components
    from api.services.configuration import (
        managed_resolution,
        managed_tiers,
        model_presets,
    )
    from api.services.configuration.ai_model_configuration import (
        get_effective_ai_model_configuration_for_workflow,
    )
    from api.services.telephony import carriage

    effective = await get_effective_ai_model_configuration_for_workflow(
        organization_id=organization_id,
        workflow_configurations=workflow_configurations,
    )

    def resolve(component: str, section) -> tuple[str, str] | None:
        """The real vendor and model behind a section, managed or not."""
        if section is None:
            return None
        provider = getattr(section, "provider", None)
        provider = provider.value if hasattr(provider, "value") else str(provider or "")
        model = str(getattr(section, "model", "") or "")
        if not provider:
            return None
        if provider == "decibyl":
            # On a managed section ``model`` is the tier name, not a model.
            upstream = managed_tiers.resolve(component, model or None)
            return upstream.provider, upstream.model
        return provider, model

    # Realtime replaces the transcriber/voice pair rather than joining it, so
    # the row is one card on that path and three on the cascade. The screen
    # renders whatever it is given rather than branching on architecture.
    #
    # No separate brain card on the realtime path. A speech-to-speech model
    # *is* the language model -- build_realtime_pipeline runs no second LLM --
    # and the ``llm`` section a realtime stack still carries names the realtime
    # tier, which ``managed_tiers.resolve`` then reads as an unknown *LLM* tier
    # and falls back to the default text model. Showing that card named an
    # OpenAI model the agent never runs.
    if effective.is_realtime:
        wanted = [
            ("realtime", "Speech", effective.realtime),
        ]
    else:
        wanted = [
            ("stt", "Transcriber", effective.stt),
            ("llm", "Model", effective.llm),
            ("tts", "Voice", effective.tts),
        ]

    section_by_component = {component: section for component, _title, section in wanted}
    resolved: list[tuple[str, str, str, str]] = []
    for component, title, section in wanted:
        pair = resolve(component, section)
        if pair is None:
            continue
        resolved.append((component, title, pair[0], pair[1]))

    # A realtime session has no per-minute line of its own: the vendor meters
    # it as language-model usage and the rate card prices it there, which is
    # the seam ``realtime_price_per_minute`` already goes through. Priced under
    # its own name it comes back unpriced, and the card reads "--".
    def _rate_component(component: str) -> str:
        return "llm" if component == "realtime" else component

    priced = await price_components(
        session,
        organization_id=organization_id,
        slots=[(_rate_component(c), p, m) for c, _title, p, m in resolved],
    )

    # The whole minute, not the sum of the cards. Telephony and the platform
    # fee are in a connected minute and in neither of the three slots, so a
    # total added up from the cards would be quietly low -- which is the one
    # direction a price shown to a customer must never be wrong in.
    by_component = {c: (p, m) for c, _title, p, m in resolved}
    llm_pair = by_component.get("realtime") or by_component.get("llm", (None, ""))
    # The carrier a call would actually leave through. Without it
    # estimate_cost_per_minute adds no telephony line at all, which runs about
    # ten per cent light on an account whose numbers are ours -- the same
    # omission the agent-options route already documents having shipped once.
    basis = await carriage.billable_carrier(
        session, organization_id=organization_id or 0
    )
    estimate = await estimate_cost_per_minute(
        session,
        organization_id=organization_id,
        # What a new agent runs on every call whether or not anybody
        # asked for it. Left out, every quote on this screen was short
        # by the QA fee — in the one direction a price must not be.
        addons=DEFAULT_AGENT_ADDONS,
        stt_provider=by_component.get("stt", (None, ""))[0],
        stt_model=by_component.get("stt", (None, ""))[1],
        # The realtime model goes in the llm slot for the same reason it is
        # priced as one -- see realtime_estimate, which this must agree with.
        # Passing the stack's vestigial text-LLM section instead quoted a
        # speech-to-speech agent at a text model's price, several times under
        # what the call actually bills.
        llm_provider=llm_pair[0],
        llm_model=llm_pair[1],
        tts_provider=by_component.get("tts", (None, ""))[0],
        tts_model=by_component.get("tts", (None, ""))[1],
        telephony_provider=basis.provider,
    )

    latency = await stage_latency(
        session, workflow_id=workflow_id, organization_id=organization_id or 0
    )
    # One stage per card. Realtime hears and speaks in one model, so its stage
    # is the whole turn rather than a slice of it.
    stage_key = {
        "stt": "transcribe_ms",
        "llm": "think_ms",
        "tts": "speak_ms",
        "realtime": "total_ms",
    }

    slots = []
    for component, title, provider, model in resolved:
        line = priced.get((_rate_component(component), provider, model))
        slots.append(
            {
                "component": component,
                "title": title,
                "provider": provider,
                "model": model,
                # Null rather than zero when nothing is on file: zero reads as
                # free, which is the one thing it never means.
                "paise_per_minute": line.paise_per_minute if line else None,
                "approximate": bool(line and line.rate_is_provider_fallback),
                "latency_ms": (latency or {}).get(stage_key[component]),
                # The voice tile's pencil opens on the voice it has, not the
                # first in the list. Only the voice slot carries one.
                "voice": (
                    getattr(effective.tts, "voice", None)
                    if component == "tts"
                    else None
                ),
                # What the slot's panel can tune, as it stands, so the
                # sliders open where the agent is rather than at a default.
                "tuning": slot_tuning(component, section_by_component.get(component)),
            }
        )

    # Which named stack this is, and which of them we can serve. Derived from
    # the tiers the stack names rather than stored, so an agent tuned by hand
    # reads as custom instead of mislabelling itself -- the same rule
    # LATENCY_PRESETS follows for turn timings.
    keyed = await managed_resolution.tier_availability(session)
    presets = [
        {
            "slug": preset.slug,
            "label": preset.label,
            "blurb": preset.blurb,
            "available": model_presets.is_available(preset, keyed),
        }
        for preset in model_presets.MODEL_PRESETS
    ]

    return {
        "is_realtime": effective.is_realtime,
        "slots": slots,
        "presets": presets,
        "active_preset": model_presets.match(effective),
        # The same three segments the wizard's bar and the receipt use, so a
        # figure here and a figure on an invoice cannot tell different stories.
        "cost": {
            "total_paise_per_minute": estimate.total_paise_per_minute,
            "agent_paise_per_minute": estimate.agent_paise_per_minute,
            "telephony_paise_per_minute": estimate.telephony_paise_per_minute,
            "platform_paise_per_minute": estimate.platform_paise_per_minute,
            "unpriced": list(estimate.unpriced),
            # False when this account's numbers are not ours, so the bar can
            # say the telephony segment is missing rather than imply it is nil.
            "includes_telephony": basis.provider is not None,
        },
        # Present but null-filled when the agent has not run enough turns to
        # say. The screen says "not enough calls yet" rather than printing a
        # median of six turns as though it were a measurement.
        "latency": latency,
    }
