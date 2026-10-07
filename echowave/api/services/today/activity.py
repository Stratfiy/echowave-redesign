"""Activity and one item's detail (handoff 22 "Activity"; screen 09).

One list over the three things that do work for a person -- approval cards,
tasks on the board, and deliveries of their reminders and briefs -- in the
task ledger's one vocabulary (queued, running, needs input, awaiting
approval, scheduled, completed, failed, cancelled, outcome unknown), each
with its evidence. Provider internals stay in staff views: a failure reads
as its reason, never as a stack.

Retry is offered only where it is safe. A card that failed or whose outcome
is unknown is never re-fired from here: unknown says "Check delivery", and
a failed act is asked for again in Chat, which makes a new card with a new
approval. "Timed out" is a reason, not proof the provider did nothing.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import and_, or_, select

from api.db import db_client
from api.db.models import AgentEventModel, AgentTaskModel
from api.db.today_models import (
    DailyBriefModel,
    TodayDeliveryModel,
    TodayReminderModel,
)
from api.enums import AgentEventKind
from api.services.today import approvals
from api.services.today.scope import NotFound, TodayError, Viewer, full_local
from api.services.workflow import actions, task_ledger

STATES = task_ledger.STATES
#: How far back Activity looks by default.
DEFAULT_DAYS = 14

_DELIVERY_STATE = {
    "queued": task_ledger.QUEUED,
    "sent": task_ledger.COMPLETED,
    "accepted": task_ledger.COMPLETED,
    "needs_setup": task_ledger.NEEDS_INPUT,
    "skipped": task_ledger.CANCELLED,
    "failed": task_ledger.FAILED,
    "unknown": task_ledger.OUTCOME_UNKNOWN,
}


async def _cards(viewer: Viewer, since: datetime) -> list[Any]:
    async with db_client.async_session() as session:
        rows = list(
            (
                await session.execute(
                    select(AgentEventModel)
                    .where(
                        and_(
                            AgentEventModel.organization_id == viewer.organization_id,
                            AgentEventModel.kind
                            == AgentEventKind.ACTION_PROPOSED.value,
                            AgentEventModel.at >= since,
                        )
                    )
                    .order_by(AgentEventModel.id.desc())
                    .limit(300)
                )
            ).scalars()
        )
    threads = await approvals._visible_threads(viewer, rows)
    return [r for r in rows if approvals._may_see(r, threads, viewer.user_id)]


def _card_item(row: Any, names: dict[int, str]) -> dict[str, Any]:
    payload = dict(row.payload or {})
    state = task_ledger.card_state(payload)
    done = payload.get("done") or {}
    evidence = done.get("note")
    if state == task_ledger.OUTCOME_UNKNOWN:
        evidence = task_ledger.UNKNOWN_COPY
    elif state == task_ledger.FAILED:
        evidence = payload.get("error")
    return {
        "kind": "card",
        "id": int(row.id),
        "title": str(payload.get("label") or "An action"),
        "state": state,
        "at": (done.get("at") or row.at.isoformat()) if row.at else done.get("at"),
        "helper": names.get(row.workflow_id, "Decibyl")
        if row.workflow_id
        else "Decibyl",
        "evidence": evidence,
    }


async def _tasks(viewer: Viewer, since: datetime) -> list[Any]:
    mine = or_(
        AgentTaskModel.assignee_user_id == viewer.user_id,
        AgentTaskModel.created_by == viewer.user_id,
    )
    async with db_client.async_session() as session:
        return list(
            (
                await session.execute(
                    select(AgentTaskModel)
                    .where(
                        and_(
                            AgentTaskModel.organization_id == viewer.organization_id,
                            mine,
                            AgentTaskModel.created_at >= since,
                        )
                    )
                    .order_by(AgentTaskModel.id.desc())
                    .limit(300)
                )
            ).scalars()
        )


def _task_item(row: Any) -> dict[str, Any]:
    state = task_ledger.state_of(row)
    evidence = row.outcome_evidence or row.result
    if isinstance(evidence, dict):
        evidence = ", ".join(f"{k}: {v}" for k, v in evidence.items())
    return {
        "kind": "task",
        "id": int(row.id),
        "title": row.title,
        "state": state,
        "at": (row.finished_at or row.started_at or row.created_at).isoformat()
        if (row.finished_at or row.started_at or row.created_at)
        else None,
        "helper": "Task board",
        "evidence": str(evidence)[:300] if evidence else None,
    }


async def _deliveries(viewer: Viewer, since: datetime) -> list[Any]:
    async with db_client.async_session() as session:
        return list(
            (
                await session.execute(
                    select(TodayDeliveryModel)
                    .where(
                        and_(
                            TodayDeliveryModel.organization_id
                            == viewer.organization_id,
                            TodayDeliveryModel.user_id == viewer.user_id,
                            TodayDeliveryModel.created_at >= since,
                        )
                    )
                    .order_by(TodayDeliveryModel.id.desc())
                    .limit(300)
                )
            ).scalars()
        )


_HELPER = {
    "reminder": "Reminders",
    "brief": "Daily brief",
    "end_of_day": "End-of-day note",
}
_CHANNEL = {
    "in_app": "in the app",
    "whatsapp": "on WhatsApp",
    "push": "as a phone notification",
}


def _delivery_item(row: Any) -> dict[str, Any]:
    what = _HELPER.get(row.subject_kind, row.subject_kind)
    title = f"{what} {_CHANNEL.get(row.channel, row.channel)}"
    if row.is_test:
        title = f"Test: {title}"
    return {
        "kind": "delivery",
        "id": int(row.id),
        "title": title,
        "state": _DELIVERY_STATE.get(row.status, task_ledger.NEEDS_INPUT),
        "at": (row.delivered_at or row.created_at).isoformat(),
        "helper": what,
        "evidence": row.evidence or row.detail,
    }


async def list_for(
    viewer: Viewer,
    *,
    state: str | None = None,
    helper: str | None = None,
    days: int = DEFAULT_DAYS,
    now: datetime | None = None,
) -> dict[str, Any]:
    if state is not None and state not in STATES:
        raise TodayError("Not a task state.")
    since = (now or datetime.now().astimezone()) - timedelta(days=max(1, min(days, 90)))
    cards = await _cards(viewer, since)
    names = await approvals._actor_names(viewer.organization_id, cards)
    items = [
        _card_item(r, names)
        for r in cards
        if (r.payload or {}).get("state") not in (None, actions.PROPOSED)
    ]
    items += [_task_item(r) for r in await _tasks(viewer, since)]
    items += [_delivery_item(r) for r in await _deliveries(viewer, since)]
    helpers = sorted({i["helper"] for i in items})
    if state:
        items = [i for i in items if i["state"] == state]
    if helper:
        items = [i for i in items if i["helper"] == helper]
    items.sort(key=lambda i: i["at"] or "", reverse=True)
    return {"items": items[:200], "helpers": helpers, "timezone": viewer.zone_name}


def _stage(label: str, stamp: Any) -> dict[str, Any] | None:
    if not stamp:
        return None
    at = stamp.get("at") if isinstance(stamp, dict) else stamp
    return {"label": label, "at": at}


async def detail(viewer: Viewer, kind: str, item_id: int) -> dict[str, Any]:
    if kind == "card":
        return await _card_detail(viewer, item_id)
    if kind == "task":
        return await _task_detail(viewer, item_id)
    if kind == "delivery":
        return await _delivery_detail(viewer, item_id)
    raise NotFound("Not here.")


async def _card_detail(viewer: Viewer, event_id: int) -> dict[str, Any]:
    preview = await approvals.preview(viewer, event_id)
    event = await db_client.get_agent_event(
        event_id, organization_id=viewer.organization_id
    )
    payload = dict(event.payload or {})
    stages = [
        {"label": "Proposed", "at": preview["at"]},
        _stage("Approved", payload.get("confirmed")),
        _stage("Due to run", payload.get("fires_at"))
        if payload.get("state") in (actions.ARMED,)
        else None,
        _stage("Declined", payload.get("declined")),
        _stage("Cancelled", payload.get("cancelled")),
        _stage("Done", payload.get("done")),
        _stage("Put back", payload.get("undone")),
    ]
    state = task_ledger.card_state(payload)
    if state in (task_ledger.FAILED, task_ledger.OUTCOME_UNKNOWN, task_ledger.RUNNING):
        stages.append({"label": state.replace("_", " ").capitalize(), "at": None})
    return {
        "kind": "card",
        "id": event_id,
        "goal": preview["verb"],
        "owner": preview["sentence"].split(" wants to:")[0],
        "scope": "This conversation"
        if event.thread_id or event.workflow_id is None
        else "This workspace",
        "state": state,
        "stages": [s for s in stages if s],
        "inputs": {
            k: preview[k]
            for k in (
                "recipient",
                "account",
                "amount",
                "content",
                "attachments",
                "timing",
            )
            if preview.get(k)
        },
        "evidence": preview.get("done_note")
        or (
            task_ledger.UNKNOWN_COPY
            if state == task_ledger.OUTCOME_UNKNOWN
            else preview.get("error")
        ),
        "related": [
            {"kind": "approval", "id": event_id, "href": f"/tasks/approvals/{event_id}"}
        ],
        # Cancel stops future work: an armed card can still be undone.
        "can_cancel": payload.get("state") == actions.ARMED,
        # Never re-fired from here (see the module docstring).
        "can_retry": False,
        "check_delivery": state == task_ledger.OUTCOME_UNKNOWN,
        "version": preview.get("version"),
    }


async def _task_detail(viewer: Viewer, task_id: int) -> dict[str, Any]:
    async with db_client.async_session() as session:
        task = await session.scalar(
            select(AgentTaskModel).where(
                and_(
                    AgentTaskModel.id == task_id,
                    AgentTaskModel.organization_id == viewer.organization_id,
                )
            )
        )
    if task is None:
        raise NotFound("That task is not here.")
    ledger = task_ledger.as_dict(task)
    history = await task_ledger.history(viewer.organization_id, task_id)
    stages = [
        {
            "label": "Created",
            "at": task.created_at.isoformat() if task.created_at else None,
        }
    ]
    stages += [
        {
            "label": h["to"].replace("_", " ").capitalize(),
            "at": h["at"],
            "reason_code": h["reason_code"],
        }
        for h in history
    ]
    state = ledger["state"]
    return {
        "kind": "task",
        "id": task_id,
        "goal": task.title,
        "brief": task.brief,
        "owner": "You" if task.assignee_user_id == viewer.user_id else "This workspace",
        "scope": "This workspace",
        "state": state,
        "version": ledger["version"],
        "due": full_local(task.due_at, viewer.zone_name) if task.due_at else None,
        "stages": stages,
        "inputs": {},
        "evidence": ledger["outcome_evidence"] or ledger["notice"] or task.result,
        "related": [{"kind": "task", "id": task_id, "href": f"/tasks/{task_id}"}],
        "can_cancel": state not in task_ledger.TERMINAL
        and state != task_ledger.OUTCOME_UNKNOWN,
        "can_retry": False,
        "check_delivery": state == task_ledger.OUTCOME_UNKNOWN,
    }


async def _delivery_detail(viewer: Viewer, delivery_id: int) -> dict[str, Any]:
    async with db_client.async_session() as session:
        row = await session.scalar(
            select(TodayDeliveryModel).where(
                and_(
                    TodayDeliveryModel.id == delivery_id,
                    TodayDeliveryModel.organization_id == viewer.organization_id,
                    TodayDeliveryModel.user_id == viewer.user_id,
                )
            )
        )
        if row is None:
            raise NotFound("Not here.")
        subject = None
        if row.subject_kind == "reminder":
            subject = await session.scalar(
                select(TodayReminderModel).where(
                    and_(
                        TodayReminderModel.id == row.subject_id,
                        TodayReminderModel.organization_id == viewer.organization_id,
                        TodayReminderModel.user_id == viewer.user_id,
                    )
                )
            )
        else:
            subject = await session.scalar(
                select(DailyBriefModel).where(
                    and_(
                        DailyBriefModel.id == row.subject_id,
                        DailyBriefModel.organization_id == viewer.organization_id,
                        DailyBriefModel.user_id == viewer.user_id,
                    )
                )
            )
    item = _delivery_item(row)
    related = []
    if subject is not None and row.subject_kind == "reminder":
        related.append(
            {
                "kind": "reminder",
                "id": subject.id,
                "href": f"/tasks/reminders/{subject.id}",
                "title": subject.title,
            }
        )
    elif subject is not None:
        related.append(
            {
                "kind": "brief",
                "id": subject.id,
                "href": "/tasks#brief",
                "title": f"Brief for {subject.occurrence_key}",
            }
        )
    stages = [{"label": "Queued", "at": row.created_at.isoformat()}]
    if row.delivered_at:
        stages.append(
            {
                "label": "Sent" if row.status == "sent" else "Accepted by WhatsApp",
                "at": row.delivered_at.isoformat(),
            }
        )
    elif row.status != "queued":
        stages.append(
            {
                "label": (row.status or "").replace("_", " ").capitalize(),
                "at": None,
                "reason_code": row.reason_code,
            }
        )
    return {
        "kind": "delivery",
        "id": delivery_id,
        "goal": item["title"],
        "owner": "You",
        "scope": "Just you",
        "state": item["state"],
        "stages": stages,
        "inputs": {"channel": row.channel, "occurrence": row.occurrence_key},
        "evidence": row.evidence or row.detail,
        "related": related,
        "can_cancel": False,
        "can_retry": False,
        "check_delivery": row.status == "unknown",
    }


__all__ = ["detail", "list_for"]
