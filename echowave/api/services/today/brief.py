"""One daily brief, and the end-of-day note, with source coverage (handoff
10, 22; screens 07 and 20).

**Coverage is part of the brief.** Every source the brief could read is
listed with what happened to it: read, needs setup ("Connect your calendar
to include appointments"), unavailable (not built yet, said so), or failed
("Calendar could not refresh. This brief includes ... only."). A partially
failed retrieval is never turned into "nothing needs attention", and a
calendar that was not read never becomes "no meetings".

**One occurrence per day.** A brief is a row per (workspace, person, kind,
local date). Refresh rebuilds that row in place and keeps the refresh time;
the scheduled run claims ``delivered_at`` once, so one saved schedule
produces one delivered brief, however often the tick runs.

**No model writes it.** The brief is composed from records, in sentences
with numbers. Nothing in it is guessed, and nothing personal is invented to
make it feel full.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from loguru import logger
from sqlalchemy import and_, or_, select, update
from sqlalchemy.dialects.postgresql import insert

from api.db import db_client
from api.db.today_models import (
    DailyBriefModel,
    DailyBriefSettingsModel,
    TodayDeliveryModel,
    TodayEventModel,
    TodayReminderModel,
)
from api.services import features
from api.services.today import REMINDERS
from api.services.today.scope import (
    Conflict,
    TodayError,
    Viewer,
    at_local,
    day_bounds,
    full_local,
    local_today,
    parse_hhmm,
    valid_zone,
)

BRIEF = "brief"
END_OF_DAY = "end_of_day"
KINDS = (BRIEF, END_OF_DAY)

#: The time the handoff suggests, in the person's confirmed timezone.
SUGGESTED_TIME = "09:00"
CHANNELS = ("in_app", "whatsapp", "push")
MAX_SUGGESTIONS = 3
#: How late a scheduled brief may still go out (the routines' catch-up rule).
CATCH_UP_MINUTES = 20

READ = "read"
UNAVAILABLE = "unavailable"
FAILED = "failed"


@dataclass
class Source:
    """What one source gave the brief, and how."""

    kind: str
    label: str
    status: str = READ
    detail: str | None = None
    needs_setup: bool = False
    items: list[dict[str, Any]] = field(default_factory=list)

    def view(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "label": self.label,
            "status": self.status,
            "detail": self.detail,
            "needs_setup": self.needs_setup,
            "count": len(self.items),
        }


Reader = Callable[[Viewer, datetime, datetime], Awaitable[Source]]


# --- sources -----------------------------------------------------------------


async def read_approvals(viewer: Viewer, start: datetime, end: datetime) -> Source:
    from api.services.today import approvals

    pending = await approvals.pending(viewer)
    return Source(
        "approvals",
        "Approvals",
        items=[{"title": i["label"], "id": i["id"]} for i in pending["items"]],
    )


async def read_decibyl_items(viewer: Viewer, start: datetime, end: datetime) -> Source:
    """Events and reminders kept in Decibyl, for the period."""
    if not features.is_on(REMINDERS, viewer.organization_id):
        return Source(
            "reminders",
            "Reminders and events",
            status=UNAVAILABLE,
            detail="Reminders are not switched on for this workspace.",
        )
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
                            TodayEventModel.starts_at >= start,
                            TodayEventModel.starts_at < end,
                        )
                    )
                    .order_by(TodayEventModel.starts_at)
                )
            ).scalars()
        )
        reminders = list(
            (
                await session.execute(
                    select(TodayReminderModel)
                    .where(
                        and_(
                            TodayReminderModel.organization_id
                            == viewer.organization_id,
                            TodayReminderModel.user_id == viewer.user_id,
                            TodayReminderModel.status.in_(("active", "missed")),
                            TodayReminderModel.remind_at < end,
                        )
                    )
                    .order_by(TodayReminderModel.remind_at)
                )
            ).scalars()
        )
    items = [
        {
            "type": "event",
            "id": e.id,
            "title": e.title,
            "at": e.starts_at.isoformat(),
            "when": full_local(e.starts_at, e.timezone),
        }
        for e in events
    ] + [
        {
            "type": "reminder",
            "id": r.id,
            "title": r.title,
            "at": r.remind_at.isoformat() if r.remind_at else None,
            "when": full_local(r.remind_at, r.timezone) if r.remind_at else None,
            "overdue": bool(r.remind_at and r.remind_at < start)
            or r.status == "missed",
        }
        for r in reminders
    ]
    return Source("reminders", "Reminders and events", items=items)


async def read_tasks(viewer: Viewer, start: datetime, end: datetime) -> Source:
    """Commitments on the task board that are this person's and due by the
    end of the period, or waiting on their input."""
    from api.db.models import AgentTaskModel

    mine = or_(
        AgentTaskModel.assignee_user_id == viewer.user_id,
        and_(
            AgentTaskModel.assignee_user_id.is_(None),
            AgentTaskModel.created_by == viewer.user_id,
        ),
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
                            AgentTaskModel.status.notin_(("done", "cancelled")),
                            or_(
                                AgentTaskModel.due_at < end,
                                AgentTaskModel.ledger_state == "needs_input",
                            ),
                        )
                    )
                    .order_by(AgentTaskModel.due_at.asc().nulls_last())
                    .limit(50)
                )
            ).scalars()
        )
    return Source(
        "tasks",
        "Tasks",
        items=[
            {
                "type": "task",
                "id": t.id,
                "title": t.title,
                "at": t.due_at.isoformat() if t.due_at else None,
                "needs_input": t.ledger_state == "needs_input",
                "overdue": bool(t.due_at and t.due_at < start),
            }
            for t in rows
        ],
    )


async def read_missed_calls(viewer: Viewer, start: datetime, end: datetime) -> Source:
    rows = await db_client.list_missed_calls(viewer.organization_id, limit=200)
    since = end - timedelta(hours=48)
    recent = [r for r in rows if r.received_at and since <= r.received_at < end]
    return Source(
        "missed_calls",
        "Missed calls",
        items=[
            {
                "id": r.id,
                "caller": r.caller,
                "at": r.received_at.isoformat(),
                "handled": r.outcome == "called_back",
                "outcome": r.outcome,
            }
            for r in recent
        ],
    )


async def read_calendar(viewer: Viewer, start: datetime, end: datetime) -> Source:
    """The workspace's connected Google Calendar, for the period."""
    from api.services.integrations.google_calendar import availability, oauth

    label = "Calendar"
    try:
        async with db_client.async_session() as session:
            status = await oauth.get_status(
                session, organization_id=viewer.organization_id
            )
            if not status.connected:
                return Source(
                    "calendar",
                    label,
                    status=UNAVAILABLE,
                    needs_setup=True,
                    detail="Connect your calendar to include appointments.",
                )
            token = await oauth.get_valid_access_token(
                session, organization_id=viewer.organization_id
            )
        calendars = [status.calendar_id or "primary", *status.busy_calendar_ids]
        day = start.astimezone(viewer.zone).date()
        items = await availability.busy_events(token, calendars, day, viewer.zone_name)
    except Exception as exc:  # noqa: BLE001 - a failed source is a state, not an error
        logger.warning(
            "Brief calendar read failed for org {}: {}", viewer.organization_id, exc
        )
        items = None
    if items is None:
        return Source(
            "calendar", label, status=FAILED, detail="Calendar could not refresh."
        )
    out = []
    for item in items:
        begins = (item.get("start") or {}).get("dateTime") or (
            item.get("start") or {}
        ).get("date")
        out.append(
            {
                "type": "appointment",
                "title": item.get("summary") or "Busy",
                "at": begins,
            }
        )
    return Source("calendar", label, items=out)


