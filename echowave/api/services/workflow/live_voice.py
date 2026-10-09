"""Change an agent's voice from its About panel, and have it take effect.

The About panel sits beside the agent's chat and offers three things a
person changes: its voice, its skills and its memory. Skills and memory are
live the moment they are saved. The voice was not: it was saved into the
agent's draft, nothing on that page publishes, so the change waited
silently -- and went live later, by surprise, the next time anybody
published an unrelated edit from a card in the thread.

So a voice changed here is published on its own: a new version that is the
live one with only the voice changed. Whatever else is waiting in the draft
stays a draft; it is given the same voice so that publishing it later does
not quietly put the old voice back.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from api.db import db_client
from api.services.configuration import model_slot
from api.services.workflow import audit_log

#: Where this publish came from, on its audit row.
VIA_ABOUT_VOICE = "about_voice"

#: What the voice's own panel edits besides the slot. Anything else sent
#: here is refused, not dropped -- see ``SettingsRefused``.
VOICE_SETTINGS = frozenset({"pronunciation_lexicon", "ambient_noise_configuration"})


class AgentNotFound(LookupError):
    pass


class SettingsRefused(ValueError):
    """A setting the voice panel does not own was sent to be put live."""


@dataclass(frozen=True)
class AppliedVoice:
    version_number: int | None
    published_at: datetime | None
    #: True when a draft was waiting; it was left a draft.
    draft_kept: bool
    #: The slot as it was before, for analytics.
    before: dict[str, Any]


def _with_settings(configurations: dict, settings: dict[str, Any] | None) -> dict:
    out = dict(configurations)
    for key, value in (settings or {}).items():
        out[key] = copy.deepcopy(value)
    return out


async def apply_voice_now(
    *,
    workflow_id: int,
    organization_id: int,
    user_id: int | None,
    component: str,
    provider: str,
    model: str,
    voice: str | None,
    tuning: dict[str, Any] | None = None,
    settings: dict[str, Any] | None = None,
) -> AppliedVoice:
    """Publish this voice change, and only it. Org-scoped.

    Raises ``AgentNotFound``, ``model_slot.SlotRefused`` (a model the
    catalogue does not sell, or a stack that cannot run) or
    ``SettingsRefused``. Writes nothing unless every check passes.
    """
    unknown = sorted(set(settings or {}) - VOICE_SETTINGS)
    if unknown:
        raise SettingsRefused(
            "Only the voice's own settings can be changed here, not "
            + ", ".join(unknown)
            + "."
        )

    workflow = await db_client.get_workflow(
        workflow_id, organization_id=organization_id
    )
    if workflow is None:
        raise AgentNotFound("Workflow not found")
    live = await db_client.get_published_definition(workflow_id, organization_id)
    if live is None:
        raise AgentNotFound("This agent has no live version to change.")

    await model_slot.ensure_sellable(
        component=component, provider=provider, model=model
    )

    slot = dict(
        organization_id=organization_id,
        component=component,
        provider=provider,
        model=model,
        voice=voice,
        tuning=tuning,
    )
    live_configurations, before = await model_slot.with_slot(
        live.workflow_configurations or {}, **slot
    )
    live_configurations = _with_settings(live_configurations, settings)

    draft = await db_client.get_draft_version(workflow_id)
    draft_configurations = None
    if draft is not None:
        draft_configurations, _ = await model_slot.with_slot(
            draft.workflow_configurations or {}, **slot
        )
        draft_configurations = _with_settings(draft_configurations, settings)

    published = await db_client.publish_configurations(
        workflow_id,
        published_configurations=live_configurations,
        draft_configurations=draft_configurations,
    )
    if published is None:
        raise AgentNotFound("This agent has no live version to change.")

    after_slot = (
        (
            live_configurations.get(
                model_slot.WORKFLOW_MODEL_CONFIGURATION_V2_OVERRIDE_KEY
            )
            or {}
        )
        .get("stack", {})
        .get(component)
    )
    await audit_log.record(
        organization_id,
        action=audit_log.AGENT_PUBLISHED,
        subject_kind="agent",
        subject_id=workflow_id,
        subject=getattr(workflow, "name", None),
        actor_user_id=user_id,
        before={"version_number": live.version_number, component: before},
        after={
            "version_number": published.version_number,
            "via": VIA_ABOUT_VOICE,
            component: after_slot,
            "settings": sorted(settings or {}),
        },
        note=(
            "Voice changed from the About panel; the waiting draft was not published."
            if draft is not None
            else "Voice changed from the About panel."
        ),
    )
    return AppliedVoice(
        version_number=published.version_number,
        published_at=published.published_at,
        draft_kept=draft is not None,
        before=before,
    )
