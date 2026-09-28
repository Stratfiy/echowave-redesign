"""Who can see which agent (KAN-158).

Roles were owner, admin and member with no per-agent scope, so the finance
agent was in every member's list. An agent now carries a ``visibility``:
``everyone`` (the default, and what every agent was) or ``admins``. The
rule is applied wherever a *person* is served a set of agents -- the list,
the editor, a channel's roster, the board's names, Decibyl's reading of
the workspace -- and nowhere a run is: a call, a routine or a trigger is
not a viewer and sees the agent it runs.

One helper per question, so a screen and the assistant cannot disagree:
``visible`` for one agent, ``only_visible`` for a list, ``role_of`` for
the viewer. An unknown role ranks lowest: a viewer whose membership cannot
be read sees what a member sees, never more.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from api.enums import ORGANIZATION_ROLE_RANK, OrganizationRole
from api.services import features

EVERYONE = "everyone"
ADMINS = "admins"
VISIBILITIES = (EVERYONE, ADMINS)


def clean(value: Any) -> str:
    text = str(value or EVERYONE).strip().lower()
    if text not in VISIBILITIES:
        raise ValueError(f"visibility must be one of {', '.join(VISIBILITIES)}.")
    return text


def enabled() -> bool:
    return features.is_on("workspace_roles")


def visible(workflow: Any, role: str | None) -> bool:
    """May a person with this role see this agent?"""
    if not enabled():
        return True
    if getattr(workflow, "visibility", EVERYONE) != ADMINS:
        return True
    rank = ORGANIZATION_ROLE_RANK.get(str(role or ""), -1)
    return rank >= ORGANIZATION_ROLE_RANK[OrganizationRole.ADMIN.value]


def only_visible(workflows: Iterable[Any], role: str | None) -> list[Any]:
    return [w for w in workflows if visible(w, role)]


async def role_of(user_id: int | None, organization_id: int | None) -> str | None:
    """The viewer's role in the workspace, or None when there is no row --
    which ranks below member, never above."""
    if not enabled() or not user_id or not organization_id:
        # With the switch off nothing is hidden, so nothing is looked up.
        return None
    from api.db import db_client

    membership = await db_client.get_membership(int(user_id), int(organization_id))
    return getattr(membership, "role", None) if membership else None
