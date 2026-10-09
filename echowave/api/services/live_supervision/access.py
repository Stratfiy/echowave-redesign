"""Who may listen to a live call and whisper to its agent.

Two gates, both needed:

1. **The workspace allows it.** "Allow live listening" is a per-workspace
   setting, **off by default** even where the feature is switched on.
   Listening to a customer's call is monitoring; a workspace starts doing it
   because somebody there decided to, with their name on the change in the
   audit log -- not because a flag reached their account.
2. **The person may supervise this agent**: an admin or owner of the
   workspace, or the person who owns the agent. A member listening to a
   colleague's agent's calls is not something the existing roles granted,
   so it is not granted here either.

The same test decides whether the live list shows a caller's full number:
somebody who may listen to the call may know who is on it. Everybody else
sees the last four digits.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from api.db import db_client
from api.enums import ORGANIZATION_ROLE_RANK, OrganizationConfigurationKey
from api.services.workflow import audit_log

KEY = OrganizationConfigurationKey.LIVE_SUPERVISION.value
#: The workspace's choice when it has never made one.
DEFAULT_ALLOW_LISTENING = False

SETTING_OFF = "setting_off"
NOT_PERMITTED = "not_permitted"


@dataclass(frozen=True)
class Viewer:
    user_id: int
    organization_id: int
    role: str | None
    name: str

    @property
    def is_admin(self) -> bool:
        return (
            ORGANIZATION_ROLE_RANK.get(self.role or "", -1)
            >= ORGANIZATION_ROLE_RANK["admin"]
        )


async def viewer_for(user: Any) -> Viewer:
    organization_id = int(user.selected_organization_id)
    membership = await db_client.get_membership(user.id, organization_id)
    # A user row has no display name: the address's local part is what the
    # rest of the product shows for a member (tasks_board.person_name).
    name = str(user.email or "").split("@")[0].strip() or f"Member {user.id}"
    return Viewer(
        user_id=int(user.id),
        organization_id=organization_id,
        role=getattr(membership, "role", None) if membership else None,
        name=name,
    )


async def allow_listening(organization_id: int) -> bool:
    value = await db_client.get_configuration_value(organization_id, KEY, None)
    if isinstance(value, dict) and isinstance(value.get("allow_listening"), bool):
        return value["allow_listening"]
    return DEFAULT_ALLOW_LISTENING


async def set_allow_listening(viewer: Viewer, allowed: bool) -> bool:
    before = await allow_listening(viewer.organization_id)
    await db_client.upsert_configuration(
        viewer.organization_id, KEY, {"allow_listening": bool(allowed)}
    )
    if before != bool(allowed):
        await audit_log.record(
            viewer.organization_id,
            action="live_listening_setting",
            subject_kind="workspace",
            subject_id=viewer.organization_id,
            subject="Allow live listening",
            actor_user_id=viewer.user_id,
            actor=viewer.name,
            before={"allow_listening": before},
            after={"allow_listening": bool(allowed)},
        )
    return bool(allowed)


def may_supervise(viewer: Viewer, agent_owner_id: int | None) -> bool:
    """The person, setting aside the workspace switch."""
    return viewer.is_admin or (
        agent_owner_id is not None and int(agent_owner_id) == viewer.user_id
    )


def blocked_reason(
    viewer: Viewer, agent_owner_id: int | None, *, allowed: bool
) -> str | None:
    """Why this person cannot listen to this agent's calls now, or None."""
    if not may_supervise(viewer, agent_owner_id):
        return NOT_PERMITTED
    if not allowed:
        return SETTING_OFF
    return None


def mask_number(number: str | None) -> str | None:
    digits = "".join(ch for ch in (number or "") if ch.isdigit())
    if not digits:
        return None
    return "•••• " + digits[-4:] if len(digits) > 4 else "••••"