async def read_mail(viewer: Viewer, start: datetime, end: datetime) -> Source:
    """Not built yet: reading a person's inbox for the brief is the Inbox
    helper's (stream `agents`). Said, so the brief never implies it looked."""
    return Source(
        "mail",
        "Mail",
        status=UNAVAILABLE,
        detail="Important mail is not in the brief yet.",
    )


#: The brief's sources, in the order they are listed. Tests replace entries.
READERS: dict[str, Reader] = {
    "approvals": read_approvals,
    "calendar": read_calendar,
    "reminders": read_decibyl_items,
    "tasks": read_tasks,
    "missed_calls": read_missed_calls,
    "mail": read_mail,
}


async def _read_all(
    viewer: Viewer, start: datetime, end: datetime
) -> dict[str, Source]:
    out: dict[str, Source] = {}
    for key, reader in READERS.items():
        try:
            out[key] = await reader(viewer, start, end)
        except Exception as exc:  # noqa: BLE001 - one source must not sink the brief
            logger.warning("Brief source {} failed: {}", key, exc)
            out[key] = Source(
                key,
                key.replace("_", " ").capitalize(),
                status=FAILED,
                detail=f"{key.replace('_', ' ').capitalize()} could not refresh.",
            )
    return out


# --- composing ------------------------------------------------------------------


