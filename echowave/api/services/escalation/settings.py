"""Reading and saving one agent's escalation policy, from its page or the chat.

Saved into the agent's **draft**, beside everything else on its
configuration, the way the booking setup saves an agent's hours: the agent's
owner tries it in the tester and it reaches live calls when the draft is
published. A policy that changed live calls the moment a field was typed
would make the settings card the one place on the agent page that skips
Publish.
"""

from __future__ import annotations

from typing import Any, Mapping

from pydantic import ValidationError

from api.db import db_client
from api.services.escalation.policy import (
    CONFIG_KEY,
    TOPIC_LABELS,
    TOPICS,
    EscalationPolicy,
    from_configurations,
    validate_changes,
)


class PolicyInvalid(ValueError):
    pass


class AgentNotFound(LookupError):
    pass


def _message(exc: ValidationError) -> str:
    errors = exc.errors()
    if not errors:
        return "That policy is not valid."
    first = errors[0]
    where = ".".join(str(p) for p in first.get("loc", ()) if not isinstance(p, int))
    text = str(first.get("msg") or "is not valid").removeprefix("Value error, ")
    return f"{where}: {text}" if where else text


async def _configurations(organization_id: int, workflow_id: int) -> tuple[Any, dict]:
    workflow = await db_client.get_workflow(
        workflow_id, organization_id=organization_id
    )
    if workflow is None:
        raise AgentNotFound("That agent is not here.")
    draft = await db_client.get_draft_version(workflow_id)
    configurations = dict(
        (draft.workflow_configurations if draft is not None else None)
        or await db_client.get_released_configurations(workflow)
        or workflow.workflow_configurations
        or {}
    )
    return workflow, configurations


def topics() -> list[dict[str, str]]:
    return [{"key": key, "label": TOPIC_LABELS[key]} for key in TOPICS]


async def read(organization_id: int, workflow_id: int) -> dict[str, Any]:
    workflow, configurations = await _configurations(organization_id, workflow_id)
    released = await db_client.get_released_configurations(workflow)
    policy = from_configurations(configurations)
    live = from_configurations(released or {})
    return {
        "workflow_id": workflow_id,
        "policy": policy.model_dump(mode="json"),
        "topics": topics(),
        #: The draft holds a policy that live calls do not use yet.
        "unpublished": policy.model_dump(mode="json") != live.model_dump(mode="json"),
    }


async def save(
    organization_id: int, workflow_id: int, changes: Mapping[str, Any]
) -> dict[str, Any]:
    try:
        policy = validate_changes(changes)
    except ValidationError as exc:
        raise PolicyInvalid(_message(exc)) from exc
    _workflow, configurations = await _configurations(organization_id, workflow_id)
    configurations[CONFIG_KEY] = policy.model_dump(mode="json")
    await db_client.save_workflow_draft(
        workflow_id, workflow_configurations=configurations
    )
    return await read(organization_id, workflow_id)


def merged(current: EscalationPolicy, changes: Mapping[str, Any]) -> dict[str, Any]:
    """A partial change (from the chat) applied over the current policy."""
    out = current.model_dump(mode="json")
    for key, value in changes.items():
        if key in out:
            out[key] = value
    return out


__all__ = [
    "AgentNotFound",
    "PolicyInvalid",
    "merged",
    "read",
    "save",
    "topics",
]
