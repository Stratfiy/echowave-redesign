"""Four brains for a chat, named for what they are for.

A person typing to a bot in a channel is choosing how hard the bot should
think about this message, the way ChatGPT's picker asks "fast or thorough".
They are not choosing a vendor, and they are not changing the bot: the bot's
own stack is what it is, and this is one message's override. It rides on the
message, into the run's session data, and is read exactly once -- where the
text-chat runner resolves the run's model.

Each preset is a managed LLM tier, so it is priced, resolved to a vendor at
call time, and moves when the tier does. Nothing here names a model.
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


#: Ordered as offered: cheapest and quickest first.
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
    ChatPreset(
        "advanced",
        "Advanced",
        "The strongest model. Thinks before it answers.",
        "advanced",
    ),
)

PRESETS_BY_SLUG = {preset.slug: preset for preset in CHAT_PRESETS}

#: What ``session_data`` carries it under.
SESSION_KEY = "chat_preset"


class UnknownPreset(ValueError):
    pass


def normalise(slug: Optional[str]) -> Optional[str]:
    """The slug as stored, None for "not chosen", or UnknownPreset."""
    cleaned = (slug or "").strip().lower()
    if not cleaned:
        return None
    if cleaned not in PRESETS_BY_SLUG:
        raise UnknownPreset(f"No chat preset called {slug!r}")
    return cleaned


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

    tier = PRESETS_BY_SLUG[chosen].llm_tier
    existing = configs.get(WORKFLOW_MODEL_CONFIGURATION_V2_OVERRIDE_KEY)
    stack = existing.get("stack") if isinstance(existing, dict) else None
    if isinstance(stack, dict) and existing.get("version") == 3:
        # Only the brain. The transcriber and the voice are somebody's
        # choices for the phone, and a chat never touches them.
        new_stack = dict(stack)
        new_stack["llm"] = {"provider": "decibyl", "model": tier, "api_key": ""}
        configs[WORKFLOW_MODEL_CONFIGURATION_V2_OVERRIDE_KEY] = {
            **existing,
            "stack": new_stack,
        }
        return configs

    configs.update(managed_stack_override(voice="", llm_tier=tier))
    return configs
