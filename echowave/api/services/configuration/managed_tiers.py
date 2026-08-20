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
        # Off Google entirely. Flash-Lite 2.5 retired and its successor came
        # back 3.3x dearer at a price nobody had confirmed, which is a poor
        # foundation for a tier whose whole promise is a stable price.
        #
        # Lite is Sarvam, and it is the interesting one: eight times cheaper
        # per token than the Gemini it replaces, trained on Indian languages,
        # and the same vendor already serving managed transcription and voice —
        # so the cheap tier is one relationship and one key rather than three.
        ("llm", "lite"): _tier("llm", "lite", "sarvam", "sarvam-105b"),
        ("llm", "default"): _tier("llm", "default", "openai", "gpt-4.1-mini"),
        ("llm", "accurate"): _tier("llm", "accurate", "openai", "gpt-4.1"),
        # Retired names, kept resolving to the model they always served.
        ("llm", "fast"): _tier("llm", "fast", "sarvam", "sarvam-105b"),
        ("llm", "zen"): _tier("llm", "zen", "sarvam", "sarvam-105b"),
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
