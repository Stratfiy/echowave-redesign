"""Today, approvals, reminders and the daily brief (launch stream `today`).

Thin: each handler resolves the viewer (person, workspace, timezone) and
delegates to ``services/today``. Every group is a 404 while its flag is off
for the caller's workspace, so turning a flag off restores today's
behaviour. Approving and declining are not here: they are the controls
stream's ``POST /timeline/actions/settle`` with the version shown.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from api.db.models import UserModel
from api.services import features
from api.services.auth.depends import get_user
from api.services.today import (
    APPROVAL_DOCK,
    DAILY_BRIEF,
    END_OF_DAY,
    REMINDERS,
    TODAY_LIST,
    activity,
    approvals,
    brief,
    delivery,
    listing,
    reminders,
    ticks,
)
from api.services.today.scope import Conflict, NotFound, TodayError, viewer_for

router = APIRouter(prefix="/today", tags=["today"])


def _any_of(*names: str):
    """404 unless one of ``names`` is on for the caller's workspace."""

    def dependency(user: UserModel = Depends(get_user)) -> None:
        org = getattr(user, "selected_organization_id", None)
        if not any(features.is_on(name, org) for name in names):
            raise HTTPException(status_code=404, detail="Not Found")

    dependency.feature = names  # read by tests
    return dependency


_list_on = [Depends(features.require(TODAY_LIST, per_organization=True))]
_approvals_on = [Depends(_any_of(TODAY_LIST, APPROVAL_DOCK))]
_reminders_on = [Depends(features.require(REMINDERS, per_organization=True))]
_brief_on = [Depends(features.require(DAILY_BRIEF, per_organization=True))]
_eod_on = [Depends(features.require(END_OF_DAY, per_organization=True))]


async def _viewer(user: UserModel):
    try:
        return await viewer_for(user)
    except TodayError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


async def _run(coro):
    try:
        return await coro
    except NotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Conflict as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "This changed since you opened it.",
                "stored": exc.stored,
            },
        ) from exc
    except TodayError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


User = Annotated[UserModel, Depends(get_user)]


# --- Today (screen 07) -----------------------------------------------------------


@router.get("", dependencies=_list_on)
async def get_today(user: User) -> dict[str, Any]:
    """The ordered list: approvals, due, brief, upcoming, suggestions."""
    return await _run(listing.today(await _viewer(user)))


class DismissRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    later_minutes: int | None = Field(default=None, ge=15, le=7 * 24 * 60)


@router.post("/suggestions/{key}/dismiss", dependencies=_list_on)
async def dismiss_suggestion(
    key: str, body: DismissRequest, user: User
) -> dict[str, Any]:
    return await _run(
        listing.dismiss(await _viewer(user), key, later_minutes=body.later_minutes)
    )


@router.post("/missed-calls/{missed_call_id}/call-back", dependencies=_list_on)
async def propose_call_back(missed_call_id: int, user: User) -> dict[str, Any]:
    """Proposes the callback as a card; nothing is dialled until approved."""
    return await _run(listing.propose_callback(await _viewer(user), missed_call_id))


# --- Approvals (screen 08 and the dock) ---------------------------------------------


@router.get("/approvals", dependencies=_approvals_on)
async def pending_approvals(user: User) -> dict[str, Any]:
    """Only genuinely pending cards this person may see; ``count`` is the
    badge."""
    return await _run(approvals.pending(await _viewer(user)))


@router.get("/approvals/{event_id}", dependencies=_approvals_on)
async def approval_preview(event_id: int, user: User) -> dict[str, Any]:
    return await _run(approvals.preview(await _viewer(user), event_id))


# --- Activity (screen 09) --------------------------------------------------------------


@router.get("/activity", dependencies=_list_on)
async def list_activity(
    user: User,
    state: Annotated[str | None, Query(max_length=24)] = None,
    helper: Annotated[str | None, Query(max_length=120)] = None,
    days: Annotated[int, Query(ge=1, le=90)] = activity.DEFAULT_DAYS,
) -> dict[str, Any]:
    return await _run(
        activity.list_for(await _viewer(user), state=state, helper=helper, days=days)
    )


