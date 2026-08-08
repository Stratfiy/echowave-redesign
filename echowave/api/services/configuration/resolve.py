"""Resolve effective config by merging per-workflow model overrides onto global config."""

from __future__ import annotations

import copy

from api.schemas.ai_model_configuration import EffectiveAIModelConfiguration
from api.services.configuration.registry import (
    REGISTRY,
    ServiceType,
)

# Maps override key → (EffectiveAIModelConfiguration field, ServiceType for registry lookup)
_SECTION_MAP: dict[str, ServiceType] = {
    "llm": ServiceType.LLM,
    "tts": ServiceType.TTS,
    "stt": ServiceType.STT,
    "realtime": ServiceType.REALTIME,
}


def _build_section_from_override(service_type: ServiceType, override: dict):
    """Construct a typed config object from a raw override dict using the registry."""
    provider = override.get("provider")
    if not provider:
        return None
    registry = REGISTRY.get(service_type, {})
    config_cls = registry.get(provider)
    if config_cls is None:
        return None
    return config_cls(**override)


_SECRET_FIELDS = ("api_key", "credentials", "aws_access_key", "aws_secret_key")


#: Which credential component authenticates each override section. Realtime is
#: metered and keyed as LLM — the vendor issues one key for both.
_SECTION_CREDENTIAL_COMPONENT: dict[str, str] = {
    "llm": "llm",
    "tts": "tts",
    "stt": "stt",
    "realtime": "llm",
}


def enrich_overrides_with_api_keys(
    model_overrides: dict,
    user_config: EffectiveAIModelConfiguration,
) -> dict:
    """Copy API keys from the global config into model_overrides where missing.

    When a workflow override selects the same provider as the current global
    config but omits the API key, the override becomes broken if the global
    config later switches to a different provider. This function stamps the
    global provider's API key (and other secret fields) into the override at
    save time so the override is self-contained.

    **Only covers the case where the override names the same provider as the
    global config.** An override that picks a *different* vendor — the whole
    point of a per-agent override — gets no key from here, because the global
    section does not hold one. That case is served by
    :func:`enrich_overrides_from_vault`, which reads the account's own stored
    keys. Both are needed: this one keeps an override working when the global
    config later moves, the other makes a divergent override work at all.
    """
    result = copy.deepcopy(model_overrides)
    for section_key in _SECTION_MAP:
        if section_key not in result:
            continue
        override = result[section_key]
        override_provider = override.get("provider")
        if not override_provider:
            continue
        global_section = getattr(user_config, section_key, None)
        if global_section is None:
            continue
        if getattr(global_section, "provider", None) != override_provider:
            continue
        for field in _SECRET_FIELDS:
            if override.get(field):
                continue
            if field == "api_key" and hasattr(global_section, "get_all_api_keys"):
                all_keys = global_section.get_all_api_keys()
                if all_keys:
                    override[field] = all_keys[0] if len(all_keys) == 1 else all_keys
            else:
                global_value = getattr(global_section, field, None)
                if global_value is not None:
                    override[field] = global_value
    return result


async def enrich_overrides_from_vault(
    session,
    model_overrides: dict,
    *,
    organization_id: int,
) -> dict:
    """Fill an override's missing API key from the account's own key vault.

    A per-agent override exists so one agent can run on a different vendor from
    the rest of the account. That is exactly the case
    :func:`enrich_overrides_with_api_keys` cannot serve — it copies from the
    global config section, which holds a key for the *global* provider and
    nothing for the one the override picked. The result was an override that
    saved cleanly, showed the vendor's own "missing credentials" error on the
    settings screen, and would have failed at dial time.

    The key comes from the same vault the Provider Keys screen writes to, so a
    key the customer can see listed is a key their override can use. A provider
    they hold no key for is left alone: the slot is then genuinely
    unconfigured, and saying so beats inventing a credential.
    """
    from api.services.configuration import organization_credentials

    result = copy.deepcopy(model_overrides)
    for section_key, component in _SECTION_CREDENTIAL_COMPONENT.items():
        override = result.get(section_key)
        if not isinstance(override, dict):
            continue
        provider = override.get("provider")
        if not provider or override.get("api_key"):
            continue

        key = await organization_credentials.resolve_api_key(
            session,
            organization_id=organization_id,
            component=component,
            provider=provider,
        )
        if key:
            override["api_key"] = key
    return result


def resolve_effective_config(
    user_config: EffectiveAIModelConfiguration,
    model_overrides: dict | None,
) -> EffectiveAIModelConfiguration:
    """Deep-merge workflow model_overrides onto global user config.

    - If model_overrides is None or empty, returns a copy of user_config unchanged.
    - For each section (llm, tts, stt, realtime), if the override contains that key:
      - If the global section is None, construct a new config from the override.
      - If the provider changes, construct a new config from the override.
      - Otherwise, merge override fields onto the existing config (model_copy).
    - is_realtime is a simple boolean override.
    - Sections not in the override are inherited from global unchanged.
    - The original user_config is never mutated.
    """
    if not model_overrides:
        return user_config.model_copy(deep=True)

    effective = user_config.model_copy(deep=True)

    # Handle is_realtime boolean
    if "is_realtime" in model_overrides:
        effective.is_realtime = model_overrides["is_realtime"]

    # Handle service sections
    for section_key, service_type in _SECTION_MAP.items():
        if section_key not in model_overrides:
            continue

        override = model_overrides[section_key]
        base = getattr(effective, section_key)

        if base is None:
            # No global config for this section — build from override
            setattr(
                effective,
                section_key,
                _build_section_from_override(service_type, override),
            )
        elif "provider" in override and override["provider"] != base.provider:
            # Provider changed — must construct new typed object
            setattr(
                effective,
                section_key,
                _build_section_from_override(service_type, override),
            )
        else:
            # Same provider — merge fields onto existing config
            merged = base.model_copy(update=override)
            setattr(effective, section_key, merged)

    return effective
