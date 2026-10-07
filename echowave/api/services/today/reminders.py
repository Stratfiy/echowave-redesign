"""Reminders, events, and reminders linked to an event (handoff 22; screen 10).

Three rules from the handoff shape this file:

* **Event time and reminder time are separate.** An event is something at a
  confirmed time; a reminder about it is ``event.starts_at + offset``
  ("At event time" is 0, "One day before" is -1440). Moving the event
  recalculates each linked reminder by its approved offset and keeps the old
  and new times; cancelling the event cancels them.
* **Nothing is scheduled the person did not see.** :func:`preview` says the
  next occurrence as a full local date with the timezone and returns a
  ``schedule_key``; a save must send that key back, so a save of a schedule
  that changed since it was shown is refused rather than stored.
* **"Tomorrow" is the person's tomorrow**, resolved in their timezone and
  shown as a full date before anything is saved.

Every read and write is scoped to the viewer's (organisation, person); a
colleague's reminder is "not here".
"""

from __future__ import annotations

import hashlib
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import and_, select

from api.db import db_client
from api.db.today_models import TodayEventModel, TodayReminderModel
from api.services.today.scope import (
    Conflict,
    NotFound,
    TodayError,
    Viewer,
    at_local,
    full_local,
    local_today,
    parse_hhmm,
    valid_zone,
)

RECURRENCES = ("once", "daily", "weekdays", "weekly")
CHANNELS = ("in_app", "whatsapp", "push")
STATUSES = ("active", "paused", "done", "missed", "cancelled")
#: The two offsets the handoff offers for an event reminder.
AT_EVENT = 0
DAY_BEFORE = -24 * 60
OFFSET_WORDS = {AT_EVENT: "At event time", DAY_BEFORE: "One day before"}
#: How far ahead a recurrence is searched for its next day.
_SEARCH_DAYS = 15
MAX_TITLE = 200


def _now() -> datetime:
    return datetime.now(UTC)


# --- dates in words ----------------------------------------------------------


def resolve_date(words: str, zone_name: str, *, now: datetime | None = None) -> date:
    """``today``, ``tomorrow`` or ``YYYY-MM-DD``, in the person's zone."""
    zone = ZoneInfo(zone_name)
    key = (words or "").strip().lower()
    today = local_today(zone, now)
    if key == "today":
        return today
    if key == "tomorrow":
        return today + timedelta(days=1)
    try:
        return date.fromisoformat(key)
    except ValueError:
        raise TodayError(
            "Say the day as today, tomorrow or a date like 2026-10-09."
        ) from None


def _offset_words(minutes: int | None) -> str:
    if minutes is None:
        return ""
    if minutes in OFFSET_WORDS:
        return OFFSET_WORDS[minutes]
    span = abs(minutes)
    unit = f"{span // 60} hours" if span % 60 == 0 else f"{span} minutes"
    return f"{unit} {'before' if minutes < 0 else 'after'}"


# --- the schedule ------------------------------------------------------------


def _matches(recurrence: str, day: date, weekday: int | None) -> bool:
    if recurrence == "daily":
        return True
    if recurrence == "weekdays":
        return day.weekday() < 5
    if recurrence == "weekly":
        return day.weekday() == (weekday or 0)
    return False


def next_recurring(
    *,
    recurrence: str,
    local_time: str,
    weekday: int | None,
    zone_name: str,
    after: datetime,
) -> datetime | None:
    """The first occurrence strictly after ``after``. DST changes move the
    wall time predictably: each day's instant is computed in the zone on its
    own date, so 09:00 stays 09:00 local across a change."""
    hhmm = parse_hhmm(local_time)
    if hhmm is None:
        return None
    zone = ZoneInfo(zone_name)
    start = after.astimezone(zone).date()
    for offset in range(_SEARCH_DAYS):
        day = start + timedelta(days=offset)
        if not _matches(recurrence, day, weekday):
            continue
        candidate = at_local(day, hhmm, zone)
        if candidate > after:
            return candidate
    return None


def _schedule_key(draft: dict[str, Any], next_at: datetime | None) -> str:
    canonical = "|".join(
        str(x)
        for x in (
            next_at.isoformat() if next_at else "none",
            draft.get("recurrence"),
            draft.get("local_time"),
            draft.get("weekday"),
            draft.get("timezone"),
            draft.get("channel"),
            draft.get("event_id"),
            draft.get("offset_minutes"),
        )
    )
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]