@router.get("/activity/{kind}/{item_id}", dependencies=_list_on)
async def activity_detail(kind: str, item_id: int, user: User) -> dict[str, Any]:
    return await _run(activity.detail(await _viewer(user), kind, item_id))


class RoutinePreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    cadence: str = Field(max_length=16)
    anchor: str = Field(max_length=16)
    at_minute: int = Field(default=0, ge=0, le=1439)
    offset_minutes: int = Field(default=0, ge=-1440, le=1440)
    weekday: int = Field(default=0, ge=0, le=6)


@router.post("/routines/preview", dependencies=_list_on)
async def routine_preview(body: RoutinePreviewRequest, user: User) -> dict[str, Any]:
    """The live next-run sentence for the routine editor, in the
    workspace's zone (a routine runs on the workspace's clock)."""
    from datetime import UTC, datetime

    from api.services.compliance import dnd
    from api.services.organization_preferences import get_organization_preferences
    from api.services.today.scope import full_local
    from api.services.workflow import routines

    try:
        spec = routines.RoutineSpec(
            cadence=routines.Cadence(body.cadence),
            anchor=routines.Anchor(body.anchor),
            at_minute=body.at_minute,
            offset_minutes=body.offset_minutes,
            weekday=body.weekday,
            is_active=True,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=422, detail="Not a schedule we can run."
        ) from exc
    prefs = await get_organization_preferences(user.selected_organization_id)
    zone = dnd.resolve_zone(getattr(prefs, "timezone", None))
    nxt = routines.next_slot(
        spec,
        now=datetime.now(UTC),
        zone=zone,
        business_hours=getattr(prefs, "business_hours", None),
    )
    return {
        "schedule": routines.describe(spec),
        "next_at": nxt.isoformat() if nxt else None,
        "sentence": (
            f"{routines.describe(spec)}. Next run: {full_local(nxt, zone.key)}."
            if nxt
            else f"{routines.describe(spec)}. It does not come round in the next two weeks."
        ),
        "timezone": zone.key,
    }


# --- Reminders and events (screen 10) ----------------------------------------------


class ReminderDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(max_length=200)
    note: str = Field(default="", max_length=2000)
    event_id: int | None = None
    offset_minutes: int | None = None
    recurrence: str = Field(default="once", max_length=16)
    date: str | None = Field(default=None, max_length=16)
    local_time: str | None = Field(default=None, max_length=5)
    weekday: int | None = Field(default=None, ge=0, le=6)
    timezone: str | None = Field(default=None, max_length=64)
    #: None is "the person's usual": their kept reminder channel while
    #: ``evolve_personal`` is on (services/personal), else in the app.
    channel: str | None = Field(default=None, max_length=16)


class ReminderSave(ReminderDraft):
    #: The ``schedule_key`` from the preview the person confirmed.
    schedule_key: str = Field(max_length=32)
    revision: int | None = None


def _draft(body: ReminderDraft) -> dict[str, Any]:
    return body.model_dump(exclude={"schedule_key", "revision"})


@router.get("/reminders", dependencies=_reminders_on)
async def list_reminders(user: User, include_finished: bool = False) -> dict[str, Any]:
    viewer = await _viewer(user)
    view = await _run(reminders.list_for(viewer, include_finished=include_finished))
    view["channel_states"] = [
        await delivery.channel_state(viewer.organization_id, viewer.user_id, channel)
        for channel in reminders.CHANNELS
    ]
    return view


@router.post("/reminders/preview", dependencies=_reminders_on)
async def preview_reminder(body: ReminderDraft, user: User) -> dict[str, Any]:
    return await _run(reminders.preview(await _viewer(user), _draft(body)))


@router.post("/reminders", dependencies=_reminders_on)
async def create_reminder(body: ReminderSave, user: User) -> dict[str, Any]:
    return await _run(
        reminders.create(
            await _viewer(user), _draft(body), schedule_key=body.schedule_key
        )
    )


