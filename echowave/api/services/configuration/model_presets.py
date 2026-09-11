"""Four named stacks, so choosing one is a click rather than three questions.

Vapi opens its assistant screen with a row of presets above the model cards
and a *Customized* state once you deviate. The idea is right: somebody
choosing how their phone agent should behave is answering "cheap, every
language, clever, or international", not picking a transcriber.

These replaced the bundles. Bundles and presets were the same product idea
built twice -- one click, one price, one stack -- with two pickers, two price
tables and a tier system underneath that only ever had one voice tier. One
mechanism now: a preset is a stack of managed tiers, it has a price a minute,
and the pencil on each tile is the way off it.

Two things this borrows from ``LATENCY_PRESETS``, which solved the same
problem for turn timings and solved it well:

**Derived, never stored.** The active preset is matched from the stack
itself, so there is no field to migrate, nothing to keep in sync, and an agent
tuned by hand keeps its models and simply reads as custom. Picking a preset is
the only thing that overwrites anything.

**A preset is a product choice, not a vendor.** Each one names managed tiers,
and ``managed_stack_override`` writes them as tiers, so moving what ``basic``
resolves to moves every agent on Basic without touching a single stored
configuration. Naming vendors here would turn a tier change into a migration.

The ladder is four rungs, cheapest first, and each rung is a *reason to pay
more* rather than a quality grade: more languages, a stronger brain, an
international voice. That is also what lets :func:`recommend` pick one from
what an agent has to do -- the cheapest rung that meets the requirement.
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
    stt_tier: str = "default"
    tts_tier: str = "default"
    llm_tier: str = ""
    #: Empty for the cascade. A realtime tier replaces the transcriber and the
    #: voice rather than joining them, which is why the two are never both set.
    realtime_tier: str = ""


#: Ordered as they are offered and as the recommender climbs them: cheapest
#: first, each rung buying one more thing.
MODEL_PRESETS: tuple[ModelPreset, ...] = (
    ModelPreset(
        slug="basic",
        label="Basic",
        blurb="Hindi and English, the cheapest a minute. For scripted calls.",
        tts_tier="basic",
        llm_tier="lite",
    ),
    ModelPreset(
        slug="standard",
        label="Standard",
        blurb="Every Indian language, still cheap. Start here.",
        tts_tier="default",
        llm_tier="lite",
    ),
    ModelPreset(
        slug="smart",
        label="Smart",
        blurb="A stronger brain for calls that use tools, documents, or go off script.",
        tts_tier="default",
        llm_tier="accurate",
    ),
    ModelPreset(
        slug="global",
        label="Global",
        blurb="The international voice, for English-first callers.",
        tts_tier="global",
        llm_tier="default",
    ),
)

PRESETS_BY_SLUG = {preset.slug: preset for preset in MODEL_PRESETS}

#: What a stack matches when it matches nothing. Named rather than ``None`` so
#: callers do not each invent a spelling for it.
CUSTOM = "custom"


def _tier_of(component: str, section) -> str:
    """The tier a managed section names, or "" when it is not managed.

    A managed slot carries ``provider="decibyl"`` and a *tier* in ``model`` --
    see ``managed_stack_override``. A slot naming a real vendor is a hand-built
    stack, which no preset describes.

    Folded through ``managed_tiers.canonical_tier`` so a retired name matches
    the preset it still runs: ``zen`` resolves to the ``lite`` stack at dial
    time, so an agent stored on it *is* Standard and reading it as custom
    would be the screen disagreeing with the call.
    """
    from api.services.configuration import managed_tiers
    from api.services.configuration.registry import ServiceProviders

    if section is None:
        return ""
    provider = getattr(section, "provider", None)
    provider = provider.value if hasattr(provider, "value") else str(provider or "")
    if provider.strip().lower() != ServiceProviders.DECIBYL.value:
        return ""
    tier = str(getattr(section, "model", "") or "").strip()
    if not tier:
        return ""
    return managed_tiers.canonical_tier(component, tier)


def match(effective) -> str:
    """Which preset this stack is, or ``CUSTOM``.

    Matched on the tiers the stack names rather than on the vendors they
    currently resolve to. Comparing resolved vendors would mean an agent
    silently stopped matching its own preset the day we moved a tier -- the
    exact coupling tiers exist to prevent.

    All three cascade slots have to agree. A preset names a voice tier now,
    so an agent whose voice has been changed by hand is not on the preset any
    more, whatever its brain says; reading it as one would let a click on the
    same chip quietly put the voice back.
    """
    if getattr(effective, "is_realtime", False):
        tier = _tier_of("realtime", getattr(effective, "realtime", None))
        for preset in MODEL_PRESETS:
            if preset.realtime_tier and preset.realtime_tier == tier:
                return preset.slug
        return CUSTOM

    llm_tier = _tier_of("llm", getattr(effective, "llm", None))
    stt_tier = _tier_of("stt", getattr(effective, "stt", None))
    tts_tier = _tier_of("tts", getattr(effective, "tts", None))
    if not (llm_tier and stt_tier and tts_tier):
        return CUSTOM
    for preset in MODEL_PRESETS:
        if preset.realtime_tier:
            continue
        if (
            preset.llm_tier == llm_tier
            and preset.stt_tier == stt_tier
            and preset.tts_tier == tts_tier
        ):
            return preset.slug
    return CUSTOM


def is_available(preset: ModelPreset, keyed: dict[str, dict[str, bool]]) -> bool:
    """Can we serve this preset right now?

    ``keyed`` is ``managed_resolution.tier_availability``. A preset resolving to
    a vendor we hold no key for is a call that fails after it connects, so the
    picker shows it disabled rather than selling it.
    """
    if preset.realtime_tier:
        return keyed.get("realtime", {}).get(preset.realtime_tier, False)
    # The cascade needs ears, a brain and a voice; one missing key is one
    # silent failure.
    return (
        keyed.get("llm", {}).get(preset.llm_tier, False)
        and keyed.get("stt", {}).get(preset.stt_tier, False)
        and keyed.get("tts", {}).get(preset.tts_tier, False)
    )


# ---------------------------------------------------------------------------
# Recommending one
# ---------------------------------------------------------------------------

#: The languages the Basic voice speaks. Anything else needs the Sarvam voice.
_BASIC_LANGUAGES = frozenset({"hi", "en"})

#: What the Indian speech stack covers. A language outside this set is an
#: international caller, which is what the Global voice is for.
_INDIAN_LANGUAGES = frozenset(
    {"hi", "en", "ta", "te", "kn", "ml", "mr", "bn", "gu", "pa", "or", "ur", "as"}
)

#: The wizard asks for languages by name; the stored field carries subtags.
#: Both are accepted here so the same recommender serves the create screen
#: and the agent screen.
_LANGUAGE_CODES = {
    "english": "en",
    "hindi": "hi",
    "tamil": "ta",
    "telugu": "te",
    "kannada": "kn",
    "malayalam": "ml",
    "marathi": "mr",
    "bengali": "bn",
    "gujarati": "gu",
    "punjabi": "pa",
    "odia": "or",
    "urdu": "ur",
    "assamese": "as",
}


def language_code(value: str) -> str:
    """``"Hindi"``, ``"hi"`` and ``"hi-IN"`` all read as ``hi``."""
    raw = (value or "").strip().lower()
    if not raw:
        return ""
    if raw in _LANGUAGE_CODES:
        return _LANGUAGE_CODES[raw]
    return raw.split("-")[0].split("_")[0]


@dataclass(frozen=True)
class Requirement:
    """What an agent has to do, as far as the preset choice is concerned."""

    #: Languages the agent serves, by name or subtag. Empty means unknown,
    #: which is read as "Indian, more than Hindi and English".
    languages: tuple[str, ...] = ()
    #: Does it call tools or read documents? A tool call is where the cheap
    #: brain is weakest: it has to produce exact arguments, not a sentence.
    uses_tools: bool = False


@dataclass(frozen=True)
class Recommendation:
    slug: str
    #: One sentence saying why, in the customer's words, for the badge.
    reason: str


def recommend(
    requirement: Requirement,
    *,
    available: dict[str, bool] | None = None,
) -> Recommendation:
    """The cheapest preset that does the job.

    Climbs the ladder from the bottom: Basic if the languages are Hindi and
    English and there are no tools; Standard for any other Indian language;
    Smart when there are tools or documents to get right; Global when a
    language is not an Indian one at all. "Automatic" here is a rule, not a
    model -- a rule can be read, argued with and tested, and a recommendation
    a customer cannot predict is one they will not trust.

    ``available`` maps slug to whether we can serve it; a rung we cannot
    serve is skipped upward, and if nothing above serves either, the first
    available rung wins, so the chip row always has something to light.
    """
    codes = {language_code(v) for v in requirement.languages} - {""}
    foreign = codes - _INDIAN_LANGUAGES

    if foreign:
        chosen, reason = (
            "global",
            "A language outside India's; the international voice suits it.",
        )
    elif requirement.uses_tools:
        chosen, reason = (
            "smart",
            "It calls tools or reads documents, which the cheaper brains get wrong.",
        )
    elif codes and codes <= _BASIC_LANGUAGES:
        chosen, reason = (
            "basic",
            "Hindi and English only, so the cheapest voice covers it.",
        )
    else:
        chosen, reason = (
            "standard",
            "Speaks every Indian language at the lowest price that does.",
        )

    if available is None:
        return Recommendation(chosen, reason)

    order = [p.slug for p in MODEL_PRESETS]
    start = order.index(chosen)
    for slug in order[start:] + order[:start]:
        if available.get(slug, False):
            if slug != chosen:
                reason = f"{PRESETS_BY_SLUG[chosen].label} is not available on this account yet."
            return Recommendation(slug, reason)
    return Recommendation(chosen, reason)
