"""Who may join a live call.

Everything listening needs (``live_supervision.access``: "Allow live
listening" on, and an admin, an owner or the agent's own owner), and one
more switch: **"Allow supervisors to join calls"**, per workspace, **off by
default**, switched by admins and owners only, with the change in the audit
log. Listening is monitoring; speaking to a business's customer as that
business is a bigger step, and a workspace takes it on its own decision.
"""

from __future__ import annotations

from api.db import db_client
from api.enums import OrganizationConfigurationKey
from api.services.live_supervision import access as listen_access
from api.services.workflow import audit_log

KEY = OrganizationConfigurationKey.LIVE_TAKEOVER.value
#: The workspace's choice when it has never made one.
DEFAULT_ALLOW_JOINING = False

JOINING_OFF = "joining_off"


async def allow_joining(organization_id: int) -> bool:
    value = await db_client.get_configuration_value(organization_id, KEY, None)
    if isinstance(value, dict) and isinstance(value.get("allow_joining"), bool):
        return value["allow_joining"]
    return DEFAULT_ALLOW_JOINING


async def set_allow_joining(viewer: listen_access.Viewer, allowed: bool) -> bool:
    before = await allow_joining(viewer.organization_id)
    await db_client.upsert_configuration(
        viewer.organization_id, KEY, {"allow_joining": bool(allowed)}
    )
    if before != bool(allowed):
        await audit_log.record(
            viewer.organization_id,
            action="live_joining_setting",
            subject_kind="workspace",
            subject_id=viewer.organization_id,
            subject="Allow supervisors to join calls",
            actor_user_id=viewer.user_id,
            actor=viewer.name,
            before={"allow_joining": before},
            after={"allow_joining": bool(allowed)},
        )
    return bool(allowed)


def blocked_reason(
    viewer: listen_access.Viewer,
    agent_owner_id: int | None,
    *,
    allow_listening: bool,
    allow_joining: bool,
) -> str | None:
    """Why this person cannot join this agent's calls now, or None. The
    listening walls first: somebody who cannot listen is told that."""
    reason = listen_access.blocked_reason(
        viewer, agent_owner_id, allowed=allow_listening
    )
    if reason:
        return reason
    if not allow_joining:
        return JOINING_OFF
    return None
