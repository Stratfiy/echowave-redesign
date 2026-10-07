"""The connect chip: how an outside tool or an ordering app gets connected.

Never "go to Settings": the chip is put on the thread where the need came
up, and connecting happens on it -- a server address and an optional token
typed into the chip, or a Sign in that opens the app's own screen in a new
tab. Nothing is connected by the chip appearing.

The chip names the person it was offered to; pressing it connects *the
person who presses it* (``POST /reach/connections``), so a colleague who
sees it on a shared thread can only ever connect their own account.
"""

from __future__ import annotations

from typing import Any

from api.enums import AgentEventActor, AgentEventKind
from api.services.workflow import agent_timeline

KIND = AgentEventKind.REACH_CONNECT_OFFERED.value


async def offer(
    *,
    organization_id: int,
    user_id: int | None,
    kind: str,
    provider: str,
    name: str,
    state: str,
    reason: str | None = None,
    server_url: str | None = None,
    why: str = "",
) -> dict[str, Any]:
    """Post one chip. Returns what the model is told."""
    await agent_timeline.record(
        organization_id=organization_id,
        kind=KIND,
        actor=AgentEventActor.AGENT.value,
        summary=f"Connect {name}"[:500],
        payload={
            "reach_kind": kind,
            "provider": provider,
            "name": name,
            "server_url": server_url,
            "state": state,
            "reason": reason,
            "why": (why or "")[:200],
            "offered_to": user_id,
            # The chip is the person's: a colleague's thread never shows it.
            "private_to": user_id,
        },
        in_channel=False,
    )
    if state == "needs_setup":
        return {
            "status": "needs_setup",
            "note": (
                f"{name} cannot be connected yet: {reason} A chip saying so is "
                "on the thread. Say that plainly; do not offer another way."
            ),
        }
    return {
        "status": "offered",
        "note": (
            f"A connect chip for {name} is on the thread. Connecting is theirs "
            f"to do on the chip. {name} is NOT connected yet; say so and end "
            "your reply."
        ),
    }


__all__ = ["KIND", "offer"]
