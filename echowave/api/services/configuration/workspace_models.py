"""Settings -> Models: one place a workspace chooses what runs every agent.

Four slots -- the brain, hearing, the voice engine and knowledge search. For
each, the workspace picks something we provide (a tier, or for the brain an
exact model), or brings its own key for a vendor we do *not* provide. Vendors
we already serve are left off the own-key list on purpose: there is nothing to
gain by paying for a key to a model you get here for free, and a shorter list
is the whole point of this screen.

Stored on the managed configuration (``DecibylManagedAIModelConfiguration``
``slots`` and tiers), so everything downstream -- resolution, billing, the
vault lookup for own keys -- already knows how to run it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from api.schemas.ai_model_configuration import (
    OWN_KEY_PREFIX,
    DecibylManagedAIModelConfiguration,
    OrganizationAIModelConfigurationV2,
    parse_slot_choice,
)
from api.services.configuration import chat_presets, managed_tiers
from api.services.configuration.registry import REGISTRY, ServiceType


@dataclass(frozen=True)
class Slot:
    key: str
    label: str
    blurb: str
    service_type: ServiceType
    #: The field on the managed configuration holding this slot's tier.
    tier_field: str | None


SLOTS: tuple[Slot, ...] = (
    Slot(
        "llm",
        "Brain",
        "Thinks and replies, in chat and on calls.",
        ServiceType.LLM,
        "llm_tier",
    ),
    Slot(
        "stt",
        "Hearing",
        "Turns a caller's speech into text.",
        ServiceType.STT,
        "stt_tier",
    ),
    Slot(
        "tts",
        "Voice engine",
        "For agents that have not picked a voice of their own.",
        ServiceType.TTS,
        "tts_tier",
    ),
    Slot(
        "embeddings",
        "Knowledge search",
        "Finds the right passage in your documents.",
        ServiceType.EMBEDDINGS,
        None,
    ),
)
SLOTS_BY_KEY = {slot.key: slot for slot in SLOTS}

TIER_PREFIX = "tier:"
MODEL_PREFIX = "model:"

_TIER_LABELS = {
    "llm": managed_tiers.LLM_TIER_LABELS,
    "stt": managed_tiers.STT_TIER_LABELS,
    "tts": managed_tiers.TTS_TIER_LABELS,
    "embeddings": {
        managed_tiers.BEDROCK_EMBEDDINGS_TIER: managed_tiers.EMBEDDINGS_TIER_LABELS[
            managed_tiers.BEDROCK_EMBEDDINGS_TIER
        ]
    },
}

#: What a workspace reads when one of our choices is listed but not ready.
#: The operator's own reason (an environment variable, an AWS console step)
#: is on the staff side; a customer is told what it means for them.
NEEDS_SETUP_NOTE = "Being set up by Decibyl. Not available to choose yet."

#: What a vendor is called on screen. Anything missing reads as its id.
_VENDOR_LABELS = {
    "openai": "OpenAI",
    "anthropic": "Anthropic",
    "google": "Google",
    "groq": "Groq",
    "deepseek": "DeepSeek",
    "mistral": "Mistral",
    "cerebras": "Cerebras",
    "fireworks": "Fireworks",
    "openrouter": "OpenRouter",
    "minimax": "MiniMax",
    "sarvam": "Sarvam",
    "deepgram": "Deepgram",
    "cartesia": "Cartesia",
    "speechmatics": "Speechmatics",
    "assemblyai": "AssemblyAI",
    "gladia": "Gladia",
    "elevenlabs": "ElevenLabs",
    "smallest": "Smallest",
    "inworld": "Inworld",
    "rumik": "Rumik",
    "camb": "CAMB.AI",
    "rime": "Rime",
    "xai": "xAI",
    "anthropic_aws": "Claude on AWS",
    "aws_bedrock": "Amazon Bedrock",
}

#: Fields a provider needs beyond a key and a model name. A vendor needing any
#: other required field (a region, an endpoint, a project) is not offered here:
#: one key box is the promise of this screen.
_SIMPLE_FIELDS = {"provider", "model", "api_key"}

#: Vendors whose fields default but are not really optional: self-hosted
#: servers (an endpoint you run) and cloud accounts (AWS or Azure
#: credentials and a region). They stay reachable through the full stack
#: editor; listed here so the key box is never a promise that cannot work.
_NEEDS_MORE_THAN_A_KEY = {
    "aws_bedrock",
    "azure",
    "azure_speech",
    "custom_llm",
    "google_vertex",
    "huggingface",
    "speaches",
}


def vendor_label(vendor: str) -> str:
    return _VENDOR_LABELS.get(vendor, vendor)


def _model_label(vendor: str, model: str) -> str:
    for catalogue_vendor in chat_presets.CATALOGUE:
        if catalogue_vendor.id == vendor:
            for entry in catalogue_vendor.models:
                if entry.id == model:
                    return entry.label
    return model


def _models_for(cls) -> list[str]:
    field = cls.model_fields.get("model")
    if field is None:
        return []
    extra = field.json_schema_extra if isinstance(field.json_schema_extra, dict) else {}
    models = [str(m) for m in (extra.get("examples") or ())]
    if isinstance(field.default, str) and field.default and field.default not in models:
        models.insert(0, field.default)
    return models


def _simple(cls) -> bool:
    required = {name for name, f in cls.model_fields.items() if f.is_required()}
    return required <= _SIMPLE_FIELDS


def _provided(slot: Slot, platform_providers: dict[str, list[str]]) -> set[str]:
    """Vendors we already serve for this slot: every vendor a tier resolves
    to, and every vendor we hold a platform key for."""
    provided = set(platform_providers.get(slot.key, []))
    for tier in managed_tiers.tiers_for(slot.key):
        provided.add(managed_tiers.resolve(slot.key, tier).provider)
    return provided


def _tier_status(slot: str, tier: str) -> dict[str, str]:
    """The honest state of one of our tiers, for the ones that can be listed
    before they are ready (the AWS gateway's). Everything else is available:
    its readiness is the platform-key check the screen already relies on."""
    from api.services.aws_gateway import config as aws_config

    status = None
    if slot == "embeddings" and tier == managed_tiers.BEDROCK_EMBEDDINGS_TIER:
        status = aws_config.embeddings_status()
    if status is None or status.available:
        return {"status": aws_config.AVAILABLE}
    return {"status": status.state, "status_note": NEEDS_SETUP_NOTE}


def _serves(slot: str, tier: str, upstream: managed_tiers.ManagedUpstream) -> str:
    """Who serves a tier, as the screen says it -- including when Claude runs
    through AWS (``CLAUDE_BACKEND``), but only once that backend is ready for
    the model; until then it is still Anthropic serving it."""
    if slot == "llm" and tier == managed_tiers.AUTO_LLM_TIER:
        return "Claude Haiku, Sonnet or Opus, chosen per task"
    vendor = upstream.provider
    if slot == "llm" and vendor == "anthropic":
        from api.services.aws_gateway import claude as aws_claude

        marker = aws_claude.platform_credential(upstream.model)
        if marker:
            vendor = aws_claude.usage_provider(marker)
    return f"{vendor_label(vendor)} · {_model_label(upstream.provider, upstream.model)}"


def _current(slot: Slot, managed: DecibylManagedAIModelConfiguration) -> str:
    chosen = (managed.slots or {}).get(slot.key, "")
    parsed = parse_slot_choice(chosen)
    if parsed is not None and parsed[0] == "decibyl" and not parsed[2]:
        # One of our tiers on a slot with no tier field of its own
        # (embeddings), stored as ``decibyl/<tier>``.
        return f"{TIER_PREFIX}{parsed[1]}"
    if parsed is not None:
        vendor, model, own = parsed
        return (
            f"{OWN_KEY_PREFIX}{vendor}/{model}"
            if own
            else f"{MODEL_PREFIX}{vendor}/{model}"
        )
    tier = getattr(managed, slot.tier_field) if slot.tier_field else "default"
    return f"{TIER_PREFIX}{tier or 'default'}"


def locked_reason(stored: OrganizationAIModelConfigurationV2 | None) -> str | None:
    """Why this screen must not save over the stored stack, if it must not.

    Two shapes cannot round-trip through four slots without losing something:
    an own-keys stack (custom voices, languages, endpoints and Azure-style
    vendors would be reduced to vendor/model) and a speech-to-speech bundle
    (one model replaces hearing, brain and voice, so the slots would not
    run). Saving either from here would quietly rebuild the workspace, so the
    screen shows what runs and leaves editing to the full stack editor.
    """
    if stored is None:
        return None
    if stored.mode == "byok":
        return (
            "This workspace runs a custom stack on its own keys, "
            "set up in the full model editor."
        )
    if stored.decibyl is not None and (stored.decibyl.realtime_tier or "").strip():
        return (
            "This workspace uses speech-to-speech, where one model hears, "
            "thinks and speaks."
        )
    return None


def view(
    stored: OrganizationAIModelConfigurationV2 | None,
    *,
    platform_providers: dict[str, list[str]],
    keys_held: dict[str, list[str]],
) -> dict[str, Any]:
    """Everything the Models screen draws, in one answer."""
    managed = managed_configuration(stored)
    slots = []
    for slot in SLOTS:
        ours: list[dict[str, Any]] = []
        labels = _TIER_LABELS.get(slot.key, {})
        for tier in managed_tiers.tiers_for(slot.key):
            upstream = managed_tiers.resolve(slot.key, tier)
            label, blurb = labels.get(tier, ("Standard", ""))
            ours.append(
                {
                    "value": f"{TIER_PREFIX}{tier}",
                    "label": label,
                    "blurb": blurb,
                    "serves": _serves(slot.key, tier, upstream),
                    **_tier_status(slot.key, tier),
                }
            )
        more: list[dict[str, Any]] = []
        if slot.key == "llm":
            keyed = set(platform_providers.get("llm", []))
            for vendor in chat_presets.CATALOGUE:
                if vendor.id not in keyed:
                    continue
                for model in vendor.models:
                    more.append(
                        {
                            "value": f"{MODEL_PREFIX}{vendor.id}/{model.id}",
                            "label": model.label,
                            "vendor": vendor.label,
                        }
                    )
        provided = _provided(slot, platform_providers)
        held = set(keys_held.get(_credential_component(slot.key), []))
        own = []
        for provider, cls in REGISTRY[slot.service_type].items():
            vendor = getattr(provider, "value", provider)
            if (
                vendor == "decibyl"
                or vendor in provided
                or vendor in _NEEDS_MORE_THAN_A_KEY
                or not _simple(cls)
            ):
                continue
            models = _models_for(cls)
            if not models:
                continue
            own.append(
                {
                    "vendor": vendor,
                    "label": vendor_label(vendor),
                    "models": models,
                    "has_key": vendor in held,
                }
            )
        own.sort(key=lambda o: o["label"].lower())
        current = _current(slot, managed)
        slots.append(
            {
                "key": slot.key,
                "label": slot.label,
                "blurb": slot.blurb,
                "current": current,
                "current_label": _label_for(current, ours, more),
                "ours": ours,
                "more": more,
                "own": own,
            }
        )
    return {
        "slots": slots,
        "credential_component": {s.key: _credential_component(s.key) for s in SLOTS},
        "locked": locked_reason(stored),
    }


def _label_for(value: str, ours: list[dict], more: list[dict]) -> str:
    for option in ours + more:
        if option["value"] == value:
            return option["label"]
    parsed = parse_slot_choice(value.removeprefix(MODEL_PREFIX))
    if parsed is not None:
        vendor, model, own = parsed
        suffix = " (your key)" if own else ""
        return f"{vendor_label(vendor)} · {model}{suffix}"
    return value


def _credential_component(slot: str) -> str:
    """Which credential row a slot authenticates with. Embeddings use the LLM
    vendor's key, as they do everywhere else."""
    return "llm" if slot == "embeddings" else slot


def managed_configuration(
    stored: OrganizationAIModelConfigurationV2 | None,
) -> DecibylManagedAIModelConfiguration:
    """The managed configuration this screen edits.

    An account still on the old all-own-keys mode is read as the same stack
    expressed slot by slot, so the screen shows what actually runs and a save
    keeps it rather than quietly moving the account onto our key.
    """
    if stored is not None and stored.mode == "decibyl" and stored.decibyl is not None:
        return stored.decibyl.model_copy(deep=True)
    managed = DecibylManagedAIModelConfiguration()
    pipeline = stored.byok.pipeline if stored and stored.byok else None
    if pipeline is not None:
        for key in ("llm", "stt", "tts", "embeddings"):
            section = getattr(pipeline, key, None)
            if section is None:
                continue
            vendor = getattr(section.provider, "value", section.provider)
            if vendor == "decibyl":
                continue
            prefix = (
                "" if getattr(section, "use_platform_key", False) else OWN_KEY_PREFIX
            )
            managed.slots[key] = f"{prefix}{vendor}/{section.model}"
    return managed


class UnknownChoice(ValueError):
    pass


class NotReady(UnknownChoice):
    """Offered, but still being set up: listed honestly, never saved."""


class LockedStack(ValueError):
    pass


def choose(
    stored: OrganizationAIModelConfigurationV2 | None,
    *,
    slot: str,
    value: str,
    offered: dict[str, Any],
) -> OrganizationAIModelConfigurationV2:
    """The stored configuration with ``slot`` set to ``value``.

    Only a value the screen offered is accepted: these strings come from a
    browser, and a vendor name nobody checked must not decide what runs.
    """
    reason = locked_reason(stored)
    if reason:
        raise LockedStack(reason)
    entry = next((s for s in offered["slots"] if s["key"] == slot), None)
    if entry is None:
        raise UnknownChoice(f"No setting called {slot!r}")
    choice = (value or "").strip()
    managed = managed_configuration(stored)
    spec = SLOTS_BY_KEY[slot]

    ours = {o["value"]: o for o in entry["ours"]}
    if choice in ours:
        if ours[choice].get("status", "available") != "available":
            raise NotReady(
                f"{ours[choice]['label']} is not ready yet: "
                f"{ours[choice].get('status_note') or NEEDS_SETUP_NOTE}"
            )
        managed.slots.pop(slot, None)
        tier = choice.removeprefix(TIER_PREFIX)
        if spec.tier_field:
            setattr(managed, spec.tier_field, tier)
        elif tier != "default":
            # No tier field to hold it: stored as a choice of ours by name.
            managed.slots[slot] = f"decibyl/{tier}"
    elif choice in {o["value"] for o in entry["more"]}:
        managed.slots[slot] = choice.removeprefix(MODEL_PREFIX)
    elif choice.startswith(OWN_KEY_PREFIX):
        parsed = parse_slot_choice(choice)
        own = {o["vendor"]: o for o in entry["own"]}
        if (
            parsed is None
            or parsed[0] not in own
            or parsed[1] not in own[parsed[0]]["models"]
        ):
            raise UnknownChoice(f"{choice!r} is not offered for {spec.label}")
        managed.slots[slot] = choice
    else:
        raise UnknownChoice(f"{choice!r} is not offered for {spec.label}")
    return OrganizationAIModelConfigurationV2(mode="decibyl", decibyl=managed)
