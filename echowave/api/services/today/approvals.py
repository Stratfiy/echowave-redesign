"""Pending approvals a person may see, and the exact preview of one.

Screen 08 ("Exact action approval") and the approval dock above the
composer both read from here. Neither has its own approval logic: a card is
the controls stream's ``action_proposed`` row (services/workflow/actions.py),
Do it / Approve is ``actions.settle(verb="confirm", version=...)`` and Don't
is ``decline``. The version a person is shown is the version they approve;
the card's own compare-and-swap keeps "the same approval from two places runs
once".

**Who sees a card.** A card on an agent's thread or a channel is the
workspace's, as it always was. A card on a Decibyl conversation follows that
conversation's privacy (D-1b): with private threads on, it is its author's,
and one with no author on record is an Admin's -- the same rule the thread
list uses (routes/agent_timeline.py). A card someone else may not see is
answered as not found.
"""

from __future__ import annotations

import re
from typing import Any

from sqlalchemy import and_, func, select

from api import constants
from api.db import db_client
from api.db.models import AgentEventModel
from api.enums import AgentEventKind
from api.services.today.scope import NotFound, Viewer
from api.services.workflow import actions

#: How many pending cards the queue looks at. A person with more than this
#: waiting has a different problem; the count still comes from the query.
QUEUE_LIMIT = 100

#: Payload keys, in order, that name who an act reaches.
_RECIPIENT_KEYS = (
    "to",
    "recipient",
    "recipient_email",
    "recipients",
    "email",
    "phone",
    "phone_number",
    "channel_id",
    "chat_id",
)
#: Payload keys, in order, that hold what is sent or written.
_CONTENT_KEYS = ("subject", "body", "message", "text", "content", "note", "description")
#: Payload keys that name an account; shown with all but four digits masked.
_ACCOUNT_KEYS = ("account", "account_number", "from_account", "upi", "vpa", "iban")
_AMOUNT_KEYS = ("amount", "amount_inr", "total")
_ATTACHMENT_KEYS = ("attachments", "attachment", "files", "file_name")

#: Screen 08's states, from a card's own state.
SCREEN_STATE = {
    actions.PROPOSED: "pending",
    actions.ARMED: "approved",
    actions.RUNNING: "executing",
    actions.DONE: "completed",
    actions.FAILED: "failed",
    actions.OUTCOME_UNKNOWN: "outcome_unknown",
    actions.DECLINED: "cancelled",
    actions.CANCELLED: "cancelled",
    actions.UNDONE: "cancelled",
    # A desktop step past its undo window: handed to the person's computer,
    # which takes it once. Executing, not done.
    actions.RELEASED: "executing",
}


def mask_digits(value: str) -> str:
    """Every run of five or more digits shown as its last four: an account
    number is recognisable to its owner and useless to a shoulder-surfer."""
    return re.sub(r"\d{5,}", lambda m: "•••• " + m.group(0)[-4:], value)


def _as_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        return ", ".join(_as_text(v) for v in value if v not in (None, ""))
    if isinstance(value, dict):
        return ", ".join(f"{k}: {_as_text(v)}" for k, v in value.items())
    return str(value)


def _first(args: dict[str, Any], keys: tuple[str, ...]) -> str:
    for key in keys:
        text = _as_text(args.get(key)).strip()
        if text:
            return text
    return ""


def _rupees(value: Any) -> str:
    try:
        number = float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return _as_text(value)
    whole = int(number)
    # Indian grouping: 1,23,45,678.
    digits = str(abs(whole))
    head, tail = digits[:-3], digits[-3:]
    groups = []
    while len(head) > 2:
        groups.insert(0, head[-2:])
        head = head[:-2]
    if head:
        groups.insert(0, head)
    grouped = ",".join([*groups, tail]) if groups else tail
    paise = round((abs(number) - abs(whole)) * 100)
    sign = "-" if number < 0 else ""
    return f"{sign}₹{grouped}" + (f".{paise:02d}" if paise else "")


