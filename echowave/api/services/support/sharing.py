"""What a person shares with support, shown before it is sent (screen 28).

Handoff 33: "Collect the user's explicit selection before sharing
conversation or recording content; never attach all history." Design 28:
"Offer task metadata by default; ask before sharing content or audio."

So the share is built from named sections, and the same function builds
the preview the person reads and the snapshot stored on the ticket -- what
staff see is, field for field, what the person was shown. Sections:

* ``account`` -- always: who is asking and from which workspace. Support
  cannot answer a request without it, and the preview says so.
* ``task_metadata`` -- on by default when the ticket is about a task or a
  reply: ids, state, times, the helper and the model. No words.
* ``content`` -- off unless chosen: the task's title and brief, or the
  reply's words and the person's message before it.

Audio is never offered: no recording is attached from Help, and the preview
says that too, rather than leaving it to be guessed.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select

from api.db import db_client
from api.db.models import AgentEventModel
from api.enums import AgentEventActor, AgentEventKind

ACCOUNT = "account"
TASK_METADATA = "task_metadata"
CONTENT = "content"
SECTIONS = (ACCOUNT, TASK_METADATA, CONTENT)
#: Sections the person may switch; ``account`` is always included.
OPTIONAL = frozenset({TASK_METADATA, CONTENT})
DEFAULT_ON = frozenset({TASK_METADATA})

TASK = "task"
REPLY = "reply"
AFFECTED_KINDS = (TASK, REPLY)

#: The longest piece of content copied into a share.
CONTENT_LIMIT = 4000

NOT_SHARED_NOTE = (
    "Not shared: the rest of your conversations, recordings and audio, "
    "your connected apps' data and anything from other people in your workspace."
)


class ShareRefused(ValueError):
    pass


class NotFound(ShareRefused):
    pass


def _iso(value: datetime | None) -> str | None:
    return value.astimezone(UTC).isoformat() if value else None


def _field(label: str, value: Any) -> dict[str, Any]:
    return {"label": label, "value": value}


async def _account(organization_id: int, user_id: int) -> list[dict[str, Any]]:
    user = await db_client.get_user_by_id(user_id)
    org = await db_client.get_organization_by_id(organization_id)
    return [
        _field("Your email", getattr(user, "email", None) or "Not set"),
        _field(
            "Workspace", (getattr(org, "name", None) or f"Workspace {organization_id}")
        ),
        _field("Workspace id", organization_id),
    ]


async def _task(organization_id: int, task_id: int) -> dict[str, Any]:
    from api.services.workflow import task_ledger

    task = await db_client.get_task(task_id, organization_id=organization_id)
    if task is None:
        raise NotFound("That task is not here.")
    meta = [
        _field("Task", f"#{task.id}"),
        _field("State", task_ledger.state_of(task).replace("_", " ")),
        _field("Created", _iso(task.created_at)),
        _field("Started", _iso(task.started_at)),
        _field("Finished", _iso(task.finished_at)),
        _field(
            "Helper",
            f"Agent #{task.assignee_workflow_id}"
            if task.assignee_workflow_id
            else "Decibyl",
        ),
    ]
    history = await task_ledger.history(organization_id, task.id)
    if history:
        last = history[-1]
        meta.append(_field("Last change", last.get("reason_code") or last.get("to")))
    content = [
        _field("Title", task.title or ""),
        _field("Brief", (task.brief or "")[:CONTENT_LIMIT]),
    ]
    if task.result:
        content.append(_field("Result", str(task.result)[:CONTENT_LIMIT]))
    return {"metadata": meta, "content": content}


async def _reply(organization_id: int, event_id: int, viewer_id: int) -> dict[str, Any]:
    from api.services import feedback

    try:
        found = await feedback.subject(
            organization_id, feedback.REPLY, event_id, viewer_id
        )
    except (feedback.FeedbackRefused, feedback.NotFound) as exc:
        raise NotFound("That reply is not here.") from exc
    event = await db_client.get_agent_event(event_id, organization_id=organization_id)
    assert event is not None  # feedback.subject found it in this workspace
    payload = dict(event.payload or {})
    state = (
        "stopped"
        if payload.get("stopped")
        else "failed"
        if payload.get("failed")
        else "answered"
    )
    meta = [
        _field("Reply", f"#{event.id}"),
        _field("When", _iso(event.at)),
        _field("State", state),
        _field("Model", found.get("model") or "Not recorded"),
        _field(
            "Helper", f"Agent #{event.workflow_id}" if event.workflow_id else "Decibyl"
        ),
    ]
    content = [
        _field(
            "Decibyl's reply",
            str(payload.get("body") or event.summary or "")[:CONTENT_LIMIT],
        )
    ]
    asked = await _message_before(organization_id, event)
    if asked is not None:
        content.insert(0, _field("Your message", asked[:CONTENT_LIMIT]))
    return {"metadata": meta, "content": content}


async def _message_before(organization_id: int, event: Any) -> str | None:
    """The person's line just before the reply, in the same thread only."""
    async with db_client.async_session() as session:
        query = select(AgentEventModel).where(
            AgentEventModel.organization_id == organization_id,
            AgentEventModel.id < event.id,
            AgentEventModel.kind == AgentEventKind.MESSAGE.value,
            AgentEventModel.actor == AgentEventActor.HUMAN.value,
        )
        thread_id = getattr(event, "thread_id", None)
        query = query.where(
            AgentEventModel.thread_id == thread_id
            if thread_id is not None
            else AgentEventModel.thread_id.is_(None)
        )
        query = query.where(
            AgentEventModel.workflow_id == event.workflow_id
            if event.workflow_id is not None
            else AgentEventModel.workflow_id.is_(None)
        )
        row = await session.scalar(query.order_by(AgentEventModel.id.desc()).limit(1))
    if row is None:
        return None
    return str((row.payload or {}).get("body") or row.summary or "")