@router.get("/reminders/{reminder_id}", dependencies=_reminders_on)
async def get_reminder(reminder_id: int, user: User) -> dict[str, Any]:
    viewer = await _viewer(user)
    view = await _run(reminders.get(viewer, reminder_id))
    view["deliveries"] = await delivery.for_subject(
        viewer.organization_id, viewer.user_id, "reminder", reminder_id
    )
    return view


@router.put("/reminders/{reminder_id}", dependencies=_reminders_on)
async def update_reminder(
    reminder_id: int, body: ReminderSave, user: User
) -> dict[str, Any]:
    if body.revision is None:
        raise HTTPException(status_code=422, detail="Say which revision you edited.")
    return await _run(
        reminders.update(
            await _viewer(user),
            reminder_id,
            _draft(body),
            revision=body.revision,
            schedule_key=body.schedule_key,
        )
    )


class ReminderStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")
    verb: str = Field(max_length=16)


@router.post("/reminders/{reminder_id}/status", dependencies=_reminders_on)
async def set_reminder_status(
    reminder_id: int, body: ReminderStatus, user: User
) -> dict[str, Any]:
    return await _run(reminders.set_status(await _viewer(user), reminder_id, body.verb))


class Snooze(BaseModel):
    model_config = ConfigDict(extra="forbid")
    minutes: int = Field(ge=5, le=24 * 60)


@router.post("/reminders/{reminder_id}/snooze", dependencies=_reminders_on)
async def snooze_reminder(reminder_id: int, body: Snooze, user: User) -> dict[str, Any]:
    return await _run(reminders.snooze(await _viewer(user), reminder_id, body.minutes))


@router.post("/reminders/{reminder_id}/test", dependencies=_reminders_on)
async def test_reminder(reminder_id: int, user: User) -> dict[str, Any]:
    """One labelled test delivery on the reminder's channel. It does not
    touch the schedule or create another one."""
    from datetime import UTC, datetime

    viewer = await _viewer(user)
    view = await _run(reminders.get(viewer, reminder_id))
    now = datetime.now(UTC)
    return await delivery.deliver(
        organization_id=viewer.organization_id,
        user_id=viewer.user_id,
        subject_kind="reminder",
        subject_id=reminder_id,
        occurrence_key=f"test:{now.strftime('%Y-%m-%dT%H:%M')}",
        channel=view["channel"],
        text=f"Test delivery. Reminder: {view['title']}",
        is_test=True,
    )


class ResolveDate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    words: str = Field(max_length=32)
    local_time: str = Field(max_length=5)
    timezone: str | None = Field(default=None, max_length=64)


@router.post("/resolve-date", dependencies=_reminders_on)
async def resolve_date(body: ResolveDate, user: User) -> dict[str, Any]:
    """ "Tomorrow" in the person's timezone, as the full date to confirm."""
    from zoneinfo import ZoneInfo

    from api.services.today.scope import at_local, full_local, parse_hhmm, valid_zone

    viewer = await _viewer(user)
    zone_name = valid_zone(body.timezone) or viewer.zone_name
    hhmm = parse_hhmm(body.local_time)
    if hhmm is None:
        raise HTTPException(status_code=422, detail="Say the time as HH:MM.")
    try:
        day = reminders.resolve_date(body.words, zone_name)
    except TodayError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    moment = at_local(day, hhmm, ZoneInfo(zone_name))
    return {
        "date": day.isoformat(),
        "at": moment.isoformat(),
        "full": full_local(moment, zone_name),
    }


class EventCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(max_length=200)
    date: str = Field(max_length=16)
    local_time: str = Field(max_length=5)
    timezone: str | None = Field(default=None, max_length=64)
    #: Offsets in minutes: 0 at event time, -1440 one day before.
    reminders: list[int] = Field(default_factory=list, max_length=4)
    channel: str = Field(default="in_app", max_length=16)


