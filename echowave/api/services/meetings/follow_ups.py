"""A suggested meeting action, made real one card at a time.

Screen 12: "Edit owner, task and proposed time before confirmation. Creating
a reminder does not authorize email or a calendar invitation. Each external
write opens S08." And the acceptance line: "Confirming two actions creates
exactly two records."

So a suggestion does nothing on its own. Review turns it into an action card
(``actions.MEETING_FOLLOW_UP``) on the controls rail -- the same proposed,
armed, run-once, undo-window card every other act uses, with its payload
version when the task ledger is on. The card's one effect is one task on the
workspace's task board, made with an idempotency key per suggestion, so a
second run, a second tab or a retried job never makes a second task. The
card says, in its effect line, that nothing is sent to anybody: a follow-up
email or an invitation would be a different card with its own preview.

Only the person who captured the meeting can confirm its cards (enforced in
``actions.settle``), and the cards live on the meeting's own hidden thread,
never in a shared conversation.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from loguru import logger

from api.db import db_client
from api.services.meetings import is_meeting_thread, thread_for

#: Card states a person can still edit the suggestion behind.
_EDITABLE_CARD_STATES = (None, "declined", "cancelled", "undone", "failed")


class FollowUpError(ValueError):
    """Said to the person, as is."""


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat()


# --- the card, as actions.py sees it ------------------------------------------


async def resolve_card(
    organization_id: int, arguments: dict[str, Any]
) -> dict[str, Any]:
    """The card's payload, built from the stored suggestion -- never from the
    caller's words, so what a person approves is what is on the record."""
    from api.services.workflow import actions

    try:
        owner = int(arguments.get("owner_user_id"))
        item_id = int(arguments.get("item_id"))
    except (TypeError, ValueError):
        raise actions.ActionError("Say which meeting action.") from None
    meeting = await db_client.get_meeting(
        str(arguments.get("meeting") or ""),
        organization_id=organization_id,
        owner_user_id=owner,
    )
    if meeting is None:
        raise actions.ActionError("That meeting is not here.")
    item = await db_client.get_meeting_item(
        item_id, meeting_id=meeting.id, organization_id=organization_id
    )
    if item is None or item.kind != "action":
        raise actions.ActionError("That meeting action is not here.")
    organization = await db_client.get_organization_by_id(organization_id)
    workspace = getattr(organization, "name", None) or "this workspace"
    due = _iso(item.due_at)
    return {
        "action": actions.MEETING_FOLLOW_UP,
        "args": {
            "meeting": meeting.public_id,
            "meeting_title": meeting.title[:200],
            "item_id": item.id,
            "owner_user_id": owner,
            "task": item.text[:300],
            "owner_name": item.owner_name,
            "due_at": due,
            "due_text": item.due_text,
            "excerpt": (item.excerpt or "")[:300],
        },
        "label": f"Add a task: {item.text[:160]}",
        "why": f"From the meeting {meeting.title[:120]}",
        "effect": (
            f"Adds one task to the task board in {workspace}. Nothing is sent "
            "to anyone: no email, message or calendar invitation."
        ),
        "reversible": True,
        "state": actions.PROPOSED,
    }


async def execute(organization_id: int, payload: dict[str, Any]) -> str:
    """The card ran: make its one task. Returns the line the card shows."""
    from api.services.workflow import actions, task_ledger

    args = payload.get("args") or {}
    owner = int(args.get("owner_user_id") or 0)
    meeting = await db_client.get_meeting(
        str(args.get("meeting") or ""),
        organization_id=organization_id,
        owner_user_id=owner,
    )
    if meeting is None:
        raise actions.ActionError("That meeting was deleted, so nothing was added.")
    item = await db_client.get_meeting_item(
        int(args.get("item_id") or 0),
        meeting_id=meeting.id,
        organization_id=organization_id,
    )
    if item is None:
        raise actions.ActionError(
            "That meeting action was removed, so nothing was added."
        )
    due_at = datetime.fromisoformat(args["due_at"]) if args.get("due_at") else None
    brief = "\n".join(
        line
        for line in (
            f"From the meeting: {args.get('meeting_title') or meeting.title}",
            f"Owner, as said: {args.get('owner_name') or 'not named'}",
            f"When, as said: {args.get('due_text') or 'not said'}",
            f"Source: “{args.get('excerpt')}”" if args.get("excerpt") else "",
        )
        if line
    )
    key = f"meeting:{meeting.public_id}:item:{item.id}"
    if task_ledger.enabled(organization_id):
        task, _created = await task_ledger.create(
            organization_id=organization_id,
            title=str(args.get("task") or item.text),
            brief=brief,
            created_by=owner or None,
            idempotency_key=key,
            due_at=due_at,
        )
    else:
        existing = (
            await db_client.get_task(item.task_id, organization_id=organization_id)
            if item.task_id
            else None
        )
        task = existing or await db_client.create_task(
            organization_id=organization_id,
            title=str(args.get("task") or item.text)[:200],
            brief=brief,
            created_by=owner or None,
            status="todo",
            due_at=due_at,
        )
    await db_client.update_meeting_item(
        item.id, meeting_id=meeting.id, organization_id=organization_id, task_id=task.id
    )
    payload.setdefault("result", {})["task_id"] = task.id
    when = f", due {due_at.strftime('%d %b %Y %H:%M UTC')}" if due_at else ""
    return f"Added to the task board: {task.title}{when}."