def _plural(n: int, one: str, many: str | None = None) -> str:
    return f"{n} {one if n == 1 else (many or one + 's')}"


def _coverage_line(sources: dict[str, Source]) -> str | None:
    """The design's partial-brief copy: "Calendar could not refresh. This
    brief includes mail only." -- naming what it does include."""
    failed = [s for s in sources.values() if s.status == FAILED]
    if not failed:
        return None
    included = [s.label.lower() for s in sources.values() if s.status == READ]
    names = " and ".join(s.label for s in failed)
    if included:
        return f"{names} could not refresh. This brief includes {', '.join(included)} only."
    return f"{names} could not refresh. This brief has nothing it could read."


def suggestions_from(
    sources: dict[str, Source], *, brief_on: bool
) -> list[dict[str, Any]]:
    """At most three, each with "Why this?" from evidence the brief read.
    A suggestion is offered, never acted on."""
    out: list[dict[str, Any]] = []
    calls = [
        c for c in sources.get("missed_calls", Source("", "")).items if not c["handled"]
    ]
    if calls:
        call = calls[0]
        out.append(
            {
                "key": f"missed_call:{call['id']}",
                "title": f"Call {call['caller']} back",
                "why": "They rang and nobody has called them back yet.",
                "action": {"kind": "propose_callback", "missed_call_id": call["id"]},
            }
        )
    for item in sources.get("reminders", Source("", "")).items:
        if item.get("type") == "reminder" and item.get("overdue"):
            out.append(
                {
                    "key": f"overdue_reminder:{item['id']}",
                    "title": f"Snooze or finish: {item['title']}",
                    "why": "This reminder's time has passed.",
                    "action": {"kind": "open_reminder", "reminder_id": item["id"]},
                }
            )
            break
    if not brief_on:
        out.append(
            {
                "key": "offer_daily_brief",
                "title": "Would a daily summary help?",
                "why": f"One short brief at {SUGGESTED_TIME} in your timezone. Off until you say yes.",
                "action": {"kind": "open_brief_settings"},
            }
        )
    return out[:MAX_SUGGESTIONS]


def _compose_brief(
    sources: dict[str, Source], viewer: Viewer
) -> tuple[str, dict[str, Any]]:
    approvals = (
        sources["approvals"].items if sources["approvals"].status == READ else []
    )
    decibyl = sources["reminders"].items if sources["reminders"].status == READ else []
    calendar = sources["calendar"]
    tasks = sources["tasks"].items if sources["tasks"].status == READ else []
    calls = [c for c in sources["missed_calls"].items if not c["handled"]]

    appointments = [i for i in decibyl if i["type"] == "event"]
    if calendar.status == READ:
        appointments = [*calendar.items, *appointments]
    due = [i for i in decibyl if i["type"] == "reminder"] + tasks

    parts = []
    if approvals:
        parts.append(f"{_plural(len(approvals), 'approval')} waiting for you")
    if due:
        parts.append(f"{_plural(len(due), 'thing')} due")
    if calendar.status == READ or appointments:
        parts.append(_plural(len(appointments), "appointment"))
    if calls:
        parts.append(f"{_plural(len(calls), 'missed call')} not returned")
    if parts:
        summary = ", ".join(parts) + "."
        summary = summary[0].upper() + summary[1:]
    else:
        summary = "Nothing due in Decibyl."
    if calendar.status != READ:
        # Never "no meetings" from a calendar nobody read.
        summary += " " + (calendar.detail or "Calendar not read.")
    coverage = _coverage_line(sources)
    if coverage and coverage not in summary:
        summary += " " + coverage
    sections = {
        "appointments": {
            "calendar_read": calendar.status == READ,
            "items": appointments,
        },
        "due": due,
        "approvals": {"count": len(approvals), "items": approvals[:5]},
        "missed_calls": {"count": len(calls), "items": calls[:5]},
        "suggestions": suggestions_from(sources, brief_on=True),
    }
    return summary, sections