def describe(payload: dict[str, Any], *, actor: str = "Decibyl") -> dict[str, Any]:
    """The exact preview of a card: one sentence, one line of detail, and
    every field in full. Nothing is truncated -- a hidden recipient, amount
    or attachment is exactly what an approval must not hide (screen 08)."""
    action = str(payload.get("action") or "")
    args = dict(payload.get("args") or {})
    label = str(payload.get("label") or action.replace("_", " ")).strip()
    inner = args.get("arguments") if action == actions.RUN_TOOL else None
    fields = dict(inner) if isinstance(inner, dict) else args

    recipient = _first(fields, _RECIPIENT_KEYS)
    account = mask_digits(_first(fields, _ACCOUNT_KEYS))
    amount_raw = _first(fields, _AMOUNT_KEYS)
    amount = _rupees(amount_raw) if amount_raw else ""
    attachments: list[str] = []
    for key in _ATTACHMENT_KEYS:
        value = fields.get(key)
        if isinstance(value, (list, tuple)):
            attachments.extend(_as_text(v) for v in value if v)
        elif value:
            attachments.append(_as_text(value))
    content_parts = [
        f"{key.capitalize()}: {_as_text(fields.get(key))}"
        for key in _CONTENT_KEYS
        if _as_text(fields.get(key)).strip()
    ]
    app = ""
    timing = f"Runs {actions.UNDO_WINDOW_SECONDS} seconds after you approve; you can undo until then."

    if action == actions.RUN_TOOL:
        app = str(args.get("toolkit") or "").strip()
    elif action == actions.SEND_DOCUMENT:
        recipient = str(args.get("to") or recipient)
        app = str(args.get("channel") or "")
        if args.get("name"):
            attachments = [str(args["name"])]
        content_parts = [f"Note: {args['note']}"] if args.get("note") else []
    elif action == actions.RETURN_MISSED_CALL:
        recipient = str(args.get("caller") or "")
        content_parts = []
    elif action == actions.SCHEDULE_ROUTINE:
        content_parts = [f"Instruction: {args.get('instruction', '')}"]
        timing = str(args.get("said") or "")
    elif action == actions.SEND_IDENTITY_EMAIL:
        app = str(args.get("from_address") or "")
        timing = ""
    elif action == actions.BROWSER_STEP:
        # The exact button and page, with the fields as they will be sent.
        app = "your private browser"
        content_parts = [
            f"Press: {args.get('button', '')}",
            f"Page: {args.get('page_url', '')}",
            *(f"{k}: {_as_text(v)}" for k, v in dict(args.get("fields") or {}).items()),
        ]
    elif action == actions.DESKTOP_STEP:
        app = " on ".join(
            part
            for part in (str(args.get("app") or ""), str(args.get("device") or ""))
            if part
        )
        content_parts = [f"Step: {args.get('step_name', '')}"]
        timing = (
            "Your computer takes it once, after you approve and the undo window passes."
        )
    elif action == actions.MEETING_FOLLOW_UP:
        content_parts = [
            f"Task: {args.get('task', '')}",
            *([f"For: {args['owner_name']}"] if args.get("owner_name") else []),
            *([f"Due: {args['due_text']}"] if args.get("due_text") else []),
            *(
                [f"From the meeting: {args['meeting_title']}"]
                if args.get("meeting_title")
                else []
            ),
        ]
        timing = ""
    elif action == actions.PLACE_ORDER:
        app = str(args.get("provider") or "")
        timing = ""
    elif action == actions.CREATE_BOT:
        content_parts = [
            f"{k}: {v}" for k, v in dict(args.get("variables") or {}).items()
        ]

    detail = " · ".join(
        part
        for part in (
            f"To {recipient}" if recipient else "",
            amount,
            f"From {account}" if account else "",
            f"In {app}" if app else "",
        )
        if part
    )
    state = str(payload.get("state") or actions.PROPOSED)
    return {
        "sentence": f"{actor} wants to: {label}"
        + (f": {amount}" if amount and amount not in label else ""),
        "detail": detail,
        "verb": label,
        "action": action,
        "account": account or app or None,
        "recipient": recipient or None,
        "amount": amount or None,
        "content": "\n".join(content_parts) or None,
        "attachments": attachments,
        "timing": timing or None,
        "consequence": str(
            payload.get("effect")
            or (
                "It can be put back afterwards."
                if payload.get("reversible")
                else "It cannot be undone once it runs."
            )
        ),
        "why": str(payload.get("why") or "") or None,
        "reversible": bool(payload.get("reversible")),
        # No expiry yet (CONTROLS.md, "Not built here"); said, not invented.
        "expires_at": None,
        "version": payload.get("version"),
        "revisions": list(payload.get("revisions") or []),
        "state": state,
        "screen_state": SCREEN_STATE.get(state, "pending"),
        "error": payload.get("error"),
        "done_note": (payload.get("done") or {}).get("note"),
        "fires_at": payload.get("fires_at"),
    }


async def _visible_threads(viewer: Viewer, rows: list[Any]) -> dict[str | None, bool]:
    """For each Decibyl conversation among ``rows``, whether ``viewer`` may
    see it. Everything is visible while private threads are off."""
    out: dict[str | None, bool] = {}
    for row in rows:
        if row.workflow_id is not None or row.folder_id is not None:
            continue
        if row.thread_id in out:
            continue
        if not constants.DECIBYL_PRIVATE_THREADS_ENABLED:
            out[row.thread_id] = True
            continue
        author = await db_client.thread_author(
            organization_id=viewer.organization_id, thread_id=row.thread_id
        )
        out[row.thread_id] = (
            author == viewer.user_id if author is not None else viewer.is_admin
        )
    return out


def _may_see(
    row: Any, threads: dict[str | None, bool], user_id: int | None = None
) -> bool:
    """Whether a card is visible at all: its conversation's privacy, and --
    given the viewer -- a private card (a meeting's, an identity act) that
    is someone else's reads as not there."""
    payload = dict(row.payload or {})
    if user_id is not None:
        refusal = actions.answer_refusal(payload, user_id)
        if refusal == actions.NOT_HERE:
            return False
        owner = payload.get("private_to")
        if owner is not None and int(owner) == int(user_id):
            # A card kept for one person is theirs on whatever thread it
            # sits: a plain member's own card on the authorless thread (an
            # Admin's) would otherwise be missing from their own Today.
            return True
    if row.workflow_id is not None or row.folder_id is not None:
        return True
    return threads.get(row.thread_id, False)


