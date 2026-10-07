"""Today as one ordered list (handoff 22; screen 07).

"What needs my attention?" -- in the handoff's order: pending approvals,
urgent or due commitments (with active jobs beside them), the daily brief,
upcoming events, and at most three suggestions. Not a dashboard: no counts
for their own sake, no manufactured urgency, nothing personal invented to
make the page feel active.

Each section carries its own state. A section that could not be read is
``failed`` with its reason -- never an empty list -- so "Nothing due in
Decibyl" only appears when every section actually answered.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from loguru import logger
from sqlalchemy import and_, or_, select

from api.db import db_client
from api.db.models import AgentTaskModel
from api.db.today_models import TodayDismissalModel, TodayEventModel, TodayReminderModel
from api.services import features
from api.services.today import DAILY_BRIEF, END_OF_DAY, REMINDERS, approvals, brief
from api.services.today.scope import (
    NotFound,
    TodayError,
    Viewer,
    day_bounds,
    full_local,
    local_today,
)
from api.services.workflow import actions, task_ledger

EMPTY_COPY = "Nothing due in Decibyl"
CALENDAR_SETUP_COPY = "Connect your calendar to include appointments"
UPCOMING_DAYS = 7


async def _section(name: str, read: Callable[[], Awaitable[Any]]) -> dict[str, Any]:
    try:
        return {"state": "ok", **(await read())}
    except Exception as exc:  # noqa: BLE001 - a failed section is a state
        logger.warning("Today section {} failed: {}", name, exc)
        return {
            "state": "failed",
            "items": [],
            "message": f"{name.capitalize()} could not load. Try again.",
        }


async def _dismissed(viewer: Viewer, now: datetime) -> set[str]:
    async with db_client.async_session() as session:
        rows = (
            await session.execute(
                select(TodayDismissalModel).where(
                    and_(
                        TodayDismissalModel.organization_id == viewer.organization_id,
                        TodayDismissalModel.user_id == viewer.user_id,
                        or_(
                            TodayDismissalModel.until.is_(None),
                            TodayDismissalModel.until > now,
                        ),
                    )
                )
            )
        ).scalars()
        return {r.suggestion_key for r in rows}


async def _active_jobs(viewer: Viewer) -> list[dict[str, Any]]:
    """Work under way, shown beside the commitments it serves."""
    mine = or_(
        AgentTaskModel.assignee_user_id == viewer.user_id,
        AgentTaskModel.created_by == viewer.user_id,
    )
    async with db_client.async_session() as session:
        rows = list(
            (
                await session.execute(
                    select(AgentTaskModel)
                    .where(
                        and_(
                            AgentTaskModel.organization_id == viewer.organization_id,
                            mine,
                            AgentTaskModel.status == "in_progress",
                        )
                    )
                    .order_by(AgentTaskModel.id.desc())
                    .limit(10)
                )
            ).scalars()
        )
    return [
        {
            "kind": "task",
            "id": t.id,
            "title": t.title,
            "state": task_ledger.state_of(t),
            "href": f"/tasks/activity/task/{t.id}",
        }
        for t in rows
    ]


async def _upcoming(viewer: Viewer, now: datetime) -> dict[str, Any]:
    end = now + timedelta(days=UPCOMING_DAYS)
    async with db_client.async_session() as session:
        events = list(
            (
                await session.execute(
                    select(TodayEventModel)
                    .where(
                        and_(
                            TodayEventModel.organization_id == viewer.organization_id,
                            TodayEventModel.user_id == viewer.user_id,
                            TodayEventModel.status == "active",
                            TodayEventModel.starts_at >= now,
                            TodayEventModel.starts_at < end,
                        )
                    )
                    .order_by(TodayEventModel.starts_at)
                )
            ).scalars()
        )
        linked = {}
        if events:
            rows = (
                await session.execute(
                    select(TodayReminderModel).where(
                        and_(
                            TodayReminderModel.event_id.in_([e.id for e in events]),
                            TodayReminderModel.organization_id
                            == viewer.organization_id,
                            TodayReminderModel.user_id == viewer.user_id,
                            TodayReminderModel.status != "cancelled",
                        )
                    )
                )
            ).scalars()
            for r in rows:
                linked.setdefault(r.event_id, []).append(
                    {"id": r.id, "offset_minutes": r.offset_minutes, "status": r.status}
                )
    return {
        "items": [
            {
                "kind": "event",
                "id": e.id,
                "title": e.title,
                "at": e.starts_at.isoformat(),
                "when": full_local(e.starts_at, e.timezone),
                "timezone": e.timezone,
                "reminders": linked.get(e.id, []),
                "revision": e.revision,
            }
            for e in events
        ]
    }


async def _calendar_connected(viewer: Viewer) -> bool | None:
    from api.services.integrations.google_calendar import oauth

    try:
        async with db_client.async_session() as session:
            return (
                await oauth.get_status(session, organization_id=viewer.organization_id)
            ).connected
    except Exception:  # noqa: BLE001 - unknown is said as unknown
        return None


async def today(viewer: Viewer, *, now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    zone = viewer.zone
    day = local_today(zone, now)
    start, end = day_bounds(day, zone)
    reminders_on = features.is_on(REMINDERS, viewer.organization_id)
    brief_on = features.is_on(DAILY_BRIEF, viewer.organization_id)
    eod_on = features.is_on(END_OF_DAY, viewer.organization_id)

    async def read_approvals() -> dict[str, Any]:
        return await approvals.pending(viewer)

    sources: dict[str, brief.Source] = {}

    async def read_due() -> dict[str, Any]:
        items: list[dict[str, Any]] = []
        if reminders_on:
            sources["reminders"] = await brief.read_decibyl_items(viewer, start, end)
            for item in sources["reminders"].items:
                if item["type"] == "reminder":
                    items.append(
                        {
                            "kind": "reminder",
                            "id": item["id"],
                            "title": item["title"],
                            "at": item["at"],
                            "when": item["when"],
                            "overdue": item["overdue"],
                            "href": f"/tasks/reminders/{item['id']}",
                        }
                    )
        sources["tasks"] = await brief.read_tasks(viewer, start, end)
        for item in sources["tasks"].items:
            items.append(
                {
                    "kind": "task",
                    "id": item["id"],
                    "title": item["title"],
                    "at": item["at"],
                    "when": full_local(
                        datetime.fromisoformat(item["at"]), viewer.zone_name
                    )
                    if item["at"]
                    else None,
                    "overdue": item["overdue"],
                    "needs_input": item["needs_input"],
                    "href": f"/tasks/activity/task/{item['id']}",
                }
            )
        sources["missed_calls"] = await brief.read_missed_calls(viewer, start, end)
        for call in sources["missed_calls"].items:
            if call["handled"]:
                continue
            items.append(
                {
                    "kind": "missed_call",
                    "id": call["id"],
                    "title": f"Missed call from {call['caller']}",
                    "at": call["at"],
                    "when": full_local(
                        datetime.fromisoformat(call["at"]), viewer.zone_name
                    ),
                    "outcome": call["outcome"],
                }
            )
        items.sort(key=lambda i: (not i.get("overdue"), i.get("at") or "9999"))
        return {"items": items, "active": await _active_jobs(viewer)}

    async def read_brief() -> dict[str, Any]:
        settings = await brief.get_settings(viewer)
        return {
            "item": await brief.latest(viewer),
            "enabled": settings["enabled"] and not settings["paused"],
            "paused": settings["paused"],
            "next_sentence": settings["next_sentence"],
        }

    async def read_eod() -> dict[str, Any]:
        return {"item": await brief.latest(viewer, kind=brief.END_OF_DAY)}

    async def read_upcoming() -> dict[str, Any]:
        return await _upcoming(viewer, now)

    sections: dict[str, Any] = {
        "approvals": await _section("approvals", read_approvals),
        "due": await _section("due", read_due),
    }
    sections["brief"] = await _section("brief", read_brief) if brief_on else None
    sections["upcoming"] = (
        await _section("upcoming", read_upcoming) if reminders_on else None
    )
    sections["end_of_day"] = await _section("end of day", read_eod) if eod_on else None

    brief_enabled = bool(sections["brief"] and sections["brief"].get("enabled"))
    candidates = brief.suggestions_from(sources, brief_on=brief_enabled or not brief_on)
    hidden = await _dismissed(viewer, now)
    suggestions = [s for s in candidates if s["key"] not in hidden][
        : brief.MAX_SUGGESTIONS
    ]
    for s in suggestions:
        if s["action"]["kind"] == "open_reminder" and not reminders_on:
            s["action"] = {"kind": "none"}

    connected = await _calendar_connected(viewer)
    missing = []
    if connected is False:
        missing.append({"kind": "calendar", "message": CALENDAR_SETUP_COPY})

    answered = all(
        sections[k] is None or sections[k]["state"] == "ok"
        for k in ("approvals", "due", "upcoming")
    )
    nothing = (
        not sections["approvals"].get("count")
        and not sections["due"].get("items")
        and not (sections["upcoming"] or {}).get("items")
    )
    return {
        "date": day.isoformat(),
        "date_label": f"{day.strftime('%A')} {day.day} {day.strftime('%B %Y')}",
        "timezone": viewer.zone_name,
        "refreshed_at": now.isoformat(),
        "organization_id": viewer.organization_id,
        "order": ["approvals", "due", "brief", "upcoming", "suggestions", "end_of_day"],
        "sections": {**sections, "suggestions": {"state": "ok", "items": suggestions}},
        "missing_sources": missing,
        "empty": answered and nothing,
        "empty_copy": EMPTY_COPY if answered and nothing else None,
    }


async def dismiss(
    viewer: Viewer,
    key: str,
    *,
    later_minutes: int | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    from sqlalchemy.dialects.postgresql import insert

    now = now or datetime.now(UTC)
    key = (key or "").strip()[:96]
    if not key:
        raise TodayError("Say which suggestion.")
    until = None
    if later_minutes is not None:
        if not 15 <= int(later_minutes) <= 7 * 24 * 60:
            raise TodayError("Later is between 15 minutes and a week.")
        until = now + timedelta(minutes=int(later_minutes))
    stmt = insert(TodayDismissalModel).values(
        organization_id=viewer.organization_id,
        user_id=viewer.user_id,
        suggestion_key=key,
        until=until,
        created_at=now,
    )
    stmt = stmt.on_conflict_do_update(
        constraint="uq_today_dismissal", set_={"until": until, "created_at": now}
    )
    async with db_client.async_session() as session:
        await session.execute(stmt)
        await session.commit()
    return {"key": key, "until": until.isoformat() if until else None}


def _today_thread(viewer: Viewer) -> str:
    """The person's own Decibyl conversation for things asked from Today,
    so a card proposed here is theirs under private threads (D-1b)."""
    return str(
        uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"decibyl:today:{viewer.organization_id}:{viewer.user_id}",
        )
    )


async def propose_callback(viewer: Viewer, missed_call_id: int) -> dict[str, Any]:
    """Ask first: a callback is a card the person approves on screen 08,
    never a call placed from a tap on Today."""
    from api.enums import AgentEventActor, AgentEventKind
    from api.services.workflow import agent_timeline

    row = await db_client.get_missed_call(
        missed_call_id, organization_id=viewer.organization_id
    )
    if row is None:
        raise NotFound("That missed call is not here.")
    thread = _today_thread(viewer)
    with agent_timeline.in_thread(thread):
        await agent_timeline.record(
            organization_id=viewer.organization_id,
            kind=AgentEventKind.MESSAGE.value,
            actor=AgentEventActor.HUMAN.value,
            summary=f"Call {row.caller} back",
            payload={
                "body": f"Call {row.caller} back",
                "author_id": viewer.user_id,
                "from_today": True,
            },
            in_channel=False,
        )
        result = await actions.propose(
            organization_id=viewer.organization_id,
            workflow_id=None,
            workflow_run_id=None,
            in_channel=False,
            arguments={
                "action": actions.RETURN_MISSED_CALL,
                "missed_call_id": missed_call_id,
                "why": "Asked from Today",
            },
        )
    if result.get("status") == "not_proposed":
        raise TodayError(str(result.get("reason") or "Could not ask for that."))
    pending = await approvals.pending(viewer)
    for item in pending["items"]:
        event = await db_client.get_agent_event(
            item["id"], organization_id=viewer.organization_id
        )
        args = dict((event.payload or {}).get("args") or {}) if event else {}
        if (event.payload or {}).get(
            "action"
        ) == actions.RETURN_MISSED_CALL and args.get(
            "missed_call_id"
        ) == missed_call_id:
            return {"event_id": item["id"], "status": result.get("status")}
    raise TodayError("The card was asked for but could not be found. Look in Chat.")


__all__ = ["CALENDAR_SETUP_COPY", "EMPTY_COPY", "dismiss", "propose_callback", "today"]