async def _done_today(
    viewer: Viewer, start: datetime, end: datetime
) -> list[dict[str, Any]]:
    """Finished work in the period, with evidence: cards that ran, tasks
    marked done, reminders delivered."""
    from api.db.models import AgentEventModel, AgentTaskModel
    from api.enums import AgentEventKind
    from api.services.today import approvals as approvals_service

    done: list[dict[str, Any]] = []
    async with db_client.async_session() as session:
        cards = list(
            (
                await session.execute(
                    select(AgentEventModel)
                    .where(
                        and_(
                            AgentEventModel.organization_id == viewer.organization_id,
                            AgentEventModel.kind
                            == AgentEventKind.ACTION_PROPOSED.value,
                            AgentEventModel.at >= start - timedelta(days=2),
                        )
                    )
                    .order_by(AgentEventModel.id.desc())
                    .limit(300)
                )
            ).scalars()
        )
        tasks = list(
            (
                await session.execute(
                    select(AgentTaskModel).where(
                        and_(
                            AgentTaskModel.organization_id == viewer.organization_id,
                            AgentTaskModel.status == "done",
                            AgentTaskModel.finished_at >= start,
                            AgentTaskModel.finished_at < end,
                            or_(
                                AgentTaskModel.assignee_user_id == viewer.user_id,
                                AgentTaskModel.created_by == viewer.user_id,
                            ),
                        )
                    )
                )
            ).scalars()
        )
        delivered = list(
            (
                await session.execute(
                    select(TodayDeliveryModel).where(
                        and_(
                            TodayDeliveryModel.organization_id
                            == viewer.organization_id,
                            TodayDeliveryModel.user_id == viewer.user_id,
                            TodayDeliveryModel.subject_kind == "reminder",
                            TodayDeliveryModel.is_test.is_(False),
                            TodayDeliveryModel.status.in_(("sent", "accepted")),
                            TodayDeliveryModel.delivered_at >= start,
                            TodayDeliveryModel.delivered_at < end,
                        )
                    )
                )
            ).scalars()
        )
    threads = await approvals_service._visible_threads(viewer, cards)
    for card in cards:
        payload = dict(card.payload or {})
        stamp = (payload.get("done") or {}).get("at")
        if payload.get("state") != "done" or not stamp:
            continue
        try:
            at = datetime.fromisoformat(stamp)
        except ValueError:
            continue
        if not (start <= at < end) or not approvals_service._may_see(
            card, threads, viewer.user_id
        ):
            continue
        done.append(
            {
                "type": "card",
                "id": card.id,
                "title": str(payload.get("label") or "An approved action"),
                "evidence": (payload.get("done") or {}).get("note"),
            }
        )
    done += [
        {"type": "task", "id": t.id, "title": t.title, "evidence": "Marked done"}
        for t in tasks
    ]
    done += [
        {
            "type": "reminder",
            "id": d.subject_id,
            "title": "Reminder delivered",
            "evidence": d.evidence,
        }
        for d in delivered
    ]
    return done


async def _compose_end_of_day(
    viewer: Viewer, sources: dict[str, Source], start: datetime, end: datetime
) -> tuple[str, dict[str, Any]]:
    done = await _done_today(viewer, start, end)
    left = [
        *(sources["reminders"].items if sources["reminders"].status == READ else []),
        *(sources["tasks"].items if sources["tasks"].status == READ else []),
    ]
    left = [i for i in left if i.get("type") != "event"]
    calls = (
        sources["missed_calls"].items if sources["missed_calls"].status == READ else []
    )
    today_calls = [c for c in calls if start.isoformat() <= c["at"] < end.isoformat()]
    handled = [c for c in today_calls if c["handled"]]
    waiting = [c for c in calls if not c["handled"]]
    parts = [f"{_plural(len(done), 'thing')} done today"]
    if left:
        parts.append(f"{len(left)} still open")
    if today_calls:
        parts.append(
            f"{len(handled)} of {_plural(len(today_calls), 'missed call')} called back"
        )
    elif waiting:
        parts.append(f"{_plural(len(waiting), 'missed call')} still to return")
    summary = ", ".join(parts) + "."
    summary = summary[0].upper() + summary[1:]
    coverage = _coverage_line(sources)
    if coverage:
        summary += " " + coverage
    return summary, {
        "done": done,
        "left": left,
        "missed_calls": {
            "today": len(today_calls),
            "handled": len(handled),
            "waiting": [
                {"id": c["id"], "caller": c["caller"], "at": c["at"]}
                for c in waiting[:5]
            ],
        },
    }