async def _event(viewer: Viewer, event_id: int) -> TodayEventModel:
    async with db_client.async_session() as session:
        row = await session.scalar(
            select(TodayEventModel).where(
                and_(
                    TodayEventModel.id == event_id,
                    TodayEventModel.organization_id == viewer.organization_id,
                    TodayEventModel.user_id == viewer.user_id,
                )
            )
        )
    if row is None:
        raise NotFound("That event is not here.")
    return row


def _clean(draft: dict[str, Any], viewer: Viewer) -> dict[str, Any]:
    """Validate a reminder draft. Raises TodayError with the reason."""
    title = str(draft.get("title") or "").strip()
    if not title:
        raise TodayError("Say what to be reminded about.")
    if len(title) > MAX_TITLE:
        raise TodayError(f"Keep the title under {MAX_TITLE} characters.")
    zone_name = draft.get("timezone") or viewer.zone_name
    if not valid_zone(zone_name):
        raise TodayError(f"{zone_name!r} is not a timezone we know.")
    recurrence = str(draft.get("recurrence") or "once")
    if recurrence not in RECURRENCES:
        raise TodayError("Repeat once, daily, on weekdays or weekly.")
    channel = str(draft.get("channel") or "in_app")
    if channel not in CHANNELS:
        raise TodayError("Deliver in the app, on WhatsApp or as a phone notification.")
    out = {
        "title": title,
        "note": str(draft.get("note") or "")[:2000],
        "timezone": zone_name,
        "recurrence": recurrence,
        "channel": channel,
        "event_id": draft.get("event_id"),
        "offset_minutes": draft.get("offset_minutes"),
        "local_time": draft.get("local_time"),
        "weekday": draft.get("weekday"),
        "date": draft.get("date"),
    }
    if out["event_id"] is not None:
        out["recurrence"] = "once"
        try:
            out["offset_minutes"] = int(out["offset_minutes"] or 0)
        except (TypeError, ValueError):
            raise TodayError("Say how long before the event.") from None
        if not -30 * 24 * 60 <= out["offset_minutes"] <= 24 * 60:
            raise TodayError("A reminder can be up to 30 days before the event.")
    else:
        if parse_hhmm(out["local_time"]) is None:
            raise TodayError("Say the time as HH:MM, like 09:00.")
        if recurrence == "weekly":
            try:
                out["weekday"] = int(out["weekday"])
            except (TypeError, ValueError):
                raise TodayError("Say which day of the week.") from None
            if not 0 <= out["weekday"] <= 6:
                raise TodayError("Say which day of the week.")
        if recurrence == "once" and not out["date"]:
            raise TodayError("Say which day: today, tomorrow or a date.")
    return out


async def _next_for(
    clean: dict[str, Any], viewer: Viewer, now: datetime
) -> tuple[datetime | None, dict[str, Any] | None]:
    """The next occurrence of a cleaned draft, and its event if linked."""
    if clean["event_id"] is not None:
        event = await _event(viewer, int(clean["event_id"]))
        if event.status != "active":
            raise TodayError("That event was cancelled.")
        return event.starts_at + timedelta(
            minutes=clean["offset_minutes"]
        ), _event_view(event)
    if clean["recurrence"] == "once":
        day = resolve_date(str(clean["date"]), clean["timezone"], now=now)
        hhmm = parse_hhmm(clean["local_time"])
        return at_local(day, hhmm, ZoneInfo(clean["timezone"])), None
    return (
        next_recurring(
            recurrence=clean["recurrence"],
            local_time=clean["local_time"],
            weekday=clean["weekday"],
            zone_name=clean["timezone"],
            after=now,
        ),
        None,
    )


def _sentence(
    clean: dict[str, Any], next_at: datetime | None, event: dict | None
) -> str:
    if next_at is None:
        return "This does not come round in the next two weeks."
    when = full_local(next_at, clean["timezone"])
    if event is not None:
        return f"{_offset_words(clean['offset_minutes'])} {event['title']}: {when}."
    repeat = {
        "once": "Once",
        "daily": "Every day",
        "weekdays": "Every weekday",
        "weekly": "Every week",
    }[clean["recurrence"]]
    return f"{repeat}. Next: {when}."