async def _actor_names(organization_id: int, rows: list[Any]) -> dict[int, str]:
    names: dict[int, str] = {}
    for workflow_id in {r.workflow_id for r in rows if r.workflow_id is not None}:
        workflow = await db_client.get_workflow(
            workflow_id, organization_id=organization_id
        )
        names[workflow_id] = getattr(workflow, "name", None) or "Your agent"
    return names


def _row_view(row: Any, actor: str) -> dict[str, Any]:
    payload = dict(row.payload or {})
    preview = describe(payload, actor=actor)
    return {
        "id": int(row.id),
        "at": row.at.isoformat() if row.at else None,
        "label": preview["verb"],
        "sentence": preview["sentence"],
        "detail": preview["detail"],
        "why": preview["why"],
        "version": preview["version"],
        "state": preview["state"],
        "workflow_id": row.workflow_id,
        "thread_id": row.thread_id,
    }


async def pending(viewer: Viewer, *, limit: int = QUEUE_LIMIT) -> dict[str, Any]:
    """Only genuinely pending cards this person may see, newest first, and
    the count -- the badge reads this number, never a client tally."""
    state = func.coalesce(AgentEventModel.payload.op("->>")("state"), actions.PROPOSED)
    query = (
        select(AgentEventModel)
        .where(
            and_(
                AgentEventModel.organization_id == viewer.organization_id,
                AgentEventModel.kind == AgentEventKind.ACTION_PROPOSED.value,
                state == actions.PROPOSED,
            )
        )
        .order_by(AgentEventModel.id.desc())
        .limit(QUEUE_LIMIT)
    )
    async with db_client.async_session() as session:
        rows = list((await session.execute(query)).scalars().all())
    threads = await _visible_threads(viewer, rows)
    # Only cards this person may answer: a care card goes to the person it is
    # about, an order or outside write to its owner, a desktop or browser step
    # to the person whose machine it is, a meeting follow-up to whoever
    # captured the meeting (actions.answer_refusal). Nobody is offered a
    # Do it the card would refuse.
    visible = [
        r
        for r in rows
        if _may_see(r, threads, viewer.user_id)
        and actions.answer_refusal(dict(r.payload or {}), viewer.user_id) is None
    ]
    names = await _actor_names(viewer.organization_id, visible)
    items = [
        _row_view(
            r, names.get(r.workflow_id, "Decibyl") if r.workflow_id else "Decibyl"
        )
        for r in visible[:limit]
    ]
    return {"count": len(visible), "items": items}


def _editable(payload: dict[str, Any]) -> dict[str, Any] | None:
    """The fields a person may change before approving (actions.revise)."""
    action = payload.get("action")
    args = dict(payload.get("args") or {})
    if action == actions.RUN_TOOL:
        return dict(args.get("arguments") or {})
    if action == actions.SEND_DOCUMENT:
        return {k: args.get(k) or "" for k in ("to", "channel", "note")}
    if action == actions.SEND_IDENTITY_EMAIL:
        return {k: args.get(k) or "" for k in ("to", "subject", "body")}
    return None


async def preview(viewer: Viewer, event_id: int) -> dict[str, Any]:
    """Screen 08: one card in full, if this person may see it."""
    event = await db_client.get_agent_event(
        event_id, organization_id=viewer.organization_id
    )
    if event is None or event.kind != AgentEventKind.ACTION_PROPOSED.value:
        raise NotFound("That approval is not here.")
    threads = await _visible_threads(viewer, [event])
    if not _may_see(event, threads, viewer.user_id):
        raise NotFound("That approval is not here.")
    payload = dict(event.payload or {})
    refusal = actions.answer_refusal(payload, viewer.user_id)
    if refusal == actions.NOT_HERE:
        raise NotFound("That approval is not here.")
    actor = "Decibyl"
    if event.workflow_id is not None:
        actor = (await _actor_names(viewer.organization_id, [event])).get(
            event.workflow_id, actor
        )
    view = describe(payload, actor=actor)
    from api.services.workflow import task_ledger

    ledger = task_ledger.enabled(viewer.organization_id)
    view.update(
        {
            "id": int(event.id),
            "at": event.at.isoformat() if event.at else None,
            "workflow_id": event.workflow_id,
            "thread_id": event.thread_id,
            "editable": ledger
            and payload.get("action") in actions.REVISABLE
            and view["state"] in (actions.PROPOSED, actions.ARMED),
            "bound_to_version": ledger,
            # Who may answer it; a card someone else must answer is shown
            # read-only with the reason, never with a Do it that is refused.
            "can_answer": refusal is None,
            "answer_refusal": refusal,
            "arguments": _editable(payload),
        }
    )
    return view


__all__ = ["SCREEN_STATE", "describe", "mask_digits", "pending", "preview"]
