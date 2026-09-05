"""Four named stacks, so choosing one is a click rather than three questions.

Vapi opens its assistant screen with Balanced / High Intelligence / Ultra Fast
/ Cost Saver above the model cards, and a *Customized* state once you deviate.
The idea is right and the vocabulary is right: somebody choosing how their
phone agent should behave is answering "cheap, clever, or quick", not picking a
transcriber.

Two things this borrows from ``LATENCY_PRESETS``, which solved the same problem
for turn timings and solved it well:

**Derived, never stored.** The active preset is matched from the stack itself,
so there is no field to migrate, nothing to keep in sync, and an agent tuned by
hand keeps its models and simply reads as custom. Picking a preset is the only
thing that overwrites anything.

**A preset is a product choice, not a vendor.** Each one names managed tiers,
and ``managed_stack_override`` writes them as tiers, so moving what ``accurate``
resolves to moves every agent on High Intelligence without touching a single
stored configuration. Naming vendors here would turn a tier change into a
migration.

The set is deliberately small and covers one axis each -- price, capability,
latency -- with Balanced as the thing to pick when you have no opinion. A fifth
preset that is "Balanced but slightly different" is how a picker stops helping.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ModelPreset:
    slug: str
    label: str
    #: What the choice does, in the words of somebody who does not know what a
    #: model is. Never the vendor -- the cards below already name those.
    blurb: str
    #: Empty for the cascade. A realtime tier replaces the transcriber and the
    #: voice rather than joining them, which is why the two are never both set.
    realtime_tier: str = ""
    llm_tier: str = ""


#: Ordered as they are offered: cheapest first, then the default, then the two
#: reasons to pay more. Balanced is second rather than first because a picker
#: that opens on its cheapest option reads as an upsell ladder.
MODEL_PRESETS: tuple[ModelPreset, ...] = (
    ModelPreset(
        slug="cost_saver",
        label="Cost Saver",
        blurb="Cheapest a minute, and the strongest of these on Indian languages.",
        llm_tier="lite",
    ),
    ModelPreset(
        slug="balanced",
        label="Balanced",
        blurb="Handles a real conversation at a sensible price. Start here.",
        llm_tier="default",
    ),
    ModelPreset(
        slug="high_intelligence",
        label="High Intelligence",
        blurb="For calls where getting it wrong is expensive.",
        llm_tier="accurate",
    ),
    ModelPreset(
        slug="ultra_fast",
        label="Ultra Fast",
        blurb="Replies the instant the caller stops. One model hears and speaks.",
        realtime_tier="natural",
    ),
)

PRESETS_BY_SLUG = {preset.slug: preset for preset in MODEL_PRESETS}

#: What a stack matches when it matches nothing. Named rather than ``None`` so
#: callers do not each invent a spelling for it.
CUSTOM = "custom"


def _tier_of(section) -> str:
    """The tier a managed section names, or "" when it is not managed.

    A managed slot carries ``provider="decibyl"`` and a *tier* in ``model`` --
    see ``managed_stack_override``. A slot naming a real vendor is a hand-built
    stack, which no preset describes.
    """
    if section is None:
        return ""
    provider = getattr(section, "provider", None)
    provider = provider.value if hasattr(provider, "value") else str(provider or "")
    if provider != "decibyl":
        return ""
    return str(getattr(section, "model", "") or "")


def match(effective) -> str:
    """Which preset this stack is, or ``CUSTOM``.

    Matched on the tiers the stack names rather than on the vendors they
    currently resolve to. Comparing resolved vendors would mean an agent
    silently stopped matching its own preset the day we moved a tier -- the
    exact coupling tiers exist to prevent.
    """
    if getattr(effective, "is_realtime", False):
        tier = _tier_of(getattr(effective, "realtime", None))
        for preset in MODEL_PRESETS:
            if preset.realtime_tier and preset.realtime_tier == tier:
                return preset.slug
        return CUSTOM

    llm_tier = _tier_of(getattr(effective, "llm", None))
    if not llm_tier:
        return CUSTOM
    # A cascade preset only claims the brain. Speech is one tier each today, so
    # pinning them here would make every preset stop matching the moment a
    # second voice tier ships -- and the customer did not choose a voice by
    # picking "Balanced".
    for preset in MODEL_PRESETS:
        if not preset.realtime_tier and preset.llm_tier == llm_tier:
            return preset.slug
    return CUSTOM


def is_available(preset: ModelPreset, keyed: dict[str, dict[str, bool]]) -> bool:
    """Can we serve this preset right now?

    ``keyed`` is ``managed_resolution.tier_availability``. A preset resolving to
    a vendor we hold no key for is a call that fails after it connects, so the
    picker shows it disabled rather than selling it -- the same rule the bundle
    cards on the create screen follow.
    """
    if preset.realtime_tier:
        return keyed.get("realtime", {}).get(preset.realtime_tier, False)
    # The cascade needs ears, a brain and a voice; one missing key is one
    # silent failure.
    return (
        keyed.get("llm", {}).get(preset.llm_tier, False)
        and keyed.get("stt", {}).get("default", False)
        and keyed.get("tts", {}).get("default", False)
    )
