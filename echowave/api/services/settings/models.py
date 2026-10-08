"""Model defaults showing inheritance (screen 26; handoff 8, 25, 30).

Built on the existing Settings -> Models screen (``services/configuration/
workspace_models``): the same four slots, the same offered choices, the same
``choose``. This adds what that screen does not say:

* **Where each value comes from** -- the platform default, or this
  workspace's own choice -- in the precedence the handoff names: platform
  allowlist and policy, then the workspace default, then a permitted agent
  override.
* **Readiness** -- whether the key it needs is actually held on this
  deployment (``ready``), not (``missing_credential`` for a workspace key,
  ``needs_setup`` for a platform key nobody has added), or the stack is
  managed elsewhere (``managed``). Never "ready" on a guess.
* **Fallback** -- the explicitly configured backups for hearing and voice,
  and none invented.
* **Agent overrides** -- each agent either "Inherits workspace" or shows the
  override it runs.
* **A revision per slot**, so a save made from a stale screen is a conflict
  carrying the stored value rather than an overwrite, and two quick picks
  leave client and server on the last one (handoff 30).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

from loguru import logger

from api.services import features
from api.services.settings import MODEL_INHERITANCE

#: What a slot's readiness says, for the screen.
READY = "ready"
MISSING_CREDENTIAL = "missing_credential"
NEEDS_SETUP = "needs_setup"
MANAGED = "managed"


def enabled(organization_id: int | None = None) -> bool:
    return features.is_on(MODEL_INHERITANCE, organization_id)


@dataclass
class Conflict(Exception):
    stored: dict[str, Any]


def revision_of(slot: str, current: str) -> str:
    return hashlib.sha256(f"{slot}\x1f{current}".encode()).hexdigest()[:12]


def _vendor_of(slot_key: str, current: str) -> str | None:
    from api.schemas.ai_model_configuration import parse_slot_choice
    from api.services.configuration import managed_tiers, workspace_models

    if current.startswith(workspace_models.TIER_PREFIX):
        tier = current.removeprefix(workspace_models.TIER_PREFIX)
        try:
            return managed_tiers.resolve(slot_key, tier).provider
        except Exception:  # noqa: BLE001 - a slot with no tiers
            return None
    parsed = parse_slot_choice(current.removeprefix(workspace_models.MODEL_PREFIX))
    return parsed[0] if parsed else None


def _readiness(
    slot_key: str,
    current: str,
    *,
    locked: str | None,
    platform_providers: dict[str, list[str]],
    keys_held: dict[str, list[str]],
) -> tuple[str, str]:
    from api.schemas.ai_model_configuration import OWN_KEY_PREFIX
    from api.services.configuration import workspace_models

    if locked:
        return MANAGED, locked
    vendor = _vendor_of(slot_key, current)
    label = workspace_models.vendor_label(vendor) if vendor else "this choice"
    if current.startswith(OWN_KEY_PREFIX):
        held = set(keys_held.get(workspace_models._credential_component(slot_key), []))
        if vendor in held:
            return READY, f"Runs on your {label} key."
        return MISSING_CREDENTIAL, f"Add your {label} key, or choose one we provide."
    if vendor and vendor in set(platform_providers.get(slot_key, [])):
        return READY, f"Provided by Decibyl ({label})."
    return (
        NEEDS_SETUP,
        (
            f"Decibyl's {label} key is not set up on this deployment yet, so this "
            "cannot run until it is."
        ),
    )


def _source(slot_key: str, stored: Any, managed: Any) -> str:
    """``platform`` when the workspace never chose this slot, ``workspace``
    when it did."""
    from api.services.configuration import workspace_models

    if stored is None:
        return "platform"
    if slot_key in (managed.slots or {}):
        return "workspace"
    field = workspace_models.SLOTS_BY_KEY[slot_key].tier_field
    if field is None:
        return "platform"
    tier = getattr(managed, field, None)
    return "workspace" if tier and tier != "default" else "platform"


def _fallbacks(stored: Any) -> dict[str, list[str]]:
    """The backups configured for hearing and voice, as labels."""
    from api.schemas.ai_model_configuration import compile_ai_model_configuration_v2
    from api.services.configuration import workspace_models

    out: dict[str, list[str]] = {"stt": [], "tts": []}
    if stored is None:
        return out
    try:
        effective = compile_ai_model_configuration_v2(stored)
    except Exception as exc:  # noqa: BLE001 - shown as none, and logged
        logger.warning("Could not compile the workspace stack: {}", exc)
        return out
    for slot, sections in (
        ("stt", effective.fallback_stt),
        ("tts", effective.fallback_tts),
    ):
        for section in sections or []:
            provider = getattr(section.provider, "value", section.provider)
            out[slot].append(
                f"{workspace_models.vendor_label(str(provider))} · {getattr(section, 'model', '')}"
            )
    return out


async def _agent_overrides(organization_id: int) -> list[dict[str, Any]]:
    """Each agent: does it inherit the workspace, or run its own?"""
    from api.db import db_client
    from api.services.configuration import workspace_models
    from api.services.configuration.ai_model_configuration import (
        WORKFLOW_MODEL_CONFIGURATION_V2_OVERRIDE_KEY,
        _is_managed_default,
        compile_workflow_model_configuration_override,
    )

    workflows = await db_client.get_all_workflows(
        organization_id=organization_id, status="active"
    )
    out = []
    for workflow in workflows:
        config = getattr(workflow, "workflow_configurations", None) or {}
        override = config.get(WORKFLOW_MODEL_CONFIGURATION_V2_OVERRIDE_KEY)
        slots: dict[str, dict[str, Any]] = {}
        effective = None
        if override:
            try:
                effective = compile_workflow_model_configuration_override(override)
            except Exception:  # noqa: BLE001 - unreadable is its own choice
                effective = None
        for key in ("llm", "stt", "tts"):
            section = getattr(effective, key, None) if effective else None
            if not override:
                slots[key] = {"inherits": True, "label": "Inherits workspace"}
            elif effective is None:
                slots[key] = {"inherits": False, "label": "Its own setup (unreadable)"}
            elif section is None or _is_managed_default(section):
                slots[key] = {"inherits": True, "label": "Inherits workspace"}
            else:
                provider = getattr(section.provider, "value", section.provider)
                slots[key] = {
                    "inherits": False,
                    "label": f"{workspace_models.vendor_label(str(provider))} · "
                    f"{getattr(section, 'model', '') or ''}".strip(" ·"),
                }
        out.append(
            {"workflow_id": int(workflow.id), "name": workflow.name, "slots": slots}
        )
    return out


async def view(organization_id: int) -> dict[str, Any]:
    """The Models screen's answer, with inheritance, readiness, fallback,
    overrides and a revision per slot."""
    from api.db import db_client
    from api.services.configuration import (
        managed_resolution,
        organization_credentials,
        workspace_models,
    )
    from api.services.configuration.ai_model_configuration import (
        get_organization_ai_model_configuration_v2,
    )

    stored = await get_organization_ai_model_configuration_v2(organization_id)
    async with db_client.async_session() as session:
        keys_held = await organization_credentials.available_providers(
            session, organization_id=organization_id
        )
        platform_providers = await managed_resolution.platform_provider_catalog(session)
    base = workspace_models.view(
        stored, platform_providers=platform_providers, keys_held=keys_held
    )
    managed = workspace_models.managed_configuration(stored)
    fallbacks = _fallbacks(stored)
    for slot in base["slots"]:
        key = slot["key"]
        state, reason = _readiness(
            key,
            slot["current"],
            locked=base.get("locked"),
            platform_providers=platform_providers,
            keys_held=keys_held,
        )
        vendor = _vendor_of(key, slot["current"])
        slot["source"] = _source(key, stored, managed)
        slot["readiness"] = state
        slot["readiness_reason"] = reason
        slot["revision"] = revision_of(key, slot["current"])
        slot["fallback"] = fallbacks.get(key, [])
        slot["provider"] = vendor
        if key in ("stt", "tts"):
            # Sarvam for voice (founder decision): say whether it is the one
            # running, and whether it is on offer -- never claim it is used.
            offered = any("Sarvam" in (o.get("serves") or "") for o in slot["ours"])
            slot["sarvam"] = {"in_use": vendor == "sarvam", "offered": offered}
    base["agents"] = await _agent_overrides(organization_id)
    base["precedence"] = [
        "Decibyl's allowed models and policy",
        "This workspace's choice",
        "An agent's own override, where one is allowed",
    ]
    return base


async def choose(
    organization_id: int, *, slot: str, value: str, revision: str | None
) -> dict[str, Any]:
    """Set one slot, if the screen saw the value that is stored now."""
    from api.services.configuration import workspace_models
    from api.services.configuration.ai_model_configuration import (
        get_organization_ai_model_configuration_v2,
        upsert_organization_ai_model_configuration_v2,
    )

    current = await view(organization_id)
    entry = next((s for s in current["slots"] if s["key"] == slot), None)
    if entry is None:
        raise workspace_models.UnknownChoice(f"No setting called {slot!r}")
    if revision is not None and revision != entry["revision"]:
        raise Conflict(stored=entry)
    stored = await get_organization_ai_model_configuration_v2(organization_id)
    configuration = workspace_models.choose(
        stored, slot=slot, value=value, offered=current
    )
    await upsert_organization_ai_model_configuration_v2(organization_id, configuration)
    return await view(organization_id)
