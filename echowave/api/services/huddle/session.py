"""Starting a huddle, and telling one apart from Talk.

A huddle is a ``voice_sessions`` row whose fixed ``config`` names the agent
(``config["huddle"]``). Everything about the row -- one live per person,
stale moves losing, End always working, minutes settled at the end, the
sweep of abandoned sessions -- is ``services/voice/sessions``'s, unchanged.
What this adds is the refusals before anything connects: the agent must be
this workspace's and visible to this person, voice must be set up, and the
person's daily voice minutes must not be spent.
"""

from __future__ import annotations

from typing import Any

from api.db import db_client
from api.services import member_preferences, quotas
from api.services.huddle import FLAG, record
from api.services.voice import readiness, sessions


class NotFound(Exception):
    """No such agent here, as far as this person may know."""


class NotReady(Exception):
    def __init__(self, state: readiness.Readiness):
        super().__init__(state.reason or state.state)
        self.state = state


async def agent_for(*, organization_id: int, user_id: int, workflow_id: int) -> Any:
    """The agent, if it is this workspace's and this person may see it."""
    from api.services.workflow import visibility

    workflow = await db_client.get_workflow(
        workflow_id, organization_id=organization_id
    )
    if workflow is None:
        raise NotFound()
    role = await visibility.role_of(user_id, organization_id)
    if not visibility.visible(workflow, role):
        raise NotFound()
    return workflow


def workflow_of(session: dict[str, Any] | None) -> int | None:
    """The agent a huddle session is with; None for any other session."""
    huddle = ((session or {}).get("config") or {}).get("huddle") or {}
    value = huddle.get("workflow_id") if isinstance(huddle, dict) else None
    return int(value) if isinstance(value, int) else None


async def get(
    *, organization_id: int, user_id: int, session_id: int
) -> dict[str, Any] | None:
    """The person's own huddle session; None for Talk or anybody else's."""
    session = await sessions.get(
        organization_id=organization_id, user_id=user_id, session_id=session_id
    )
    return session if workflow_of(session) is not None else None


async def start(
    *, organization_id: int, user_id: int, workflow_id: int
) -> dict[str, Any]:
    """Open a huddle in ``connecting``.

    Raises NotFound, NotReady, ``quotas.QuotaExceeded`` or
    ``sessions.AlreadyLive``. Returns the session with the agent's name and
    the notes it will start from."""
    workflow = await agent_for(
        organization_id=organization_id, user_id=user_id, workflow_id=workflow_id
    )
    prefs = await member_preferences.get(user_id)
    state = await readiness.live_voice(
        organization_id=organization_id,
        user_id=user_id,
        language=prefs.get("language"),
        flag=FLAG,
    )
    if state.state != readiness.AVAILABLE:
        raise NotReady(state)
    await quotas.check(user_id, quotas.VOICE_MINUTES)
    config = sessions.config_for(state.config, prefs)
    config["huddle"] = {"workflow_id": workflow.id, "agent_name": workflow.name}
    voice = prefs.get("voice")
    session = await sessions.start(
        organization_id=organization_id,
        user_id=user_id,
        thread_id=None,
        language=state.config.get("language"),
        voice=voice if config["voice_compatible"] else None,
        config=config,
    )
    notes = await record.notes_for(
        organization_id=organization_id, user_id=user_id, workflow_id=workflow.id
    )
    return {**session, "agent_name": workflow.name, "notes": notes}