def brief_view(row: DailyBriefModel | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {
        "id": row.id,
        "kind": row.kind,
        "date": row.occurrence_key,
        "timezone": row.timezone,
        "period_start": row.period_start.isoformat(),
        "period_end": row.period_end.isoformat(),
        "covered": f"{full_local(row.period_start, row.timezone)} to {full_local(row.period_end - timedelta(minutes=1), row.timezone)}",
        "refreshed_at": row.refreshed_at.isoformat(),
        "status": row.status,
        "summary": row.summary,
        "sources": list(row.sources or []),
        "sections": dict(row.sections or {}),
        "delivered_at": row.delivered_at.isoformat() if row.delivered_at else None,
    }


async def build(
    viewer: Viewer,
    *,
    kind: str = BRIEF,
    day: date | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Build (or rebuild) this person's brief for ``day`` and store it on
    the day's one row. Refresh updates the existing brief."""
    if kind not in KINDS:
        raise TodayError("Not a kind of brief.")
    now = now or datetime.now(UTC)
    zone = viewer.zone
    day = day or local_today(zone, now)
    start, end = day_bounds(day, zone)
    sources = await _read_all(viewer, start, end)
    if kind == BRIEF:
        summary, sections = _compose_brief(sources, viewer)
    else:
        summary, sections = await _compose_end_of_day(viewer, sources, start, end)
    status = (
        "partial" if any(s.status == FAILED for s in sources.values()) else "complete"
    )
    if all(s.status == FAILED for s in sources.values()):
        status = "failed"
    values = {
        "organization_id": viewer.organization_id,
        "user_id": viewer.user_id,
        "kind": kind,
        "occurrence_key": day.isoformat(),
        "timezone": viewer.zone_name,
        "period_start": start,
        "period_end": end,
        "refreshed_at": now,
        "status": status,
        "summary": summary,
        "sources": [s.view() for s in sources.values()],
        "sections": sections,
        "created_at": now,
    }
    stmt = insert(DailyBriefModel).values(**values)
    stmt = stmt.on_conflict_do_update(
        constraint="uq_daily_brief_occurrence",
        set_={
            k: stmt.excluded[k]
            for k in (
                "refreshed_at",
                "status",
                "summary",
                "sources",
                "sections",
                "timezone",
                "period_start",
                "period_end",
            )
        },
    ).returning(DailyBriefModel.id)
    async with db_client.async_session() as session:
        brief_id = await session.scalar(stmt)
        await session.commit()
        row = await session.get(DailyBriefModel, brief_id)
        return brief_view(row)


async def latest(viewer: Viewer, *, kind: str = BRIEF) -> dict[str, Any] | None:
    """The newest brief of ``kind`` for this person here. An older brief is
    kept with its original refresh time; the screen labels it stale."""
    async with db_client.async_session() as session:
        row = await session.scalar(
            select(DailyBriefModel)
            .where(
                and_(
                    DailyBriefModel.organization_id == viewer.organization_id,
                    DailyBriefModel.user_id == viewer.user_id,
                    DailyBriefModel.kind == kind,
                )
            )
            .order_by(DailyBriefModel.occurrence_key.desc())
            .limit(1)
        )
    view = brief_view(row)
    if view is not None:
        view["stale"] = view["date"] != local_today(viewer.zone).isoformat()
    return view


async def claim_delivery(brief_id: int, now: datetime) -> bool:
    """Mark a brief delivered once. False if something already did."""
    async with db_client.async_session() as session:
        result = await session.execute(
            update(DailyBriefModel)
            .where(
                and_(
                    DailyBriefModel.id == brief_id,
                    DailyBriefModel.delivered_at.is_(None),
                )
            )
            .values(delivered_at=now)
        )
        await session.commit()
        return (result.rowcount or 0) == 1


def text_of(view: dict[str, Any]) -> str:
    """The brief as one message, for WhatsApp: the summary, then the lines
    that matter, then coverage."""
    title = "Your brief" if view["kind"] == BRIEF else "End of day"
    lines = [f"{title} for {view['date']}", view["summary"]]
    sections = view.get("sections") or {}
    for item in (sections.get("appointments") or {}).get("items", [])[:5]:
        lines.append(
            f"• {item.get('title')} {item.get('when') or item.get('at') or ''}".rstrip()
        )
    for item in (sections.get("due") or [])[:5]:
        lines.append(f"• Due: {item.get('title')}")
    for item in (sections.get("done") or [])[:5]:
        lines.append(f"✓ {item.get('title')}")
    missing = [
        s["detail"]
        for s in view.get("sources", [])
        if s["status"] != READ and s.get("detail")
    ]
    if missing:
        lines.append(" ".join(missing))
    return "\n".join(lines)


# --- settings --------------------------------------------------------------------


def _settings_view(
    row: DailyBriefSettingsModel | None, viewer: Viewer
) -> dict[str, Any]:
    if row is None:
        return {
            "enabled": False,
            "paused": False,
            "local_time": SUGGESTED_TIME,
            "timezone": viewer.zone_name,
            "days": [0, 1, 2, 3, 4, 5, 6],
            "channels": ["in_app"],
            "quiet_start": "21:00",
            "quiet_end": "08:00",
            "end_of_day_enabled": False,
            "end_of_day_time": "18:00",
            "revision": 0,
            "saved": False,
        }
    return {
        "enabled": row.enabled,
        "paused": row.paused,
        "local_time": row.local_time,
        "timezone": row.timezone,
        "days": list(row.days or []),
        "channels": list(row.channels or []),
        "quiet_start": row.quiet_start,
        "quiet_end": row.quiet_end,
        "end_of_day_enabled": row.end_of_day_enabled,
        "end_of_day_time": row.end_of_day_time,
        "revision": row.revision,
        "saved": True,
    }


def next_occurrence(
    settings: dict[str, Any], *, kind: str = BRIEF, now: datetime | None = None
) -> datetime | None:
    now = now or datetime.now(UTC)
    if kind == BRIEF and (not settings["enabled"] or settings["paused"]):
        return None
    if kind == END_OF_DAY and (
        not settings["end_of_day_enabled"] or settings["paused"]
    ):
        return None
    hhmm = parse_hhmm(
        settings["local_time"] if kind == BRIEF else settings["end_of_day_time"]
    )
    zone = ZoneInfo(settings["timezone"])
    days = set(settings["days"] or [])
    start = now.astimezone(zone).date()
    for offset in range(8):
        day = start + timedelta(days=offset)
        if day.weekday() not in days:
            continue
        moment = at_local(day, hhmm, zone)
        if moment > now:
            return moment
    return None


async def _settings_row(session: Any, viewer: Viewer) -> DailyBriefSettingsModel | None:
    return await session.scalar(
        select(DailyBriefSettingsModel).where(
            and_(
                DailyBriefSettingsModel.organization_id == viewer.organization_id,
                DailyBriefSettingsModel.user_id == viewer.user_id,
            )
        )
    )


async def get_settings(viewer: Viewer) -> dict[str, Any]:
    from api.services.today import delivery

    async with db_client.async_session() as session:
        view = _settings_view(await _settings_row(session, viewer), viewer)
    view["channel_states"] = [
        await delivery.channel_state(viewer.organization_id, viewer.user_id, c)
        for c in CHANNELS
    ]
    nxt = next_occurrence(view)
    view["next_at"] = nxt.isoformat() if nxt else None
    view["next_sentence"] = (
        f"Next brief: {full_local(nxt, view['timezone'])}."
        if nxt
        else _off_sentence(view)
    )
    eod = next_occurrence(view, kind=END_OF_DAY)
    view["end_of_day_next"] = full_local(eod, view["timezone"]) if eod else None
    view["sources"] = [
        {"kind": k, "label": label}
        for k, label in (
            ("approvals", "Approvals"),
            ("calendar", "Calendar"),
            ("reminders", "Reminders and events"),
            ("tasks", "Tasks"),
            ("missed_calls", "Missed calls"),
            ("mail", "Mail"),
        )
    ]
    return view


def _off_sentence(view: dict[str, Any]) -> str:
    if view["paused"]:
        return "Paused. Your settings are kept; nothing new is sent until you resume."
    if not view["enabled"]:
        return "Off. Turn it on to get one short brief a day."
    return "No day is chosen, so no brief is scheduled."


def _validate(changes: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in changes.items():
        if key in ("enabled", "paused", "end_of_day_enabled"):
            out[key] = bool(value)
        elif key in ("local_time", "quiet_start", "quiet_end", "end_of_day_time"):
            if parse_hhmm(value) is None:
                raise TodayError("Say times as HH:MM, like 09:00.")
            out[key] = value
        elif key == "timezone":
            if not valid_zone(value):
                raise TodayError(f"{value!r} is not a timezone we know.")
            out[key] = value
        elif key == "days":
            days = sorted({int(d) for d in (value or [])})
            if any(d < 0 or d > 6 for d in days):
                raise TodayError("Days are Monday to Sunday.")
            out[key] = days
        elif key == "channels":
            channels = [c for c in dict.fromkeys(value or [])]
            if not channels or any(c not in CHANNELS for c in channels):
                raise TodayError(
                    "Choose at least one of: in the app, WhatsApp, phone notification."
                )
            out[key] = channels
        else:
            raise TodayError(f"{key} is not a brief setting.")
    return out


async def save_settings(
    viewer: Viewer, changes: dict[str, Any], *, revision: int
) -> dict[str, Any]:
    """Save the fields sent. A save naming an older revision is a conflict
    that shows the stored value (design: "Save state")."""
    clean = _validate(changes)
    now = datetime.now(UTC)
    async with db_client.async_session() as session:
        row = await _settings_row(session, viewer)
        if row is None:
            if revision != 0:
                raise Conflict(stored=_settings_view(None, viewer))
            base = _settings_view(None, viewer)
            row = DailyBriefSettingsModel(
                organization_id=viewer.organization_id,
                user_id=viewer.user_id,
                enabled=base["enabled"],
                paused=base["paused"],
                local_time=base["local_time"],
                timezone=base["timezone"],
                days=base["days"],
                channels=base["channels"],
                quiet_start=base["quiet_start"],
                quiet_end=base["quiet_end"],
                end_of_day_enabled=base["end_of_day_enabled"],
                end_of_day_time=base["end_of_day_time"],
                revision=0,
                updated_at=now,
            )
            session.add(row)
        elif row.revision != revision:
            raise Conflict(stored=_settings_view(row, viewer))
        for key, value in clean.items():
            setattr(row, key, value)
        row.revision = (row.revision or 0) + 1
        row.updated_at = now
        try:
            await session.commit()
        except Exception as exc:  # the unique owner constraint: a racing first save
            await session.rollback()
            existing = await _settings_row(session, viewer)
            raise Conflict(stored=_settings_view(existing, viewer)) from exc
    return await get_settings(viewer)


async def due_now(now: datetime) -> list[tuple[DailyBriefSettingsModel, str, date]]:
    """Every (settings, kind, local day) whose time has come within the
    catch-up window. Read by the minute tick."""
    async with db_client.async_session() as session:
        rows = list(
            (
                await session.execute(
                    select(DailyBriefSettingsModel).where(
                        and_(
                            DailyBriefSettingsModel.paused.is_(False),
                            or_(
                                DailyBriefSettingsModel.enabled.is_(True),
                                DailyBriefSettingsModel.end_of_day_enabled.is_(True),
                            ),
                        )
                    )
                )
            ).scalars()
        )
    due = []
    for row in rows:
        zone = ZoneInfo(row.timezone)
        local = now.astimezone(zone)
        if local.date().weekday() not in set(row.days or []):
            continue
        for kind, enabled, hhmm in (
            (BRIEF, row.enabled, row.local_time),
            (END_OF_DAY, row.end_of_day_enabled, row.end_of_day_time),
        ):
            if not enabled:
                continue
            slot = at_local(local.date(), parse_hhmm(hhmm), zone)
            late = (now - slot).total_seconds() / 60
            if 0 <= late <= CATCH_UP_MINUTES:
                due.append((row, kind, local.date()))
    return due


__all__ = [
    "BRIEF",
    "END_OF_DAY",
    "READERS",
    "Source",
    "brief_view",
    "build",
    "claim_delivery",
    "due_now",
    "get_settings",
    "latest",
    "next_occurrence",
    "save_settings",
    "suggestions_from",
    "text_of",
]
