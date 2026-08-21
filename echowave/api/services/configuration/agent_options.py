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

from sqlalchemy.ext.asyncio import AsyncSession

from api.schemas.ai_model_configuration import DECIBYL_DEFAULT_VOICE
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


async def price_per_minute(
    session: AsyncSession,
    *,
    organization_id: int,
    brain: str,
    telephony_provider: str | None = None,
) -> int:
    """Paise per minute for a managed stack on this brain tier.

    Priced against what the tier resolves to today, because that is what the
    call will actually cost. The voice does not vary the price — every managed
    voice is the same tier and the same vendor rate — so it is not a parameter.
    """
    llm = managed_tiers.resolve("llm", brain)
    stt = managed_tiers.resolve("stt", "default")
    tts = managed_tiers.resolve("tts", "default")

    estimate = await estimate_cost_per_minute(
        session,
        organization_id=organization_id,
        stt_provider=stt.provider,
        stt_model=stt.model,
        llm_provider=llm.provider,
        llm_model=llm.model,
        tts_provider=tts.provider,
        tts_model=tts.model,
        telephony_provider=telephony_provider,
    )
    return estimate.total_paise_per_minute


async def realtime_price_per_minute(
    session: AsyncSession,
    *,
    organization_id: int,
    realtime_tier: str,
    telephony_provider: str | None = None,
) -> int:
    """Paise per minute for a speech-to-speech stack.

    One model replaces the transcriber and the voice, so there is one vendor
    to price. It is passed as the *llm* slot because that is how a realtime
    session is metered — the vendor bills it as language-model usage and the
    rate card prices it there, which is also why the estimator's realtime
    token assumption lives under that component.
    """
    upstream = managed_tiers.resolve(managed_tiers.REALTIME_COMPONENT, realtime_tier)
    estimate = await estimate_cost_per_minute(
        session,
        organization_id=organization_id,
        llm_provider=upstream.provider,
        llm_model=upstream.model,
        telephony_provider=telephony_provider,
    )
    return estimate.total_paise_per_minute


async def bundle_options(
    session: AsyncSession,
    *,
    organization_id: int,
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
    from api.services.configuration.residency import assess

    rows = await bundle_service.list_bundles(session, enabled_only=True)
    out: list[dict] = []

    for row in rows:
        variants: list[dict] = []

        if row.architecture == bundle_service.REALTIME:
            price = await realtime_price_per_minute(
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
                    "paise_per_minute": price,
                    "india_only": assess(
                        architecture="realtime", realtime_tier=row.realtime_tier
                    ).india_only,
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
                price = await price_per_minute(
                    session,
                    organization_id=organization_id,
                    brain=tier,
                    telephony_provider=telephony_provider,
                )
                variants.append(
                    {
                        "tier": tier,
                        "label": label,
                        "blurb": blurb,
                        "paise_per_minute": price,
                        "india_only": assess(
                            architecture="pipeline",
                            llm_tier=tier,
                            stt_tier=row.stt_tier,
                            tts_tier=row.tts_tier,
                        ).india_only,
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
                "variants": variants,
            }
        )
    return out


def approximate_minutes(balance_paise: int, paise_per_minute: int) -> int | None:
    """How long a balance lasts, roughly.

    Returned as a number to *show*, not to bill against. It moves with the rate
    card and with what the agent actually says, so it is an estimate in the
    honest sense — which is why the caller is expected to render it with a
    "roughly" in front of it rather than as an entitlement.

    ``None`` when the stack cannot be priced: a zero would read as "this is
    free", which is the one thing it never means.
    """
    if paise_per_minute <= 0:
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
