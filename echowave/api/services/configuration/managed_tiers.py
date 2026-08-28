"""What "Decibyl" means when a customer picks it as their provider.

A customer choosing ``decibyl`` for STT, LLM or TTS is choosing *not to hold an
API key*. They pick a tier — ``fast``, ``accurate`` — and we decide which vendor
serves it and pay for the inference, then bill it through the rate card. That is
the whole managed offering, and this module is the mapping it turns on.

Two properties matter more than the particular vendors chosen.

**The tier is a promise about behaviour, not about a vendor.** A customer who
built an agent on ``fast`` should keep working when we move that tier from one
provider to another — a better Indic model ships, a vendor raises prices, a
region goes down. Naming vendors in customer configuration would make every such
move a migration; naming tiers makes it a config change here.

**Nothing is hardcoded past this file.** Each mapping can be overridden by
environment variable, so switching the ``accurate`` tier from one model to
another is a restart rather than a release. The defaults are India-first because
that is the traffic: Indic STT and TTS beat their Western equivalents on Telugu
and Hindi by a margin no price difference covers.

The key itself never appears here. This module says *which* provider serves a
tier; ``platform_credentials.resolve_api_key`` says what we authenticate to it
with, and the two are deliberately separate so a mapping can be read and
reasoned about without touching plaintext secrets.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from loguru import logger

from api.enums import CostComponent

#: The tiers a customer can choose. Deliberately small and behavioural — adding
#: one is a product decision, and every tier must map for every component.
#:
#: Three, not five. ``fast``, ``lite`` and ``zen`` all resolved to the same
#: model, so the picker offered a choice between three identical things and
#: somebody choosing ``zen`` over ``lite`` for a better price was wrong in a
#: way the screen encouraged. The retired names still resolve — see
#: ``_RETIRED_LLM_TIERS`` — so a stored configuration naming one keeps the
#: cheap model it chose rather than being quietly upgraded.
LLM_TIERS = ("lite", "default", "accurate")

#: Names no longer offered, mapped to what they always were. Resolving them to
#: ``default`` instead would move an account from the cheapest model to the
#: middle one and change their bill without anyone asking.
_RETIRED_LLM_TIERS = {"fast": "lite", "zen": "lite"}

#: What the customer reads. The tier key is a storage detail; these are the
#: product, and they describe what the choice does rather than which vendor
#: serves it — the buyer this is aimed at does not know one from another and
#: should not have to.
LLM_TIER_LABELS: dict[str, tuple[str, str]] = {
    "lite": (
        "Lite",
        "Fastest and cheapest. Good for short, scripted calls.",
    ),
    "default": (
        "Normal",
        "Handles a real conversation. The right choice for most agents.",
    ),
    "accurate": (
        "Smart",
        "For calls where getting it wrong is expensive.",
    ),
}
#: Two, and the second one is a latency choice with a language price attached.
#:
#: ``default`` is saaras:v3 -- 22 Indian languages, code-mixed, and it holds
#: the turn until its final transcript lands. That wait measures ~1,172ms on
#: every turn (`call_turn_metrics`, `t_endpoint_fired_ms - t_user_stopped_ms`)
#: and is the largest single stage of a turn once the LLM is not misconfigured.
#:
#: ``instant`` is Deepgram Flux, which emits its own turn boundaries -- see
#: ``stt_uses_external_turns``. The endpointing budget does not shrink, it
#: disappears: nothing downstream waits on silence at all.
#:
#: It is not the default and should not become one. Flux multilingual covers
#: de/en/es/fr/hi/it/ja/nl/pt/ru. Hindi yes; Telugu, Tamil, Kannada, Bengali
#: and Marathi no -- and a caller answering in one of those is transcribed as
#: nothing, which is a worse call than a slow one. An agent that knows its
#: callers speak English or Hindi can buy the second back; an agent serving
#: the 22-language case cannot, and keeps the default.
#: What the customer reads, the same discipline as the brain tiers: what the
#: choice does, not who serves it. The language limit belongs on the screen --
#: it is the entire reason this is a choice and not an upgrade.
STT_TIER_LABELS: dict[str, tuple[str, str]] = {
    "default": (
        "Every language",
        "Understands 22 Indian languages, including switching mid-sentence.",
    ),
    "instant": (
        "Instant",
        "Replies about a second sooner. English and Hindi only.",
    ),
}
STT_TIERS = ("default", "instant")
TTS_TIERS = ("default",)
EMBEDDINGS_TIERS = ("default",)
#: Speech-to-speech tiers. Two, because the two vendors are not a quality
#: ladder — they are a price cliff. Gemini Live runs about Rs4.72 a minute and
#: OpenAI realtime about Rs16.45, three and a half times dearer for the same
#: job. Offering only the expensive one, as this did, made speech-to-speech a
#: tier almost nobody would choose once they saw the number.
REALTIME_TIERS = ("natural", "premium", "default")

#: What the customer reads, the same discipline as the brain tiers: what the
#: choice does, not who serves it.
REALTIME_TIER_LABELS: dict[str, tuple[str, str]] = {
    "natural": (
        "Natural",
        "Replies the instant you stop talking. Best value for a live feel.",
    ),
    "premium": (
        "Premium",
        "The most capable speech model. Noticeably dearer a minute.",
    ),
}

#: Embeddings are billed, in both of the places they are incurred, and this
#: comment used to say they were not. ``CostComponent.EMBEDDING`` now exists:
#: a knowledge-base search during a call produces an ordinary marked-up line
#: item on that call's receipt, and ingesting a document debits the ledger
#: directly through ``services/billing/embedding_ingestion.py`` — that one has
#: no call to attach a receipt to, which is the grain of truth the old comment
#: was built on.
#:
#: The mapping below is still why this constant exists: a managed
#: configuration emits an embeddings section, and it needs a real provider and
#: a real key to resolve to.
EMBEDDINGS_COMPONENT = "embeddings"

#: Speech-to-speech is not a billing component either — a realtime model is
#: metered as LLM usage, because that is what the vendor charges it as and what
#: the rate card prices. It still needs a provider and a key, so it is mapped
#: here alongside the rest.
REALTIME_COMPONENT = "realtime"


@dataclass(frozen=True)
class ManagedUpstream:
    """The real provider and model a managed tier resolves to."""

    provider: str
    model: str


def _tier(component: str, tier: str, provider: str, model: str) -> ManagedUpstream:
    """A mapping, overridable without a release.

    ``MANAGED_LLM_ACCURATE=google:gemini-3.5-flash`` moves that tier. The
    provider and model are read together because moving one without the other is
    almost always a mistake — a model name is only meaningful to the vendor that
    serves it.
    """
    raw = os.getenv(f"MANAGED_{component.upper()}_{tier.upper()}")
    if raw and ":" in raw:
        override_provider, override_model = raw.split(":", 1)
        return ManagedUpstream(override_provider.strip(), override_model.strip())
    return ManagedUpstream(provider, model)


def _defaults() -> dict[tuple[str, str], ManagedUpstream]:
    """India-first, because that is where the calls are.

    Sarvam serves speech at both ends: its models are trained on Indian
    languages and regional variation, and a generic multilingual voice reads as
    the wrong region to a Telugu or Marathi listener in a way no cost saving
    justifies. Gemini Flash carries the language model because at roughly eight
    paise a minute it is a rounding error against speech synthesis, so paying
    more there buys very little.
    """
    return {
        # --- LLM -----------------------------------------------------------
        # Off Google entirely. Flash-Lite 2.5 retired and its successor came
        # back 3.3x dearer at a price nobody had confirmed, which is a poor
        # foundation for a tier whose whole promise is a stable price.
        #
        # Lite is Sarvam, and it is the interesting one: eight times cheaper
        # per token than the Gemini it replaces, trained on Indian languages,
        # and the same vendor already serving managed transcription and voice —
        # so the cheap tier is one relationship and one key rather than three.
        # The conversational variant, not plain sarvam-105b. These tiers serve
        # voice calls, and sarvam-105b is a reasoning model: it thinks in
        # chain-of-thought before every reply and cannot be told not to, which
        # measured 6,045ms of a 7,554ms turn on run 77 with the caller waiting
        # in silence for all of it. The conversational model does no reasoning,
        # reaches first content in ~0.25s, and still calls tools.
        ("llm", "lite"): _tier("llm", "lite", "sarvam", "sarvam-105b-conversations"),
        ("llm", "default"): _tier("llm", "default", "openai", "gpt-4.1-mini"),
        ("llm", "accurate"): _tier("llm", "accurate", "openai", "gpt-4.1"),
        # Retired names, kept resolving to the model they always served.
        ("llm", "fast"): _tier("llm", "fast", "sarvam", "sarvam-105b-conversations"),
        ("llm", "zen"): _tier("llm", "zen", "sarvam", "sarvam-105b-conversations"),
        # --- Speech --------------------------------------------------------
        # saaras:v3 rather than saarika:v2.5, and the difference is the whole
        # Indian case rather than a version bump. saarika transcribes *a*
        # language; saaras is one model over 22 Indian languages plus English
        # with automatic detection, trained on code-mixed and noisy telephony
        # audio. An Indian caller switching between English and their own
        # language inside one sentence is the normal case here, not an edge —
        # and on saarika the half of the sentence in the other language is
        # simply lost.
        #
        # It costs $0.53/hour against saarika's Rs30, about 1.7x. That is the
        # price of the tier understanding the caller, which is not an
        # optimisation to trade away.
        ("stt", "default"): _tier("stt", "default", "sarvam", "saaras:v3"),
        # Deepgram Flux: the model decides a turn ended from acoustic and
        # semantic context, so `stt_uses_external_turns` routes the aggregator
        # to external turn strategies and the whole VAD-plus-timeout budget is
        # skipped rather than tuned. Priced at $0.0078/min in default_rates,
        # and it needs a Deepgram platform key -- without one,
        # `managed_resolution` logs and leaves the section alone, so the agent
        # keeps whatever it had rather than failing.
        ("stt", "instant"): _tier("stt", "instant", "deepgram", "flux-general-multi"),
        # bulbul:v3 for the matching reason on the way out: it synthesises
        # code-mixed text with native phonetics, so an English sentence
        # carrying Hindi words is pronounced rather than spelled out. Twice v2
        # per character, and the sub-200ms first byte is a latency gain on top
        # — the two things a caller notices are being understood and not being
        # kept waiting.
        ("tts", "default"): _tier("tts", "default", "sarvam", "bulbul:v3"),
        # --- Speech-to-speech ----------------------------------------------
        # A single model that hears and speaks, replacing the STT and TTS pair.
        # Deliberately *not* Sarvam: no Indic speech-to-speech model is good
        # enough yet, so this tier is a Western model and is the one managed
        # tier that is worse on Telugu than the cascade it replaces. It is
        # offered because latency is the reason anyone picks speech-to-speech,
        # and refusing to offer it at all would just push those customers to
        # bring their own key for the same model.
        #
        # The model string must be one the realtime registry actually offers —
        # ``OPENAI_REALTIME_MODELS`` in registry.py — because service_factory
        # passes it straight through to the vendor. A name that only exists
        # here fails at session open, after the call has already connected.
        # ``default`` stays on OpenAI so a stored configuration naming it keeps
        # the model it was built on rather than being moved to another vendor
        # by an upgrade nobody asked for.
        (REALTIME_COMPONENT, "default"): _tier(
            REALTIME_COMPONENT, "default", "openai_realtime", "gpt-realtime-2"
        ),
        (REALTIME_COMPONENT, "premium"): _tier(
            REALTIME_COMPONENT, "premium", "openai_realtime", "gpt-realtime-2"
        ),
        # Gemini Live. The model string must be one the realtime registry
        # offers, for the same reason the OpenAI one must: service_factory
        # passes it straight to the vendor, and a name that exists only here
        # fails at session open, after the call has connected.
        (REALTIME_COMPONENT, "natural"): _tier(
            REALTIME_COMPONENT,
            "natural",
            "google_realtime",
            "gemini-3.1-flash-live-preview",
        ),
        # --- Embeddings ----------------------------------------------------
        # OpenAI, and it has to be: **there is no Google embeddings service in
        # this codebase.** ``REGISTRY[ServiceType.EMBEDDINGS]`` holds azure,
        # decibyl, openai and openrouter, and ``build_embedding_service``
        # branches on azure and decibyl then *falls through to OpenAI for
        # everything else*. Pointing this tier at Google did not fail loudly —
        # it built an OpenAI client, aimed it at OpenAI's endpoint, and handed
        # it a Google API key. Every managed knowledge-base lookup came back
        # 401 from a vendor the configuration never named.
        #
        # It read plausibly because the credential half was sound: embeddings
        # authenticate on the LLM credential, and the default LLM tier is
        # Google, so one key would have served both. That argument only holds
        # if a Google embeddings client exists. Until one does, the tier must
        # name a provider the factory can actually build.
        #
        # OpenAI costs no extra platform key — the "accurate" LLM tier and the
        # speech-to-speech tier already need one — and text-embedding-3-small
        # is the factory's own default, so the managed path and the fallback
        # path now agree.
        #
        # That claim was true of embeddings and false of speech-to-speech until
        # platform_credentials learned to fall back from ``<vendor>_realtime``
        # to ``<vendor>``. Before that, this tier resolved provider
        # "openai_realtime", matched no stored key, and managed_resolution
        # logged and continued — so the one managed tier sold on latency was
        # the one that did not work.
        (EMBEDDINGS_COMPONENT, "default"): _tier(
            EMBEDDINGS_COMPONENT, "default", "openai", "text-embedding-3-small"
        ),
    }


#: Operator-chosen mappings, loaded from the database and refreshed when one
#: changes. Kept as a module-level cache rather than read per call for one
#: reason: ``resolve`` is called from synchronous code — the voice catalogue,
#: the options screen — and making it async would ripple through six call
#: sites to answer a question whose answer changes about twice a year.
#:
#: Empty until :func:`refresh_overrides` runs, which the app does at startup
#: and again on every worker when a mapping changes (see
#: ``WorkerSyncEventType.MANAGED_TIERS``). Empty is not a failure state: it
#: simply means the compiled defaults apply, which is what a fresh deployment
#: should do.
_OVERRIDES: dict[tuple[str, str], ManagedUpstream] = {}


async def refresh_overrides() -> int:
    """Reload the operator-chosen mappings. Returns how many are in force.

    Replaces the cache wholesale rather than merging, so a mapping deleted in
    the database disappears here too and the tier falls back to its default.
    Merging would leave a retired override applying for ever on whichever
    worker happened to be running when it was set.
    """
    from sqlalchemy import select

    from api.db import db_client
    from api.db.models import ManagedTierMappingModel

    global _OVERRIDES
    try:
        async with db_client.async_session() as session:
            rows = (await session.scalars(select(ManagedTierMappingModel))).all()
    except Exception as exc:  # noqa: BLE001 — a tier map must not fail startup
        # Keep whatever is cached. A database blip must not silently move every
        # managed customer onto a different vendor mid-shift.
        logger.warning("Could not refresh managed tier mappings: {}", exc)
        return len(_OVERRIDES)

    _OVERRIDES = {
        (row.component.strip().lower(), row.tier.strip().lower()): ManagedUpstream(
            row.provider.strip(), row.model.strip()
        )
        for row in rows
    }
    logger.info("Managed tier mappings in force: {}", len(_OVERRIDES))
    return len(_OVERRIDES)


def env_override(component: str, tier: str) -> str | None:
    """The environment variable set for this tier, if any.

    Reported by the operator screen rather than used by it. An environment
    variable that quietly outranked a saved choice would be the rate card's
    "I changed it and nothing happened" all over again, so the table wins and
    this exists to make the other setting visible instead of surprising.
    """
    return os.getenv(f"MANAGED_{component.upper()}_{tier.upper()}") or None


def resolve(component: CostComponent | str, tier: str | None) -> ManagedUpstream:
    """The provider and model serving a managed tier.

    An unknown or missing tier falls back to ``default`` rather than raising: a
    customer whose stored config names a tier we have since retired should keep
    calling on the sensible option, not have their campaign fail at dial time.
    """
    component_value = (
        component.value if isinstance(component, CostComponent) else str(component)
    ).lower()

    mappings = _defaults()
    requested = (tier or "default").lower()
    if component_value == "llm":
        requested = _RETIRED_LLM_TIERS.get(requested, requested)
    key = (component_value, requested)

    # The operator's choice first. See ``_OVERRIDES``.
    override = _OVERRIDES.get(key)
    if override is not None:
        return override

    if key in mappings:
        return mappings[key]

    fallback = mappings.get((component_value, "default"))
    if fallback is None:
        raise KeyError(f"No managed tier mapping for component {component_value!r}")
    return fallback


def upstream_providers() -> set[tuple[str, str]]:
    """Every ``(component, provider)`` the managed offering depends on.

    What the readiness check needs: a tier pointing at a provider we hold no
    platform key for is a managed customer whose calls will fail, and that is
    worth knowing before they dial rather than after.
    """
    resolved = dict(_defaults())
    # An override changes which vendor we need a key for, so the readiness
    # check has to see it. Reading only the defaults would report a platform
    # key as present for a vendor no tier points at any more, and missing for
    # the one that now serves it.
    resolved.update(_OVERRIDES)
    return {(component, up.provider) for (component, _), up in resolved.items()}
