"""The brains a chat can ask for: three presets, and a model by name.

A person typing to a bot in a channel is choosing how hard the bot should
think about this message, the way ChatGPT's picker asks "fast or thorough".
They are not changing the bot: the bot's own stack is what it is, and this
is one message's override. It rides on the message, into the run's session
data, and is read exactly once -- where the text-chat runner resolves the
run's model.

Each preset is a managed LLM tier, so it is priced, resolved to a vendor at
call time, and moves when the tier does. Behind "More models" sits the
catalogue below, by vendor, for the person who does know one model from
another: a choice there is ``model:<vendor>/<model>`` and pins that exact
model on the platform's key, the same direct path the builder's pencil
writes. Only vendors the platform holds a key for are offered.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from api.services.configuration.ai_model_configuration import (
    WORKFLOW_MODEL_CONFIGURATION_V2_OVERRIDE_KEY,
)


@dataclass(frozen=True)
class ChatPreset:
    slug: str
    label: str
    #: Why you would pick it, for somebody who does not know what a model is.
    blurb: str
    llm_tier: str


#: Ordered as offered: cheapest and quickest first. Three on the menu; the
#: strongest models are named under "More models" rather than hidden behind
#: a fourth word nobody could tell from the third.
CHAT_PRESETS: tuple[ChatPreset, ...] = (
    ChatPreset(
        "everyday", "Everyday", "Quick answers. Fine for most messages.", "lite"
    ),
    ChatPreset(
        "smart", "Smart", "A stronger brain for tools and documents.", "default"
    ),
    ChatPreset(
        "deep", "Deep", "For messages where getting it wrong is expensive.", "accurate"
    ),
)

#: Still accepted from a message -- a picker remembered it on somebody's
#: device -- and still the ``advanced`` tier. Not on the menu.
_RETIRED_PRESETS: tuple[ChatPreset, ...] = (
    ChatPreset(
        "advanced",
        "Advanced",
        "The strongest model. Thinks before it answers.",
        "advanced",
    ),
)

PRESETS_BY_SLUG = {preset.slug: preset for preset in CHAT_PRESETS + _RETIRED_PRESETS}


@dataclass(frozen=True)
class CatalogueModel:
    id: str
    label: str


@dataclass(frozen=True)
class Vendor:
    id: str
    label: str
    models: tuple[CatalogueModel, ...]


#: "More models", by vendor. Chat-capable models the platform can drive on
#: its own key. Vendor order is the order shown; models newest-first. A
#: vendor with no active platform LLM key is not offered (see ``menu``).
CATALOGUE: tuple[Vendor, ...] = (
    Vendor(
        "openai",
        "OpenAI",
        (
            CatalogueModel("gpt-5", "GPT-5"),
            CatalogueModel("gpt-5-mini", "GPT-5 mini"),
            CatalogueModel("gpt-4.1", "GPT-4.1"),
            CatalogueModel("gpt-4.1-mini", "GPT-4.1 mini"),
        ),
    ),
    Vendor(
        "anthropic",
        "Anthropic",
        (
            CatalogueModel("claude-opus-5", "Claude Opus 5"),
            CatalogueModel("claude-sonnet-5", "Claude Sonnet 5"),
            CatalogueModel("claude-haiku-4-5", "Claude Haiku 4.5"),
        ),
    ),
    Vendor(
        "google",
        "Google",
        (
            CatalogueModel("gemini-3.5-flash", "Gemini 3.5 Flash"),
            CatalogueModel("gemini-3.5-flash-lite", "Gemini 3.5 Flash Lite"),
            CatalogueModel("gemini-2.5-flash", "Gemini 2.5 Flash"),
        ),
    ),
    Vendor(
        "sarvam",
        "Sarvam",
        (
            CatalogueModel("sarvam-105b", "Sarvam 105B"),
            CatalogueModel("sarvam-105b-conversations", "Sarvam 105B Conversations"),
        ),
    ),
    Vendor(
        "deepseek",
        "DeepSeek",
        (
            CatalogueModel("deepseek-reasoner", "DeepSeek Reasoner"),
            CatalogueModel("deepseek-chat", "DeepSeek Chat"),
        ),
    ),
    Vendor(
        "mistral",
        "Mistral",
        (
            CatalogueModel("mistral-large-latest", "Mistral Large"),
            CatalogueModel("mistral-medium-latest", "Mistral Medium"),
            CatalogueModel("mistral-small-latest", "Mistral Small"),
        ),
    ),
)

VENDORS_BY_ID = {vendor.id: vendor for vendor in CATALOGUE}

#: What a named-model choice starts with: ``model:openai/gpt-5``.
MODEL_PREFIX = "model:"


def named_model(slug: Optional[str]) -> Optional[tuple[str, str]]:
    """``(vendor, model)`` for a ``model:<vendor>/<model>`` slug, else None.

    Only a pair in the catalogue: the slug is typed by a browser, and a
    vendor and model name straight from a request into a pipeline would be
    one more place a string nobody checked decides what runs.
    """
    cleaned = (slug or "").strip()
    if not cleaned.lower().startswith(MODEL_PREFIX):
        return None
    vendor_id, _, model_id = cleaned[len(MODEL_PREFIX) :].partition("/")
    vendor = VENDORS_BY_ID.get(vendor_id.strip().lower())
    if vendor is None:
        return None
    model_id = model_id.strip()
    if not any(model.id == model_id for model in vendor.models):
        return None
    return vendor.id, model_id


def model_label(slug: Optional[str]) -> Optional[str]:
    """What the picker shows for a choice: the preset's word or the model's."""
    chosen = (slug or "").strip().lower()
    if chosen in PRESETS_BY_SLUG:
        return PRESETS_BY_SLUG[chosen].label
    pair = named_model(slug)
    if pair is None:
        return None
    vendor = VENDORS_BY_ID[pair[0]]
    return next(model.label for model in vendor.models if model.id == pair[1])


async def menu(session, *, vendors: Optional[list[str]] = None) -> dict[str, Any]:
    """The picker: the presets, and the catalogue vendors we hold a key for.

    ``vendors`` narrows further -- Decibyl's own thread is driven by the
    builder client, which speaks to three vendors, so its menu lists those.
    """
    from api.services.configuration import platform_credentials

    keyed = set((await platform_credentials.managed_providers(session)).get("llm", []))
    allowed = keyed if vendors is None else keyed & set(vendors)
    return {
        "presets": [
            {"slug": p.slug, "label": p.label, "blurb": p.blurb} for p in CHAT_PRESETS
        ],
        "vendors": [
            {
                "id": vendor.id,
                "label": vendor.label,
                "models": [
                    {
                        "slug": f"{MODEL_PREFIX}{vendor.id}/{model.id}",
                        "label": model.label,
                    }
                    for model in vendor.models
                ],
            }
            for vendor in CATALOGUE
            if vendor.id in allowed
        ],
    }


#: What ``session_data`` carries it under.
SESSION_KEY = "chat_preset"


class UnknownPreset(ValueError):
    pass


def normalise(slug: Optional[str]) -> Optional[str]:
    """The slug as stored, None for "not chosen", or UnknownPreset.

    A preset's word, or ``model:<vendor>/<model>`` for something in the
    catalogue. Anything else is refused rather than quietly run as the
    bot's own brain: a picker that sent a name we do not know is a bug, and
    a message answered by the wrong brain would hide it.
    """
    cleaned = (slug or "").strip().lower()
    if not cleaned:
        return None
    if cleaned in PRESETS_BY_SLUG:
        return cleaned
    pair = named_model(cleaned)
    if pair is not None:
        return f"{MODEL_PREFIX}{pair[0]}/{pair[1]}"
    raise UnknownPreset(f"No chat preset called {slug!r}")


def apply(run_configs: dict[str, Any] | None, slug: Optional[str]) -> dict[str, Any]:
    """The run's workflow configurations with this preset's brain in the LLM slot.

    Only the LLM slot moves. A bot with a hand-built v3 stack keeps every
    other slot as it was; a bot on the organisation default gets a managed
    stack with the preset's brain, the same shape the agent presets write.
    A run that chose nothing is returned untouched -- the same dict, so a
    caller can tell nothing happened.
    """
    chosen = normalise(slug)
    configs = dict(run_configs or {})
    if chosen is None:
        return configs

    from api.services.configuration.agent_options import managed_stack_override

    pair = named_model(chosen)
    if pair is not None:
        # The direct managed shape: this vendor, this model, our key. The
        # same thing the builder's pencil writes when somebody picks a model
        # by name, so managed_resolution needs nothing new to run it.
        brain: dict[str, Any] = {
            "provider": pair[0],
            "model": pair[1],
            "api_key": "",
            "use_platform_key": True,
        }
        tier = "default"
    else:
        tier = PRESETS_BY_SLUG[chosen].llm_tier
        brain = {"provider": "decibyl", "model": tier, "api_key": ""}

    existing = configs.get(WORKFLOW_MODEL_CONFIGURATION_V2_OVERRIDE_KEY)
    stack = existing.get("stack") if isinstance(existing, dict) else None
    if isinstance(stack, dict) and existing.get("version") == 3:
        # Only the brain. The transcriber and the voice are somebody's
        # choices for the phone, and a chat never touches them.
        new_stack = dict(stack)
        new_stack["llm"] = brain
        configs[WORKFLOW_MODEL_CONFIGURATION_V2_OVERRIDE_KEY] = {
            **existing,
            "stack": new_stack,
        }
        return configs

    configs.update(managed_stack_override(voice="", llm_tier=tier))
    if pair is not None:
        configs[WORKFLOW_MODEL_CONFIGURATION_V2_OVERRIDE_KEY]["stack"]["llm"] = brain
    return configs