async def reverse(organization_id: int, payload: dict[str, Any]) -> None:
    """Undo a done card: cancel its task, if nobody has started it."""
    from api.services.workflow import actions

    task_id = (payload.get("result") or {}).get("task_id")
    if not task_id:
        raise actions.ActionError("There is no task to take back.")
    task = await db_client.get_task(int(task_id), organization_id=organization_id)
    if task is None:
        return
    if task.status not in ("todo", "backlog"):
        raise actions.ActionError("That task has already been worked on, so it stays.")
    await cancel_task(organization_id, task, reason_code="meeting_card_undone")


async def cancel_task(organization_id: int, task: Any, *, reason_code: str) -> None:
    """Cancel a task a meeting card made, through the ledger when it holds
    the task, so its state and its board column agree."""
    from api.services.workflow import task_ledger

    if task_ledger.enabled(organization_id) and getattr(task, "ledger_state", None):
        await task_ledger.transition(
            organization_id=organization_id,
            task_id=task.id,
            to_state=task_ledger.CANCELLED,
            expected_version=int(task.state_version or 0),
            reason_code=reason_code,
        )
    else:
        await db_client.update_task(
            task.id, organization_id=organization_id, status="cancelled"
        )


def owner_of(payload: dict[str, Any] | None) -> int | None:
    try:
        return int(((payload or {}).get("args") or {}).get("owner_user_id"))
    except (TypeError, ValueError):
        return None


# --- the person's half ---------------------------------------------------------


async def card_of(organization_id: int, item: Any) -> Any | None:
    if not item.card_event_id:
        return None
    event = await db_client.get_agent_event(
        item.card_event_id, organization_id=organization_id
    )
    if event is None or not is_meeting_thread(getattr(event, "thread_id", None)):
        return None
    return event


def card_view(event: Any | None) -> dict[str, Any] | None:
    if event is None:
        return None
    payload = dict(event.payload or {})
    return {
        "event_id": event.id,
        "state": payload.get("state"),
        "version": payload.get("version"),
        "revision": len(payload.get("revisions") or []) + 1,
        "label": payload.get("label"),
        "effect": payload.get("effect"),
        "fires_at": payload.get("fires_at"),
        "error": payload.get("error"),
        "done_note": (payload.get("done") or {}).get("note"),
        "task_id": (payload.get("result") or {}).get("task_id"),
        "args": {
            key: (payload.get("args") or {}).get(key)
            for key in ("task", "owner_name", "due_at", "due_text", "excerpt")
        },
    }