async def preview(
    viewer: Viewer, draft: dict[str, Any], *, now: datetime | None = None
) -> dict[str, Any]:
    """The live next-occurrence sentence shown directly above Save, with the
    states the editor must show (invalid past date, timezone conflict)."""
    now = now or _now()
    clean = _clean(draft, viewer)
    next_at, event = await _next_for(clean, viewer, now)
    problems: list[dict[str, str]] = []
    if next_at is not None and next_at <= now:
        problems.append(
            {
                "code": "invalid_past",
                "message": f"{full_local(next_at, clean['timezone'])} has already passed.",
            }
        )
    if clean["timezone"] != viewer.zone_name:
        problems.append(
            {
                "code": "timezone_conflict",
                "message": (
                    f"This reminder is set in {clean['timezone']}; your timezone is "
                    f"{viewer.zone_name}."
                ),
            }
        )
    return {
        "next_at": next_at.isoformat() if next_at else None,
        "sentence": _sentence(clean, next_at, event),
        "schedule_key": _schedule_key(clean, next_at),
        "timezone": clean["timezone"],
        "problems": problems,
        "event": event,
    }


# --- views -------------------------------------------------------------------


def _event_view(row: TodayEventModel) -> dict[str, Any]:
    return {
        "id": row.id,
        "title": row.title,
        "starts_at": row.starts_at.isoformat(),
        "timezone": row.timezone,
        "when": full_local(row.starts_at, row.timezone),
        "status": row.status,
        "revision": row.revision,
    }


def reminder_view(row: TodayReminderModel) -> dict[str, Any]:
    return {
        "id": row.id,
        "title": row.title,
        "note": row.note,
        "event_id": row.event_id,
        "offset_minutes": row.offset_minutes,
        "offset_words": _offset_words(row.offset_minutes) or None,
        "recurrence": row.recurrence,
        "local_time": row.local_time,
        "weekday": row.weekday,
        "timezone": row.timezone,
        "remind_at": row.remind_at.isoformat() if row.remind_at else None,
        "when": full_local(row.remind_at, row.timezone) if row.remind_at else None,
        "channel": row.channel,
        "status": row.status,
        "revision": row.revision,
        "history": list(row.history or [])[-5:],
        "last_delivered_at": row.last_delivered_at.isoformat()
        if row.last_delivered_at
        else None,
    }


async def _emit(name: str, viewer: Viewer, row: Any, **properties: Any) -> None:
    from api.services import events

    await events.emit(
        name,
        user_id=viewer.user_id,
        organization_id=viewer.organization_id,
        task_id=f"reminder:{row.id}",
        properties={k: v for k, v in properties.items() if v is not None},
    )


# --- reminders -----------------------------------------------------------------


async def _reminder(
    session: Any, viewer: Viewer, reminder_id: int
) -> TodayReminderModel:
    row = await session.scalar(
        select(TodayReminderModel).where(
            and_(
                TodayReminderModel.id == reminder_id,
                TodayReminderModel.organization_id == viewer.organization_id,
                TodayReminderModel.user_id == viewer.user_id,
            )
        )
    )
    if row is None:
        raise NotFound("That reminder is not here.")
    return row


async def list_for(viewer: Viewer, *, include_finished: bool = False) -> dict[str, Any]:
    async with db_client.async_session() as session:
        query = select(TodayReminderModel).where(
            and_(
                TodayReminderModel.organization_id == viewer.organization_id,
                TodayReminderModel.user_id == viewer.user_id,
            )
        )
        if not include_finished:
            query = query.where(
                TodayReminderModel.status.in_(("active", "paused", "missed"))
            )
        reminders = list(
            (
                await session.execute(
                    query.order_by(TodayReminderModel.remind_at.asc().nulls_last())
                )
            ).scalars()
        )
        events = list(
            (
                await session.execute(
                    select(TodayEventModel)
                    .where(
                        and_(
                            TodayEventModel.organization_id == viewer.organization_id,
                            TodayEventModel.user_id == viewer.user_id,
                            TodayEventModel.status == "active",
                        )
                    )
                    .order_by(TodayEventModel.starts_at.asc())
                )
            ).scalars()
        )
    return {
        "reminders": [reminder_view(r) for r in reminders],
        "events": [_event_view(e) for e in events],
        "timezone": viewer.zone_name,
    }


async def get(viewer: Viewer, reminder_id: int) -> dict[str, Any]:
    async with db_client.async_session() as session:
        return reminder_view(await _reminder(session, viewer, reminder_id))


