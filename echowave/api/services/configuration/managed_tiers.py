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

from api.enums import CostComponent

#: Every tier name ``resolve()`` understands, including the two no longer
#: offered. Not the same list as the picker — see ``OFFERED_LLM_TIERS``.
LLM_TIERS = ("default", "fast", "lite", "accurate", "zen")

#: The tiers a customer can actually choose, and the order they read in.
#:
#: Three, because there are three genuinely different models behind them.
#: ``lite`` and ``zen`` both resolved to the same model as ``fast``, so the
#: picker was offering five names for three choices — which is worse than
#: offering three, because a customer who picks "lite" over "fast" believes
#: they have made a decision and has not.
#:
#: Both retired names stay in ``LLM_TIERS`` and stay mapped. A configuration
#: saved when they were offered must keep resolving: dropping them from the
#: resolver would fall those agents back to ``default``, which is a different
#: model at more than twice the price, silently and mid-campaign.
OFFERED_LLM_TIERS = ("fast", "default", "accurate")

#: Names kept working but no longer shown.
RETIRED_LLM_TIERS = ("lite", "zen")
STT_TIERS = ("default",)
TTS_TIERS = ("default",)
EMBEDDINGS_TIERS = ("default",)
REALTIME_TIERS = ("default",)

#: Embeddings are not a billing component — they are consumed at knowledge-base
#: ingest rather than per call, so there is no ``CostComponent.EMBEDDINGS`` and
#: no per-call line item for them. They still need a real provider and a real
#: key, which is why they are mapped here alongside the rest: a managed
#: configuration emits an embeddings section too, and until this existed that
#: section named a provider nothing could resolve.
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
        # Three offered tiers, three genuinely different models. Blended cost
        # per 1M tokens at LLM_INPUT_SHARE=0.7: $0.19, $0.48, $4.75 — a real
        # ladder rather than three names for one model.
        #
        # gemini-2.5-flash-lite retires 2026-10-16 and its replacement is 2.5x
        # dearer, so `fast` gets materially more expensive on that date unless
        # it is repointed. Recorded on the rate row as well as here.
        ("llm", "fast"): _tier("llm", "fast", "google", "gemini-2.5-flash-lite"),
        ("llm", "default"): _tier("llm", "default", "google", "gemini-2.5-flash"),
        ("llm", "accurate"): _tier("llm", "accurate", "openai", "gpt-4o"),
        # Retired from the picker, still resolved. An agent saved against
        # either of these keeps running on exactly the model it always ran on
        # — falling them back to `default` would double the price of a
        # campaign already in flight.
        ("llm", "lite"): _tier("llm", "lite", "google", "gemini-2.5-flash-lite"),
        ("llm", "zen"): _tier("llm", "zen", "google", "gemini-2.5-flash-lite"),
        # --- Speech --------------------------------------------------------
        # saarika:v2.5, not v2. Sarvam's own configuration class defaults to
        # v2.5 and offers only v2.5 and saaras:v3 — "saarika:v2" is a name from
        # a generation the vendor no longer serves, and it appeared nowhere
        # else in this repository. A managed customer transcribes every call on
        # this string, so a stale one is not a fallback, it is silence.
        ("stt", "default"): _tier("stt", "default", "sarvam", "saarika:v2.5"),
        ("tts", "default"): _tier("tts", "default", "sarvam", "bulbul:v2"),
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
        (REALTIME_COMPONENT, "default"): _tier(
            REALTIME_COMPONENT, "default", "openai_realtime", "gpt-realtime-2"
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
        (EMBEDDINGS_COMPONENT, "default"): _tier(
            EMBEDDINGS_COMPONENT, "default", "openai", "text-embedding-3-small"
        ),
    }


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
    key = (component_value, (tier or "default").lower())
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
    return {(component, up.provider) for (component, _), up in _defaults().items()}