@router.post("/events", dependencies=_reminders_on)
async def create_event(body: EventCreate, user: User) -> dict[str, Any]:
    return await _run(
        reminders.create_event(
            await _viewer(user),
            title=body.title,
            day=body.date,
            local_time=body.local_time,
            timezone=body.timezone,
            reminders=body.reminders,
            channel=body.channel,
        )
    )


class EventMove(BaseModel):
    model_config = ConfigDict(extra="forbid")
    date: str = Field(max_length=16)
    local_time: str = Field(max_length=5)
    revision: int


@router.post("/events/{event_id}/move", dependencies=_reminders_on)
async def move_event(event_id: int, body: EventMove, user: User) -> dict[str, Any]:
    return await _run(
        reminders.move_event(
            await _viewer(user),
            event_id,
            day=body.date,
            local_time=body.local_time,
            revision=body.revision,
        )
    )


@router.post("/events/{event_id}/cancel", dependencies=_reminders_on)
async def cancel_event(event_id: int, user: User) -> dict[str, Any]:
    return await _run(reminders.cancel_event(await _viewer(user), event_id))


# --- The daily brief (screens 07 and 20) --------------------------------------------


@router.get("/brief", dependencies=_brief_on)
async def get_brief(user: User) -> dict[str, Any]:
    viewer = await _viewer(user)
    return {
        "brief": await brief.latest(viewer),
        "settings": await brief.get_settings(viewer),
    }


@router.post("/brief/refresh", dependencies=_brief_on)
async def refresh_brief(user: User) -> dict[str, Any]:
    """Rebuild today's brief in place (the same brief, a new refresh time).
    Delivers nothing."""
    return await _run(brief.build(await _viewer(user)))


@router.get("/brief/settings", dependencies=_brief_on)
async def get_brief_settings(user: User) -> dict[str, Any]:
    return await brief.get_settings(await _viewer(user))


class BriefSettingsWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: int = Field(ge=0)
    enabled: bool | None = None
    paused: bool | None = None
    local_time: str | None = Field(default=None, max_length=5)
    timezone: str | None = Field(default=None, max_length=64)
    days: list[int] | None = Field(default=None, max_length=7)
    channels: list[str] | None = Field(default=None, max_length=3)
    quiet_start: str | None = Field(default=None, max_length=5)
    quiet_end: str | None = Field(default=None, max_length=5)
    end_of_day_enabled: bool | None = None
    end_of_day_time: str | None = Field(default=None, max_length=5)


@router.put("/brief/settings", dependencies=_brief_on)
async def save_brief_settings(body: BriefSettingsWrite, user: User) -> dict[str, Any]:
    changes = body.model_dump(exclude_unset=True, exclude={"revision"})
    if changes.get("end_of_day_enabled"):
        if not features.is_on(END_OF_DAY, user.selected_organization_id):
            raise HTTPException(
                status_code=422, detail="The end-of-day note is not available here yet."
            )
    return await _run(
        brief.save_settings(await _viewer(user), changes, revision=body.revision)
    )


@router.post("/brief/test", dependencies=_brief_on)
async def test_brief(user: User) -> dict[str, Any]:
    """One labelled test delivery of today's brief on each chosen channel.
    It does not create a schedule or mark the day's brief delivered."""
    viewer = await _viewer(user)
    view = await _run(brief.build(viewer))
    settings = await brief.get_settings(viewer)
    results = await ticks.deliver_brief(
        viewer, view, settings["channels"], is_test=True
    )
    return {"brief": view, "deliveries": results}


# --- The end-of-day note -----------------------------------------------------------


@router.get("/end-of-day", dependencies=_eod_on)
async def get_end_of_day(user: User) -> dict[str, Any]:
    return {"note": await brief.latest(await _viewer(user), kind=brief.END_OF_DAY)}


@router.post("/end-of-day/refresh", dependencies=_eod_on)
async def refresh_end_of_day(user: User) -> dict[str, Any]:
    return await _run(brief.build(await _viewer(user), kind=brief.END_OF_DAY))
