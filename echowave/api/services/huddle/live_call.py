"""During a live call, the huddle is that call's whisper channel.

When the agent is on a live call while its operator has a huddle open, what
the operator says in the huddle -- spoken and transcribed, or typed -- goes
to that call as a whisper (``live_supervision.registry.send_whisper``): into
the agent's context on the call, labelled with who sent it, never to the
caller, in the audit log like every whisper. The huddle says so, "This call
only", for as long as it lasts; the agent in the huddle answers with a short
acknowledgement instead of a teammate's reply.

Every wall of live supervision applies unchanged: the ``live_supervision``
flag on for the workspace, "Allow live listening" on, and this person an
admin or the agent's owner. If any is missing, or the agent has no live call,
the huddle is an ordinary huddle. With more than one live call nothing is
chosen for the operator -- a whisper sent to the wrong caller's call is worse
than none -- and the huddle says how many there are.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

from loguru import logger

from api.db import db_client
from api.services import features

FLAG = "live_supervision"
#: The huddle's mark while it whispers to a call.
THIS_CALL_ONLY = "This call only"
#: What the agent says in the huddle once a line has gone to the call.
SENT = "Passed to the call."


class NoLiveCall(Exception):
    """Nothing to whisper to: no live call, several, or not allowed."""

    def __init__(self, detail: str):
        super().__init__(detail)
        self.detail = detail


@dataclass(frozen=True)
class LiveTarget:
    run_id: int
    direction: str
    caller: str | None
    started_at: str
    label: str = THIS_CALL_ONLY

    def as_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "direction": self.direction,
            "caller": self.caller,
            "started_at": self.started_at,
            "label": self.label,
        }


async def _viewer(organization_id: int, user_id: int) -> Any:
    from api.services.live_supervision import access

    user = await db_client.get_user_by_id(user_id)
    if user is None:
        return None
    # ``viewer_for`` reads the workspace from the session's selection; a
    # huddle is already scoped to one.
    return await access.viewer_for(
        SimpleNamespace(
            id=user.id, email=user.email, selected_organization_id=organization_id
        )
    )


async def calls(
    *, organization_id: int, user_id: int, workflow_id: int
) -> tuple[list[Any], Any]:
    """(the agent's live calls this person may whisper to, the viewer)."""
    if not features.is_on(FLAG, organization_id):
        return [], None
    from api.services.live_supervision import registry

    viewer = await _viewer(organization_id, user_id)
    if viewer is None:
        return [], None
    found, _allowed, _more = await registry.live_calls(viewer, workflow_id=workflow_id)
    return [c for c in found if c.can_listen], viewer


async def find(
    *, organization_id: int, user_id: int, workflow_id: int
) -> tuple[LiveTarget | None, int]:
    """(the one live call the huddle whispers to, how many there are).
    Never raises: a huddle that cannot tell is an ordinary huddle."""
    try:
        live, _viewer_ = await calls(
            organization_id=organization_id, user_id=user_id, workflow_id=workflow_id
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Huddle could not look for a live call: {}", exc)
        return None, 0
    if len(live) != 1:
        return None, len(live)
    call = live[0]
    return (
        LiveTarget(
            run_id=call.run_id,
            direction=call.direction,
            caller=call.caller,
            started_at=call.started_at,
        ),
        1,
    )


async def whisper(
    *, organization_id: int, user_id: int, workflow_id: int, text: str
) -> dict[str, Any]:
    """Send one line to the agent's live call as a whisper. Raises
    ``NoLiveCall`` when there is no single call to send it to, and live
    supervision's own ``NotLive``/``Refused`` from the send."""
    from api.services.live_supervision import registry

    live, viewer = await calls(
        organization_id=organization_id, user_id=user_id, workflow_id=workflow_id
    )
    if not live:
        raise NoLiveCall("The agent isn't on a call you can whisper to.")
    if len(live) > 1:
        raise NoLiveCall(
            f"The agent is on {len(live)} calls. Open one from the live list "
            "to whisper to it."
        )
    sent = await registry.send_whisper(viewer, live[0].run_id, text=text, urgent=False)
    return {**sent, "run_id": live[0].run_id}


__all__ = [
    "LiveTarget",
    "NoLiveCall",
    "SENT",
    "THIS_CALL_ONLY",
    "calls",
    "find",
    "whisper",
]
