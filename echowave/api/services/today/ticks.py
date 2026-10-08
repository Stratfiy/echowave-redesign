"""The minute jobs: due reminders and due briefs, delivered once.

Survives a worker restart without a duplicate (handoff 31.4): the delivery
row for an occurrence is claimed before anything is sent and is unique per
(what, occurrence, channel); a reminder's next time is advanced only from
the time it was read (compare-and-swap); a brief's ``delivered_at`` is set
after its channels are tried, and trying them again finds every row
already claimed. A tick that dies anywhere in between leaves the next tick
to finish the job, never to repeat it.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from loguru import logger
from sqlalchemy import and_, select, update

from api.db import db_client
from api.db.today_models import DailyBriefModel, TodayEventModel, TodayReminderModel
from api.services import features
from api.services.today import DAILY_BRIEF, END_OF_DAY, REMINDERS, brief, delivery
from api.services.today.reminders import next_recurring
from api.services.today.scope import Viewer, full_local, is_admin

#: How late a reminder may still go out; later than this it is "missed",
#: recorded, and never sent at a time it no longer means anything.
CATCH_UP_MINUTES = 20
BATCH = 500


async def _reminder_text(row: TodayReminderModel) -> str:
    line = f"Reminder: {row.title}"
    if row.event_id is not None:
        async with db_client.async_session() as session:
            event = await session.get(TodayEventModel, row.event_id)
        if event is not None and event.status == "active":
            line += f" ({event.title}, {full_local(event.starts_at, event.timezone)})"
    if row.note:
        line += f"\n{row.note}"
    return line


async def _advance(
    row: TodayReminderModel, *, delivered: bool, missed: bool, now: datetime
) -> None:
    """Move a reminder past the occurrence it was read at -- only if nobody
    else has (the ``remind_at`` it was read with is the guard)."""
    values: dict[str, Any] = {"updated_at": now}
    if row.recurrence == "once":
        values["status"] = "missed" if missed else "done"
    else:
        values["remind_at"] = next_recurring(
            recurrence=row.recurrence,
            local_time=row.local_time,
            weekday=row.weekday,
            zone_name=row.timezone,
            after=max(row.remind_at, now - timedelta(minutes=CATCH_UP_MINUTES)),
        )
        if values["remind_at"] is None:
            values["status"] = "done"
    if delivered:
        values["last_delivered_at"] = now
    async with db_client.async_session() as session:
        await session.execute(
            update(TodayReminderModel)
            .where(
                and_(
                    TodayReminderModel.id == row.id,
                    TodayReminderModel.remind_at == row.remind_at,
                    TodayReminderModel.status == "active",
                )
            )
            .values(**values)
        )
        await session.commit()


async def deliver_due_reminders(now: datetime | None = None) -> dict[str, int]:
    if not features.on_anywhere(REMINDERS):
        return {"delivered": 0, "missed": 0}
    now = now or datetime.now(UTC)
    async with db_client.async_session() as session:
        rows = list(
            (
                await session.execute(
                    select(TodayReminderModel)
                    .where(
                        and_(
                            TodayReminderModel.status == "active",
                            TodayReminderModel.remind_at <= now,
                        )
                    )
                    .order_by(TodayReminderModel.remind_at)
                    .limit(BATCH)
                )
            ).scalars()
        )
    counts = {"delivered": 0, "missed": 0}
    for row in rows:
        try:
            if not features.is_on(REMINDERS, row.organization_id):
                continue
            late = (now - row.remind_at).total_seconds() / 60
            if late > CATCH_UP_MINUTES:
                await _advance(row, delivered=False, missed=True, now=now)
                from api.services import events

                await events.emit(
                    "reminder_failed",
                    user_id=row.user_id,
                    organization_id=row.organization_id,
                    task_id=f"reminder:{row.id}",
                    properties={
                        "channel": row.channel,
                        "status": "missed",
                        "reason_code": "missed",
                    },
                )
                counts["missed"] += 1
                continue
            result = await delivery.deliver(
                organization_id=row.organization_id,
                user_id=row.user_id,
                subject_kind="reminder",
                subject_id=row.id,
                occurrence_key=row.remind_at.isoformat(),
                channel=row.channel,
                text=await _reminder_text(row),
            )
            await _advance(
                row,
                delivered=result.get("status") in ("sent", "accepted"),
                missed=False,
                now=now,
            )
            counts["delivered"] += 1
        except Exception as exc:  # noqa: BLE001 - one reminder must not end the tick
            logger.exception("Reminder {} could not be delivered: {}", row.id, exc)
    return counts


async def _brief_viewer(row: Any) -> Viewer:
    return Viewer(
        user_id=row.user_id,
        organization_id=row.organization_id,
        zone_name=row.timezone,
        is_admin=await is_admin(row.user_id, row.organization_id),
    )


async def deliver_brief(
    viewer: Viewer,
    view: dict[str, Any],
    channels: list[str],
    *,
    is_test: bool = False,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Put one brief on each chosen channel. Idempotent per occurrence."""
    now = now or datetime.now(UTC)
    key = view["date"] if not is_test else f"test:{now.strftime('%Y-%m-%dT%H:%M')}"
    text = brief.text_of(view)
    if is_test:
        text = "Test delivery. " + text
    results = []
    for channel in channels:
        results.append(
            await delivery.deliver(
                organization_id=viewer.organization_id,
                user_id=viewer.user_id,
                subject_kind=view["kind"],
                subject_id=int(view["id"]),
                occurrence_key=key,
                channel=channel,
                text=text,
                is_test=is_test,
            )
        )
    return results


async def deliver_due_briefs(now: datetime | None = None) -> int:
    if not (features.on_anywhere(DAILY_BRIEF) or features.on_anywhere(END_OF_DAY)):
        return 0
    now = now or datetime.now(UTC)
    delivered = 0
    for settings, kind, day in await brief.due_now(now):
        try:
            flag = DAILY_BRIEF if kind == brief.BRIEF else END_OF_DAY
            if not features.is_on(flag, settings.organization_id):
                continue
            async with db_client.async_session() as session:
                existing = await session.scalar(
                    select(DailyBriefModel).where(
                        and_(
                            DailyBriefModel.organization_id == settings.organization_id,
                            DailyBriefModel.user_id == settings.user_id,
                            DailyBriefModel.kind == kind,
                            DailyBriefModel.occurrence_key == day.isoformat(),
                        )
                    )
                )
            if existing is not None and existing.delivered_at is not None:
                continue
            viewer = await _brief_viewer(settings)
            view = await brief.build(viewer, kind=kind, day=day, now=now)
            await deliver_brief(
                viewer, view, list(settings.channels or ["in_app"]), now=now
            )
            if await brief.claim_delivery(int(view["id"]), now):
                delivered += 1
        except Exception as exc:  # noqa: BLE001 - one person must not end the tick
            logger.exception(
                "Brief for user {} could not be delivered: {}", settings.user_id, exc
            )
    return delivered


__all__ = [
    "CATCH_UP_MINUTES",
    "deliver_brief",
    "deliver_due_briefs",
    "deliver_due_reminders",
]
