"""The API side of a take-over: join, switch, let the agent answer, hand back.

Every act goes through live supervision's own checks first
(``live_supervision.registry.require_listener``): a run outside the
workspace is ``NotFound``, a call that has ended is ``NotLive``, somebody
who may not listen is ``Refused``. Then this module's: "Allow supervisors
to join calls" on, and nobody else already on the call.

The worker running the call is the authority on who has it. The API claims
the call (``SET NX`` on the state key, so two people pressing Join at once
cannot both get it), then publishes the command; a publish that reaches
nobody means no worker is taking commands for the call -- it ended, or it
started before the feature was on -- and the claim is withdrawn. Every
join, switch and hand-back is in the audit log, with who and which call.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from api.services.live_supervision import access as listen_access
from api.services.live_supervision import registry as live_registry
from api.services.live_takeover import access, bridge, channels
from api.services.live_takeover.gates import BARGE, TAKEOVER
from api.services.workflow import audit_log

NotFound = live_registry.NotFound
NotLive = live_registry.NotLive
Refused = live_registry.Refused


class Conflict(Exception):
    """Somebody else has the call, or the act does not fit its state."""

    def __init__(self, detail: str):
        super().__init__(detail)
        self.detail = detail


@dataclass
class Takeover:
    run_id: int
    #: ``ai`` while nobody has joined, else ``barge`` or ``takeover``.
    mode: str
    by: str | None
    by_user_id: int | None
    since: str | None
    agent_answering: bool
    #: This viewer is the one on the call.
    mine: bool
    #: Whether this viewer could join (or take the call over) now, and why not.
    can_join: bool
    blocked: str | None
    allow_joining: bool
    can_change_setting: bool
    #: ``pipeline`` (speak from the browser) or ``plivo_mpc`` (by phone).
    bridge: str
    needs_phone: bool
    recovery_seconds: float

    def as_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


async def read_state(run_id: int) -> dict[str, Any] | None:
    raw = await channels.redis().get(channels.state_key(run_id))
    if not raw:
        return None
    try:
        state = json.loads(raw)
    except ValueError:
        return None
    return state if isinstance(state, dict) and state.get("mode") else None


async def _checked(viewer: listen_access.Viewer, run_id: int):
    """The call, if this person may join it; raises otherwise."""
    call = await live_registry.require_listener(viewer, run_id)
    if not await access.allow_joining(viewer.organization_id):
        raise Refused(access.JOINING_OFF)
    return call


async def describe(viewer: listen_access.Viewer, run_id: int) -> Takeover:
    from api import constants

    call = await live_registry.require_listener(viewer, run_id)
    allow_listening = await listen_access.allow_listening(viewer.organization_id)
    allow_joining = await access.allow_joining(viewer.organization_id)
    blocked = access.blocked_reason(
        viewer,
        call.agent_owner_id,
        allow_listening=allow_listening,
        allow_joining=allow_joining,
    )
    state = await read_state(run_id) or {}
    holder = state.get("by_user_id")
    mine = holder is not None and holder == viewer.user_id
    if blocked is None and holder is not None and not mine:
        blocked = "taken"
    name = bridge.configured()
    return Takeover(
        run_id=run_id,
        mode=str(state.get("mode") or "ai"),
        by=state.get("by"),
        by_user_id=holder,
        since=state.get("since"),
        agent_answering=bool(state.get("agent_answering")),
        mine=mine,
        can_join=blocked is None,
        blocked=blocked,
        allow_joining=allow_joining,
        can_change_setting=viewer.is_admin,
        bridge=name,
        needs_phone=name == bridge.PLIVO_MPC,
        recovery_seconds=float(constants.LIVE_TAKEOVER_RECOVERY_SECONDS),
    )


async def _publish(run_id: int, command: dict[str, Any]) -> int:
    return await channels.redis().publish(
        channels.command_channel(run_id), json.dumps(command)
    )


def _taken_by(state: dict[str, Any]) -> Conflict:
    return Conflict(f"{state.get('by') or 'Someone'} is already on this call.")


def _mask_phone(phone: str | None) -> str | None:
    return listen_access.mask_number(phone) if phone else None


async def join(
    viewer: listen_access.Viewer, run_id: int, *, mode: str, phone: str | None = None
) -> dict[str, Any]:
    """Join the call in ``mode``, or switch to it if already on it."""
    if mode not in (BARGE, TAKEOVER):
        raise Conflict("Choose barge or take over.")
    call = await _checked(viewer, run_id)
    name = bridge.configured()
    if name == bridge.PLIVO_MPC and not phone:
        raise Conflict("A phone number to call you on is needed.")
    r = channels.redis()
    key = channels.state_key(run_id)
    state = await read_state(run_id)
    if state and state.get("by_user_id") != viewer.user_id:
        raise _taken_by(state)
    switching = state is not None
    if switching and state.get("mode") == mode:
        return state
    claim = {
        "mode": mode,
        "by": viewer.name,
        "by_user_id": viewer.user_id,
        "since": (state or {}).get("since"),
        "agent_answering": False,
        "bridge": name,
    }
    if switching:
        await r.set(key, json.dumps(claim), ex=channels.STATE_TTL_SECONDS, xx=True)
    elif not await r.set(
        key, json.dumps(claim), ex=channels.STATE_TTL_SECONDS, nx=True
    ):
        raise _taken_by(await read_state(run_id) or {})
    command = {
        "kind": "join",
        "mode": mode,
        "by": viewer.name,
        "by_user_id": viewer.user_id,
    }
    if phone:
        command["phone"] = phone
    if not await _publish(run_id, command):
        if not switching:
            await r.delete(key)
        raise NotLive()
    await audit_log.record(
        viewer.organization_id,
        action="call_taken_over" if mode == TAKEOVER else "call_barged",
        subject_kind="call",
        subject_id=run_id,
        subject=f"{call.agent_name} call",
        actor_user_id=viewer.user_id,
        actor=viewer.name,
        before={"mode": (state or {}).get("mode", "ai")},
        after={"mode": mode, "bridge": name, "phone": _mask_phone(phone)},
    )
    return claim


async def let_agent_answer(viewer: listen_access.Viewer, run_id: int) -> None:
    await _checked(viewer, run_id)
    state = await read_state(run_id)
    if not state or state.get("by_user_id") != viewer.user_id:
        raise Conflict("You are not on this call.")
    if state.get("mode") != BARGE:
        raise Conflict("The agent stays quiet while you have taken the call over.")
    command = {"kind": "let_agent_answer", "by_user_id": viewer.user_id}
    if not await _publish(run_id, command):
        raise NotLive()


async def hand_back(viewer: listen_access.Viewer, run_id: int) -> dict[str, Any]:
    """Give the call back to the agent. The person on it can; so can an
    admin, for a supervisor who walked away from a call they had."""
    call = await live_registry.require_listener(viewer, run_id)
    state = await read_state(run_id)
    if not state:
        raise Conflict("Nobody is on this call; the agent has it.")
    if state.get("by_user_id") != viewer.user_id and not viewer.is_admin:
        raise Conflict(f"{state.get('by') or 'Someone'} is on this call.")
    command = {"kind": "hand_back", "by": viewer.name, "by_user_id": viewer.user_id}
    if not await _publish(run_id, command):
        await channels.redis().delete(channels.state_key(run_id))
        raise NotLive()
    await channels.redis().delete(channels.state_key(run_id))
    await audit_log.record(
        viewer.organization_id,
        action="call_handed_back",
        subject_kind="call",
        subject_id=run_id,
        subject=f"{call.agent_name} call",
        actor_user_id=viewer.user_id,
        actor=viewer.name,
        before={"mode": state.get("mode"), "by": state.get("by")},
        after={"mode": "ai"},
    )
    return {"mode": "ai"}


async def require_talker(viewer: listen_access.Viewer, run_id: int):
    """For the talking socket: the call, if this person has it."""
    call = await _checked(viewer, run_id)
    state = await read_state(run_id)
    if not state or state.get("by_user_id") != viewer.user_id:
        raise Conflict("Join the call first.")
    return call


async def still_holds(viewer: listen_access.Viewer, run_id: int) -> bool:
    state = await read_state(run_id)
    return bool(state) and state.get("by_user_id") == viewer.user_id


async def ping(viewer: listen_access.Viewer, run_id: int) -> int:
    return await _publish(run_id, {"kind": "ping", "by_user_id": viewer.user_id})


async def forward_mic(run_id: int, packet: bytes) -> bool:
    if not channels.valid_mic_packet(packet):
        return False
    await channels.redis().publish(channels.mic_channel(run_id), bytes(packet))
    return True