async def build(
    *,
    organization_id: int,
    user_id: int,
    affected_kind: str | None,
    affected_id: int | None,
    share: list[str] | None,
) -> dict[str, Any]:
    """The preview and, unchanged, the snapshot stored on a ticket.

    ``share`` names the optional sections the person switched on; ``None``
    means the defaults. Every section is listed, included or not, so the
    person sees what they are leaving out as well as what they send.
    """
    if (affected_kind is None) != (affected_id is None):
        raise ShareRefused("Name both what is affected and its id, or neither.")
    if affected_kind is not None and affected_kind not in AFFECTED_KINDS:
        raise ShareRefused("A ticket can be about a task or a reply.")
    chosen = set(DEFAULT_ON if share is None else share)
    unknown = chosen - OPTIONAL - {ACCOUNT}
    if unknown:
        raise ShareRefused(f"{min(unknown)} is not something that can be shared.")

    subject: dict[str, Any] | None = None
    if affected_kind == TASK:
        subject = await _task(organization_id, int(affected_id))
    elif affected_kind == REPLY:
        subject = await _reply(organization_id, int(affected_id), user_id)

    sections: list[dict[str, Any]] = [
        {
            "key": ACCOUNT,
            "label": "Who is asking",
            "included": True,
            "required": True,
            "fields": await _account(organization_id, user_id),
        }
    ]
    if subject is not None:
        sections.append(
            {
                "key": TASK_METADATA,
                "label": "Details of what went wrong (no words from it)",
                "included": TASK_METADATA in chosen,
                "required": False,
                "fields": subject["metadata"],
            }
        )
        sections.append(
            {
                "key": CONTENT,
                "label": "The words themselves",
                "included": CONTENT in chosen,
                "required": False,
                "fields": subject["content"],
            }
        )
    return {
        "affected": (
            {"kind": affected_kind, "id": int(affected_id)} if affected_kind else None
        ),
        "sections": sections,
        "not_shared": NOT_SHARED_NOTE,
    }


def snapshot(preview: dict[str, Any]) -> dict[str, Any]:
    """What is stored and shown to staff: only the included sections."""
    return {
        "affected": preview["affected"],
        "sections": [
            {"key": s["key"], "label": s["label"], "fields": s["fields"]}
            for s in preview["sections"]
            if s["included"]
        ],
        "left_out": [s["key"] for s in preview["sections"] if not s["included"]],
        "shared_at": datetime.now(UTC).isoformat(),
    }