async def propose(*, user_id: int, meeting: Any, item: Any) -> Any:
    """Put the suggestion on an action card. Returns the card's row."""
    from api.services import acting
    from api.services.workflow import actions, agent_timeline

    if item.kind != "action":
        raise FollowUpError("Only a suggested action can be confirmed.")
    current = await card_of(meeting.organization_id, item)
    state = (current.payload or {}).get("state") if current is not None else None
    if current is not None and state not in _EDITABLE_CARD_STATES:
        # Already waiting, armed or done: the same card, never a second one.
        return current
    thread = thread_for(meeting.public_id)
    with acting.acting_as(user_id), agent_timeline.in_thread(thread):
        with agent_timeline.collecting() as written:
            result = await actions.propose(
                organization_id=meeting.organization_id,
                workflow_id=None,
                workflow_run_id=None,
                arguments={
                    "action": actions.MEETING_FOLLOW_UP,
                    "meeting": meeting.public_id,
                    "item_id": item.id,
                    "owner_user_id": user_id,
                },
                in_channel=False,
            )
    event_id = next(
        (eid for kind, eid, _ in written if kind == "action_proposed"), None
    )
    if event_id is None and result.get("status") == "already_proposed":
        event_id = await _waiting_card(meeting, item)
    if event_id is None:
        raise FollowUpError(str(result.get("reason") or "This could not be proposed."))
    await db_client.update_meeting_item(
        item.id,
        meeting_id=meeting.id,
        organization_id=meeting.organization_id,
        card_event_id=event_id,
    )
    return await db_client.get_agent_event(
        event_id, organization_id=meeting.organization_id
    )


async def _waiting_card(meeting: Any, item: Any) -> int | None:
    from api.enums import AgentEventKind

    rows = await db_client.agent_events(
        organization_id=meeting.organization_id,
        kinds=[AgentEventKind.ACTION_PROPOSED.value],
        assistant_thread=True,
        thread_id=thread_for(meeting.public_id),
        limit=50,
    )
    for row in rows or []:
        args = (row.payload or {}).get("args") or {}
        if (
            args.get("item_id") == item.id
            and (row.payload or {}).get("state") == "proposed"
        ):
            return row.id
    return None


async def settle(
    *, user_id: int, meeting: Any, item: Any, verb: str, version: str | None
) -> Any:
    """Confirm, decline or undo the suggestion's card."""
    from api.services import events
    from api.services.workflow import actions

    card = await card_of(meeting.organization_id, item)
    if card is None:
        raise FollowUpError("Review this action first.")
    try:
        payload = await actions.settle(
            organization_id=meeting.organization_id,
            event_id=card.id,
            verb=verb,
            user_id=user_id,
            version=version,
        )
    except actions.ActionError as exc:
        raise FollowUpError(str(exc)) from exc
    if verb == "confirm":
        await events.emit(
            "action_confirmed",
            user_id=user_id,
            organization_id=meeting.organization_id,
            task_id=f"card:{card.id}",
            configuration_version=payload.get("version"),
            properties={"status": payload.get("state")},
        )
    return await db_client.get_agent_event(
        card.id, organization_id=meeting.organization_id
    )


async def edit(
    *, user_id: int, meeting: Any, item: Any, changes: dict[str, Any]
) -> Any:
    """Change task, owner or time before confirming. A card already waiting
    is declined first: an edit never carries an old approval."""
    from api.services.workflow import actions

    if item.kind != "action":
        raise FollowUpError("Only a suggested action can be edited.")
    card = await card_of(meeting.organization_id, item)
    state = (card.payload or {}).get("state") if card is not None else None
    if state not in _EDITABLE_CARD_STATES and state != actions.PROPOSED:
        raise FollowUpError(
            "This one is already confirmed. Undo it on its card before editing."
        )
    if state == actions.PROPOSED:
        try:
            await actions.settle(
                organization_id=meeting.organization_id,
                event_id=card.id,
                verb="decline",
                user_id=user_id,
            )
        except actions.ActionError as exc:
            logger.info("Card {} moved on before the edit: {}", card.id, exc)
            raise FollowUpError(
                "This changed while you were editing. Look again."
            ) from exc
    fields: dict[str, Any] = {"edited": True, "card_event_id": None}
    if "text" in changes:
        words = str(changes.get("text") or "").strip()
        if not words:
            raise FollowUpError("Say what the task is.")
        fields["text"] = words[:300]
    if "owner_name" in changes:
        fields["owner_name"] = (
            (str(changes.get("owner_name") or "").strip()[:120]) or None
        )
    if "due_at" in changes:
        fields["due_at"] = changes.get("due_at")
    if "due_text" in changes:
        fields["due_text"] = (str(changes.get("due_text") or "").strip()[:120]) or None
    owner = fields.get("owner_name", item.owner_name)
    due = fields.get("due_at", item.due_at)
    fields["missing"] = [
        name for name, absent in (("owner", not owner), ("due", due is None)) if absent
    ]
    return await db_client.update_meeting_item(
        item.id,
        meeting_id=meeting.id,
        organization_id=meeting.organization_id,
        **fields,
    )
