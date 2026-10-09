"""Who may put a change to an agent live (editing_v2).

Anybody in the workspace may *propose* a change -- by chat, in a huddle, or
through Decibyl -- and the card lands on the thread for everyone to read.
Putting it live, or taking it back out with Undo, is narrower.

**Default pending founder decision 11.** The rule lives in ``PUBLISH_RULE``
and nowhere else: the agent's owner (the person who created it) and the
workspace's admins and owners may publish or undo; every other member sees
"Waiting for <name> or a workspace admin to publish" on the card. Changing
the decision is changing that one constant.

The same rule guards the About panel's voice row, which puts a voice live
on its own: launch-readiness found a plain member could change a live
agent's voice that way while the card's Publish was meant to be guarded.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from loguru import logger

from api.db import db_client
from api.enums import ORGANIZATION_ROLE_RANK, OrganizationRole
from api.services import features

FLAG = "editing_v2"


@dataclass(frozen=True)
class PublishRule:
    #: The agent's owner (``WorkflowModel.user_id``) may publish its changes.
    agent_owner: bool
    #: Anyone at this role or above may publish any agent's changes.
    min_role: OrganizationRole


#: Default pending founder decision 11 -- see the module docstring.
PUBLISH_RULE = PublishRule(agent_owner=True, min_role=OrganizationRole.ADMIN)


class NotAllowed(PermissionError):
    """The person may propose but not publish; ``str()`` says who can."""


def enabled(organization_id: int | None) -> bool:
    return features.is_on(FLAG, organization_id)


async def may_publish(
    *, organization_id: int, user_id: int | None, workflow: Any
) -> bool:
    """Whether this person may put changes to this agent live."""
    if user_id is None:
        return False
    if PUBLISH_RULE.agent_owner and getattr(workflow, "user_id", None) == user_id:
        return True
    membership = await db_client.get_membership(user_id, organization_id)
    rank = ORGANIZATION_ROLE_RANK.get(getattr(membership, "role", "") or "", -1)
    return rank >= ORGANIZATION_ROLE_RANK[PUBLISH_RULE.min_role.value]


async def owner_name(workflow: Any) -> str | None:
    """What the card calls the agent's owner: their preferred name, else the
    part of their email before the @, else None."""
    owner_id = getattr(workflow, "user_id", None)
    if owner_id is None:
        return None
    try:
        from api.services.shell import onboarding

        state = await onboarding.get(owner_id)
        name = (getattr(state, "preferred_name", None) or "").strip()
        if name:
            return name
        user = await db_client.get_user_by_id(owner_id)
        email = (getattr(user, "email", None) or "").strip()
        return email.split("@", 1)[0] or None
    except Exception as exc:  # noqa: BLE001 - a name is a courtesy
        logger.warning("Could not name the owner of agent {}: {}", workflow, exc)
        return None


def waiting_line(name: str | None) -> str:
    """The card's line for somebody who may propose but not publish."""
    if PUBLISH_RULE.agent_owner and name:
        return f"Waiting for {name} or a workspace admin to publish."
    return "Waiting for a workspace admin to publish."


async def who_can_publish(*, organization_id: int, workflow: Any) -> str:
    return waiting_line(
        await owner_name(workflow) if PUBLISH_RULE.agent_owner else None
    )


async def check(*, organization_id: int, user_id: int | None, workflow: Any) -> None:
    """Raise ``NotAllowed`` unless this person may publish. A no-op while
    editing_v2 is off: the old behaviour lets anyone in the workspace."""
    if not enabled(organization_id):
        return
    if await may_publish(
        organization_id=organization_id, user_id=user_id, workflow=workflow
    ):
        return
    raise NotAllowed(
        await who_can_publish(organization_id=organization_id, workflow=workflow)
    )


__all__ = [
    "FLAG",
    "PUBLISH_RULE",
    "NotAllowed",
    "PublishRule",
    "check",
    "enabled",
    "may_publish",
    "owner_name",
    "waiting_line",
    "who_can_publish",
]
