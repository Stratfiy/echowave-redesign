"""Approval cards for steps Decibyl takes on a person's own computer.

The desktop app (``echowave/desktop``) runs the "work on my computer" loop on
the person's machine. Screenshots and actions never come here. What does
come here is the one thing the platform will not leave to a model: a step
that sends, pays, deletes or submits. The desktop holds that step and
proposes it as a card -- the same card, the same contract, as every other
action in ``actions.py``:

1. **Proposed.** The card shows one plain sentence and the exact detail the
   desktop wrote from the held step, and which app on which computer.
2. **Armed.** The person pressed Do it; the undo window runs as for any card.
3. **Released.** The window passed (``actions.run``). Nothing ran here: the
   computer may now take the step, and only that step.
4. **Running.** The computer claimed it -- a compare-and-swap from released,
   checked against the fingerprint of the step the person saw. A second
   claim, a retried request, or a step that differs by one pixel or one
   character gets nothing.
5. **Done / failed / outcome unknown.** What the computer reported. A step
   the computer took and never reported on is swept to ``outcome_unknown``
   by ``actions.sweep_stale_running`` (task ledger), like any card whose job
   died, and is never run again. A released step no computer took is swept
   to cancelled by ``sweep_unclaimed``: nothing was done, and the card says so.

With the task ledger on, the card carries the payload version controls
binds approvals to: Confirm must name it, and ``actions.run`` re-checks it
before releasing. A ``send`` step spends the confirming person's outbound
messages quota, as every send does (``actions._is_outbound``).

RELEASED and the fingerprint claim are this module's own: every other card
is executed by ``actions.run`` on the server, so controls has no state for
"approved, waiting for the person's computer to take it", and no other
executor that must prove it runs the exact step that was approved.

Only the person whose computer it is can answer the card or claim it
(``args.user_id``); a teammate reading the thread cannot press Do it on
somebody else's screen.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

from api.db import db_client
from api.enums import AgentEventKind
from api.services.workflow import actions, agent_timeline

KINDS = ("send", "pay", "delete", "submit")
_VERB = {"send": "Send", "pay": "Pay", "delete": "Delete", "submit": "Submit"}
_FINGERPRINT = re.compile(r"^[0-9a-f]{64}$")
MAX_SUMMARY = 200
MAX_DETAIL = 500
MAX_APP = 80


class DesktopStepError(ValueError):
    """A step the card cannot carry; the message is what the desktop shows."""


def _clean(value: Any, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


def resolve(arguments: dict[str, Any], *, why: str = "") -> dict[str, Any]:
    """The card's payload, from what the desktop sent. Called by
    ``actions.resolve`` so a desktop step is a card like any other."""
    kind = str(arguments.get("kind") or "").strip().lower()
    if kind not in KINDS:
        raise DesktopStepError("A desktop step must send, pay, delete or submit.")
    app = _clean(arguments.get("app"), MAX_APP)
    summary = _clean(arguments.get("summary"), MAX_SUMMARY)
    detail = _clean(arguments.get("detail"), MAX_DETAIL)
    fingerprint = str(arguments.get("fingerprint") or "").strip().lower()
    user_id = int(arguments.get("user_id") or 0)
    if not app or not summary or not detail:
        raise DesktopStepError("Say which app, what it does and the exact detail.")
    if not _FINGERPRINT.match(fingerprint):
        raise DesktopStepError("The step has no fingerprint.")
    if not user_id:
        raise DesktopStepError("Nobody's computer.")
    step = arguments.get("step") or {}
    step_name = _clean(step.get("name") if isinstance(step, dict) else "", 40)
    return {
        "action": actions.DESKTOP_STEP,
        "args": {
            "user_id": user_id,
            "kind": kind,
            "app": app,
            "device": _clean(arguments.get("device"), 80),
            "session_id": _clean(arguments.get("session_id"), 64),
            "request_id": _clean(arguments.get("request_id"), 64),
            "step_name": step_name,
            "fingerprint": fingerprint,
        },
        # The sentence the design asks for: "Decibyl wants to: ...".
        "label": summary,
        "why": why,
        # The exact detail line: the item, the recipient, the amount, as the
        # desktop read it from the held step. Shown while the buttons are.
        "preview": detail,
        "effect": (
            f"{_VERB[kind]} in {app} on your computer, once. Nothing else is "
            "done until you answer."
        ),
        "reversible": False,
        "state": actions.PROPOSED,
    }


async def _waiting(organization_id: int, payload: dict[str, Any]) -> Any | None:
    return await actions._already_proposed(
        organization_id=organization_id,
        workflow_id=None,
        payload=payload,
        in_channel=False,
    )


async def propose(
    *,
    organization_id: int,
    user_id: int,
    thread_id: str | None,
    arguments: dict[str, Any],
) -> int:
    """Put the card on the person's Decibyl thread. Returns its id.

    A retried request with the same ``request_id`` finds the card already
    waiting and gets the same id, so one held step is one card.
    """
    arguments = {**arguments, "action": actions.DESKTOP_STEP, "user_id": user_id}
    with agent_timeline.in_thread(thread_id):
        try:
            payload = await actions.resolve(
                organization_id=organization_id, workflow_id=None, arguments=arguments
            )
        except actions.ActionError as exc:
            raise DesktopStepError(str(exc)) from exc
        waiting = await _waiting(organization_id, payload)
        if waiting is not None:
            return int(waiting.id)
        if actions._ledger_on(organization_id):
            # The exact act a Confirm will approve, as actions.propose stamps.
            payload["version"] = actions.payload_version(payload)
        with agent_timeline.collecting() as rows:
            await agent_timeline.record(
                organization_id=organization_id,
                kind=AgentEventKind.ACTION_PROPOSED.value,
                summary=payload["label"],
                payload=payload,
                in_channel=False,
            )
    for kind, event_id, _ in rows:
        if kind == AgentEventKind.ACTION_PROPOSED.value and event_id:
            return int(event_id)
    raise DesktopStepError("The card could not be written. Try again.")


async def _own_step(organization_id: int, user_id: int, event_id: int) -> Any:
    event = await db_client.get_agent_event(event_id, organization_id=organization_id)
    payload = dict(getattr(event, "payload", None) or {})
    if (
        event is None
        or event.kind != AgentEventKind.ACTION_PROPOSED.value
        or payload.get("action") != actions.DESKTOP_STEP
        or int((payload.get("args") or {}).get("user_id") or 0) != user_id
    ):
        # One answer for "not here" and "not yours": an id somebody typed
        # tells them nothing about another person's computer.
        raise DesktopStepError("That step is not here.")
    return event


async def state(*, organization_id: int, user_id: int, event_id: int) -> dict[str, Any]:
    event = await _own_step(organization_id, user_id, event_id)
    payload = dict(event.payload or {})
    return {
        "event_id": event.id,
        "state": payload.get("state") or actions.PROPOSED,
        "error": payload.get("error"),
    }


async def _cas(event: Any, from_state: str, payload: dict[str, Any]) -> bool:
    """Compare-and-swap through actions._move, which also stamps the card's
    task-ledger state. False when somebody moved it first."""
    try:
        await actions._move(event, from_state, payload)
    except actions.ActionError:
        return False
    return True


async def claim(
    *, organization_id: int, user_id: int, event_id: int, fingerprint: str
) -> bool:
    """The computer takes the released step. True exactly once, and only for
    the step the person approved."""
    event = await _own_step(organization_id, user_id, event_id)
    payload = dict(event.payload or {})
    if payload.get("state") != actions.RELEASED:
        return False
    if (payload.get("args") or {}).get("fingerprint") != str(fingerprint).lower():
        return False
    payload["state"] = actions.RUNNING
    payload["claimed"] = {"by": user_id, "at": datetime.now(UTC).isoformat()}
    return await _cas(event, actions.RELEASED, payload)


async def report(
    *,
    organization_id: int,
    user_id: int,
    event_id: int,
    ok: bool | None,
    note: str,
) -> dict[str, Any]:
    """What happened on the computer. Only a claimed step can be reported.

    ``ok=None`` is the computer saying it does not know -- the action broke
    part-way -- which is ``outcome_unknown``, never a failure it could retry.
    """
    event = await _own_step(organization_id, user_id, event_id)
    payload = dict(event.payload or {})
    if payload.get("state") != actions.RUNNING or not payload.get("claimed"):
        raise DesktopStepError("That step was not taken.")
    note = _clean(note, 300)
    if ok:
        payload["state"] = actions.DONE
        payload["done"] = {
            "at": datetime.now(UTC).isoformat(),
            "note": f"Done on your computer: {note}"
            if note
            else "Done on your computer.",
        }
    elif ok is None:
        from api.services.workflow import task_ledger

        payload["state"] = actions.OUTCOME_UNKNOWN
        payload["error"] = task_ledger.UNKNOWN_COPY
        payload["reason_code"] = "desktop_unsure"
    else:
        payload["state"] = actions.FAILED
        payload["error"] = note or "It did not work on your computer."
    if not await _cas(event, actions.RUNNING, payload):
        raise DesktopStepError("Already reported.")
    await actions._emit(
        "task_completed" if ok else "task_failed", event, payload, user_id
    )
    return {"event_id": event.id, "state": payload["state"]}


async def cancel(*, organization_id: int, user_id: int, event_id: int) -> str:
    """Stop was pressed on the computer while the card waited. Returns the
    state the card ended in; a step already taken is left as it is."""
    event = await _own_step(organization_id, user_id, event_id)
    payload = dict(event.payload or {})
    current = payload.get("state") or actions.PROPOSED
    if current == actions.PROPOSED:
        await actions.settle(
            organization_id=organization_id,
            event_id=event.id,
            verb="decline",
            user_id=user_id,
        )
        return actions.DECLINED
    if current == actions.ARMED:
        await actions.settle(
            organization_id=organization_id,
            event_id=event.id,
            verb="undo",
            user_id=user_id,
        )
        return actions.CANCELLED
    if current == actions.RELEASED:
        payload["state"] = actions.CANCELLED
        payload["cancelled"] = {"by": user_id, "at": datetime.now(UTC).isoformat()}
        if await _cas(event, actions.RELEASED, payload):
            return actions.CANCELLED
    return current


#: A released step no computer has taken after this long is not going to be
#: taken: the app was quit, the computer slept, Stop was pressed offline.
UNCLAIMED_MINUTES = 10


async def sweep_unclaimed(now: datetime | None = None) -> int:
    """Released steps nobody took become cancelled, with a line saying
    nothing was done -- "Your computer will do this once" must not stay on
    a card for something that will never happen. Returns how many."""
    from datetime import timedelta

    from sqlalchemy import text as sql

    from api.services import features

    if not features.on_anywhere("desktop_computer_use"):
        return 0
    now = now or datetime.now(UTC)
    cutoff = now - timedelta(minutes=UNCLAIMED_MINUTES)
    async with db_client.async_session() as session:
        rows = (
            await session.execute(
                sql(
                    "SELECT id, organization_id FROM agent_events "
                    "WHERE kind = :kind AND payload->>'state' = :state "
                    "AND payload->>'action' = :action ORDER BY id LIMIT 500"
                ),
                {
                    "kind": AgentEventKind.ACTION_PROPOSED.value,
                    "state": actions.RELEASED,
                    "action": actions.DESKTOP_STEP,
                },
            )
        ).all()
    swept = 0
    for event_id, organization_id in rows:
        event = await db_client.get_agent_event(
            event_id, organization_id=organization_id
        )
        if event is None:
            continue
        payload = dict(event.payload or {})
        try:
            released = datetime.fromisoformat((payload.get("released") or {})["at"])
        except (KeyError, TypeError, ValueError):
            continue
        if released > cutoff:
            continue
        payload["state"] = actions.CANCELLED
        payload["error"] = "Your computer did not take this in time. Nothing was done."
        payload["reason_code"] = "desktop_unclaimed"
        if await _cas(event, actions.RELEASED, payload):
            swept += 1
    return swept


async def post_receipt(
    *, organization_id: int, thread_id: str | None, text: str
) -> None:
    """The receipt, as one line from Decibyl on the person's thread. Text
    only: the desktop never sends a screenshot here."""
    from api.services.workflow import decibyl

    body = str(text or "").strip()[:4000]
    if not body:
        raise DesktopStepError("Nothing to post.")
    with agent_timeline.in_thread(thread_id):
        await agent_timeline.record(
            organization_id=organization_id,
            kind=AgentEventKind.MESSAGE.value,
            summary=body.splitlines()[0][:255],
            payload={"body": body, "from": decibyl.NAME, "desktop_receipt": True},
            in_channel=False,
        )
