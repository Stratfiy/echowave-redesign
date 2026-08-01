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
LLM_TIERS = ("default", "fast", "lite", "accurate", "zen")
STT_TIERS = ("default",)
TTS_TIERS = ("default",)


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
        ("llm", "default"): _tier("llm", "default", "google", "gemini-2.5-flash"),
        ("llm", "fast"): _tier("llm", "fast", "google", "gemini-2.5-flash-lite"),
        ("llm", "lite"): _tier("llm", "lite", "google", "gemini-2.5-flash-lite"),
        ("llm", "accurate"): _tier("llm", "accurate", "openai", "gpt-4o"),
        # "zen" is the quiet tier — cheapest that still holds a conversation.
        ("llm", "zen"): _tier("llm", "zen", "google", "gemini-2.5-flash-lite"),
        # --- Speech --------------------------------------------------------
        ("stt", "default"): _tier("stt", "default", "sarvam", "saarika:v2"),
        ("tts", "default"): _tier("tts", "default", "sarvam", "bulbul:v2"),
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