async def _confirmed(
    viewer: Viewer, draft: dict[str, Any], schedule_key: str | None, now: datetime
) -> tuple[dict, datetime]:
    shown = await preview(viewer, draft, now=now)
    if not schedule_key or schedule_key != shown["schedule_key"]:
        raise TodayError(
            "The schedule is not the one you confirmed. Check the next time and save again."
        )
    if any(p["code"] == "invalid_past" for p in shown["problems"]):
        raise TodayError(shown["problems"][0]["message"])
    if shown["next_at"] is None:
        raise TodayError(shown["sentence"])
    return _clean(draft, viewer), datetime.fromisoformat(shown["next_at"])


async def create(
    viewer: Viewer,
    draft: dict[str, Any],
    *,
    schedule_key: str | None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Save a reminder the person confirmed. One row per save."""
    now = now or _now()
    clean, next_at = await _confirmed(viewer, draft, schedule_key, now)
    async with db_client.async_session() as session:
        row = TodayReminderModel(
            organization_id=viewer.organization_id,
            user_id=viewer.user_id,
            title=clean["title"],
            note=clean["note"],
            event_id=clean["event_id"],
            offset_minutes=clean["offset_minutes"]
            if clean["event_id"] is not None
            else None,
            recurrence=clean["recurrence"],
            local_time=clean["local_time"] if clean["event_id"] is None else None,
            weekday=clean["weekday"] if clean["recurrence"] == "weekly" else None,
            timezone=clean["timezone"],
            remind_at=next_at,
            channel=clean["channel"],
            status="active",
            revision=1,
            history=[],
            created_at=now,
            updated_at=now,
        )
        session.add(row)
        await session.commit()
        await session.refresh(row)
        view = reminder_view(row)
    await _emit(
        "reminder_scheduled", viewer, row, channel=clean["channel"], status="active"
    )
    return view


async def update(
    viewer: Viewer,
    reminder_id: int,
    draft: dict[str, Any],
    *,
    revision: int,
    schedule_key: str | None,
    now: datetime | None = None,
) -> dict[str, Any]:
    now = now or _now()
    clean, next_at = await _confirmed(viewer, draft, schedule_key, now)
    async with db_client.async_session() as session:
        row = await _reminder(session, viewer, reminder_id)
        if row.revision != revision:
            raise Conflict(stored=reminder_view(row))
        if row.status == "cancelled":
            raise TodayError("That reminder was cancelled.")
        before = row.remind_at
        row.title = clean["title"]
        row.note = clean["note"]
        row.event_id = clean["event_id"]
        row.offset_minutes = (
            clean["offset_minutes"] if clean["event_id"] is not None else None
        )
        row.recurrence = clean["recurrence"]
        row.local_time = clean["local_time"] if clean["event_id"] is None else None
        row.weekday = clean["weekday"] if clean["recurrence"] == "weekly" else None
        row.timezone = clean["timezone"]
        row.channel = clean["channel"]
        row.remind_at = next_at
        if row.status in ("done", "missed"):
            row.status = "active"
        if before != next_at:
            row.history = [
                *list(row.history or [])[-9:],
                {
                    "at": now.isoformat(),
                    "why": "edited",
                    "old": before.isoformat() if before else None,
                    "new": next_at.isoformat(),
                },
            ]
        row.revision = row.revision + 1
        row.updated_at = now
        await session.commit()
        await session.refresh(row)
        return reminder_view(row)


async def set_status(
    viewer: Viewer, reminder_id: int, verb: str, *, now: datetime | None = None
) -> dict[str, Any]:
    """pause / resume / cancel / complete. Pause stops future occurrences;
    one already being delivered finishes (deliveries are claimed before
    sending), which the editor says in words."""
    now = now or _now()
    async with db_client.async_session() as session:
        row = await _reminder(session, viewer, reminder_id)
        if verb == "pause":
            if row.status != "active":
                raise TodayError("Only an active reminder can be paused.")
            row.status = "paused"
        elif verb == "resume":
            if row.status != "paused":
                raise TodayError("Only a paused reminder can be resumed.")
            if row.recurrence != "once":
                row.remind_at = next_recurring(
                    recurrence=row.recurrence,
                    local_time=row.local_time,
                    weekday=row.weekday,
                    zone_name=row.timezone,
                    after=now,
                )
            row.status = "active" if row.remind_at and row.remind_at > now else "missed"
        elif verb == "cancel":
            if row.status == "cancelled":
                return reminder_view(row)
            row.status = "cancelled"
        elif verb == "complete":
            row.status = "done"
        else:
            raise TodayError("Pause, resume, cancel or complete.")
        row.revision = row.revision + 1
        row.updated_at = now
        await session.commit()
        await session.refresh(row)
        view = reminder_view(row)
    if verb == "cancel":
        await _emit("reminder_cancelled", viewer, row, status="cancelled")
    return view


async def snooze(
    viewer: Viewer, reminder_id: int, minutes: int, *, now: datetime | None = None
) -> dict[str, Any]:
    now = now or _now()
    if not 5 <= int(minutes) <= 24 * 60:
        raise TodayError("Snooze for between 5 minutes and a day.")
    async with db_client.async_session() as session:
        row = await _reminder(session, viewer, reminder_id)
        if row.status not in ("active", "missed"):
            raise TodayError("Only a waiting reminder can be snoozed.")
        new = now + timedelta(minutes=int(minutes))
        row.history = [
            *list(row.history or [])[-9:],
            {
                "at": now.isoformat(),
                "why": "snoozed",
                "old": row.remind_at.isoformat() if row.remind_at else None,
                "new": new.isoformat(),
            },
        ]
        row.remind_at = new
        row.status = "active"
        row.revision = row.revision + 1
        row.updated_at = now
        await session.commit()
        await session.refresh(row)
        return reminder_view(row)


# --- events --------------------------------------------------------------------


async def create_event(
    viewer: Viewer,
    *,
    title: str,
    day: str,
    local_time: str,
    timezone: str | None = None,
    reminders: list[int] | None = None,
    channel: str = "in_app",
    now: datetime | None = None,
) -> dict[str, Any]:
    """An event at a confirmed time, with the reminders the person chose
    (``reminders`` is a list of offsets: 0 at event time, -1440 a day before)."""
    now = now or _now()
    title = (title or "").strip()
    if not title or len(title) > MAX_TITLE:
        raise TodayError("Say what the event is, in under 200 characters.")
    zone_name = timezone or viewer.zone_name
    if not valid_zone(zone_name):
        raise TodayError(f"{zone_name!r} is not a timezone we know.")
    hhmm = parse_hhmm(local_time)
    if hhmm is None:
        raise TodayError("Say the event's time as HH:MM. We never guess one.")
    if channel not in CHANNELS:
        raise TodayError("Deliver in the app, on WhatsApp or as a phone notification.")
    starts = at_local(resolve_date(day, zone_name, now=now), hhmm, ZoneInfo(zone_name))
    if starts <= now:
        raise TodayError(f"{full_local(starts, zone_name)} has already passed.")
    offsets = sorted({int(o) for o in (reminders or [])})
    async with db_client.async_session() as session:
        event = TodayEventModel(
            organization_id=viewer.organization_id,
            user_id=viewer.user_id,
            title=title,
            starts_at=starts,
            timezone=zone_name,
            status="active",
            revision=1,
            created_at=now,
            updated_at=now,
        )
        session.add(event)
        await session.flush()
        linked = []
        for offset in offsets:
            remind_at = starts + timedelta(minutes=offset)
            row = TodayReminderModel(
                organization_id=viewer.organization_id,
                user_id=viewer.user_id,
                title=title,
                note="",
                event_id=event.id,
                offset_minutes=offset,
                recurrence="once",
                timezone=zone_name,
                remind_at=remind_at,
                channel=channel,
                # A day-before reminder for an event tomorrow morning may
                # already be past: said, not silently dropped.
                status="active" if remind_at > now else "missed",
                revision=1,
                history=[],
                created_at=now,
                updated_at=now,
            )
            session.add(row)
            linked.append(row)
        await session.commit()
        await session.refresh(event)
        for row in linked:
            await session.refresh(row)
        view = {**_event_view(event), "reminders": [reminder_view(r) for r in linked]}
    for row in linked:
        await _emit(
            "reminder_scheduled", viewer, row, channel=channel, status=row.status
        )
    return view


async def move_event(
    viewer: Viewer,
    event_id: int,
    *,
    day: str,
    local_time: str,
    revision: int,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Move an event and recalculate its linked reminders once, by their
    approved offsets. Returns the event and each change (old and new), with
    a conflict named rather than chosen silently."""
    now = now or _now()
    hhmm = parse_hhmm(local_time)
    if hhmm is None:
        raise TodayError("Say the event's time as HH:MM.")
    async with db_client.async_session() as session:
        event = await session.scalar(
            select(TodayEventModel)
            .where(
                and_(
                    TodayEventModel.id == event_id,
                    TodayEventModel.organization_id == viewer.organization_id,
                    TodayEventModel.user_id == viewer.user_id,
                )
            )
            .with_for_update()
        )
        if event is None:
            raise NotFound("That event is not here.")
        if event.revision != revision:
            raise Conflict(stored=_event_view(event))
        if event.status != "active":
            raise TodayError("That event was cancelled.")
        starts = at_local(
            resolve_date(day, event.timezone, now=now), hhmm, ZoneInfo(event.timezone)
        )
        if starts <= now:
            raise TodayError(
                f"{full_local(starts, event.timezone)} has already passed."
            )
        old_start = event.starts_at
        event.starts_at = starts
        event.revision = event.revision + 1
        event.updated_at = now
        linked = list(
            (
                await session.execute(
                    select(TodayReminderModel).where(
                        and_(
                            TodayReminderModel.event_id == event.id,
                            TodayReminderModel.organization_id
                            == viewer.organization_id,
                            TodayReminderModel.user_id == viewer.user_id,
                            TodayReminderModel.status.in_(
                                ("active", "paused", "missed")
                            ),
                        )
                    )
                )
            ).scalars()
        )
        changes = []
        for row in linked:
            old = row.remind_at
            new = starts + timedelta(minutes=row.offset_minutes or 0)
            row.remind_at = new
            row.history = [
                *list(row.history or [])[-9:],
                {
                    "at": now.isoformat(),
                    "why": "event_moved",
                    "old": old.isoformat() if old else None,
                    "new": new.isoformat(),
                },
            ]
            conflict = None
            if new <= now:
                row.status = "missed"
                conflict = "The new reminder time has already passed."
            elif row.status == "missed":
                row.status = "active"
            row.revision = row.revision + 1
            row.updated_at = now
            changes.append(
                {
                    "reminder_id": row.id,
                    "offset_words": _offset_words(row.offset_minutes),
                    "old": full_local(old, row.timezone) if old else None,
                    "new": full_local(new, row.timezone),
                    "conflict": conflict,
                }
            )
        await session.commit()
        await session.refresh(event)
        return {
            **_event_view(event),
            "was": full_local(old_start, event.timezone),
            "changes": changes,
        }


async def cancel_event(
    viewer: Viewer, event_id: int, *, now: datetime | None = None
) -> dict[str, Any]:
    """Cancel an event and every reminder linked to it, once: a second
    cancel finds nothing left to cancel."""
    now = now or _now()
    async with db_client.async_session() as session:
        event = await session.scalar(
            select(TodayEventModel)
            .where(
                and_(
                    TodayEventModel.id == event_id,
                    TodayEventModel.organization_id == viewer.organization_id,
                    TodayEventModel.user_id == viewer.user_id,
                )
            )
            .with_for_update()
        )
        if event is None:
            raise NotFound("That event is not here.")
        cancelled = []
        if event.status != "cancelled":
            event.status = "cancelled"
            event.revision = event.revision + 1
            event.updated_at = now
            linked = list(
                (
                    await session.execute(
                        select(TodayReminderModel).where(
                            and_(
                                TodayReminderModel.event_id == event.id,
                                TodayReminderModel.organization_id
                                == viewer.organization_id,
                                TodayReminderModel.user_id == viewer.user_id,
                                TodayReminderModel.status != "cancelled",
                            )
                        )
                    )
                ).scalars()
            )
            for row in linked:
                row.status = "cancelled"
                row.revision = row.revision + 1
                row.updated_at = now
                cancelled.append(row.id)
        await session.commit()
        await session.refresh(event)
        view = {**_event_view(event), "cancelled_reminders": cancelled}
    for reminder_id in cancelled:
        await _emit(
            "reminder_cancelled",
            viewer,
            SimpleNamespace(id=reminder_id),
            status="cancelled",
            reason_code="event_cancelled",
        )
    return view


__all__ = [
    "AT_EVENT",
    "CHANNELS",
    "DAY_BEFORE",
    "RECURRENCES",
    "cancel_event",
    "create",
    "create_event",
    "get",
    "list_for",
    "move_event",
    "next_recurring",
    "preview",
    "reminder_view",
    "resolve_date",
    "set_status",
    "snooze",
    "update",
]
