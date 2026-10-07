"""Reminders and event-linked reminders (handoff 22, 31.4; screen 10).

Done when: "tomorrow" is the person's tomorrow, shown as a full date; a
save stores only the schedule the person saw; moving an event recalculates
its linked reminders once and keeps the old and new times; cancelling the
event cancels them; a reminder is delivered once however often the tick
runs or the worker restarts; a late one is "missed", never sent hours
later; and nobody else's reminders are ever reachable.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import select, text

from api.db import db_client
from api.db.today_models import TodayDeliveryModel, TodayReminderModel
from api.services.today import reminders, ticks
from api.services.today.scope import Conflict, NotFound, TodayError
from api.tests.today_helpers import clean, client_as, make_people, switch_on

# 2026-10-08 10:00 in Kolkata.
NOW = datetime(2026, 10, 8, 4, 30, tzinfo=UTC)


@pytest.fixture
async def people(test_engine):
    p = await make_people()
    yield p
    await clean(p)


@pytest.fixture
def on(monkeypatch):
    switch_on(monkeypatch, "today_reminders")


async def _save(viewer, draft, now=NOW):
    shown = await reminders.preview(viewer, draft, now=now)
    return await reminders.create(
        viewer, draft, schedule_key=shown["schedule_key"], now=now
    )


async def _deliveries(reminder_id):
    async with db_client.async_session() as session:
        return list(
            (
                await session.execute(
                    select(TodayDeliveryModel).where(
                        TodayDeliveryModel.subject_kind == "reminder",
                        TodayDeliveryModel.subject_id == reminder_id,
                    )
                )
            ).scalars()
        )


@pytest.mark.asyncio
class TestTomorrowIsTheirs:
    async def test_tomorrow_resolves_in_the_persons_zone(self):
        # 23:30 UTC on the 7th is already the 8th in Kolkata.
        late = datetime(2026, 10, 7, 23, 30, tzinfo=UTC)
        assert (
            reminders.resolve_date("tomorrow", "Asia/Kolkata", now=late).isoformat()
            == "2026-10-09"
        )
        assert (
            reminders.resolve_date("tomorrow", "America/New_York", now=late).isoformat()
            == "2026-10-08"
        )

    async def test_the_preview_says_the_full_date_and_zone(self, people):
        shown = await reminders.preview(
            people.me,
            {"title": "Pay rent", "date": "tomorrow", "local_time": "09:00"},
            now=NOW,
        )
        assert (
            shown["sentence"] == "Once. Next: Fri 9 Oct 2026, 09:00 IST (Asia/Kolkata)."
        )
        assert shown["next_at"] == "2026-10-09T03:30:00+00:00"
        assert shown["problems"] == []

    async def test_a_past_date_is_named(self, people):
        shown = await reminders.preview(
            people.me, {"title": "x", "date": "today", "local_time": "08:00"}, now=NOW
        )
        assert shown["problems"][0]["code"] == "invalid_past"
        with pytest.raises(TodayError):
            await reminders.create(
                people.me,
                {"title": "x", "date": "today", "local_time": "08:00"},
                schedule_key=shown["schedule_key"],
                now=NOW,
            )

    async def test_another_zone_is_flagged_as_a_conflict(self, people):
        shown = await reminders.preview(
            people.me,
            {
                "title": "Call",
                "date": "tomorrow",
                "local_time": "09:00",
                "timezone": "Europe/London",
            },
            now=NOW,
        )
        assert [p["code"] for p in shown["problems"]] == ["timezone_conflict"]
        assert "Europe/London" in shown["sentence"]

    async def test_dst_keeps_the_wall_time(self):
        # New York leaves daylight time on 1 November 2026.
        after = datetime(2026, 10, 31, 12, 0, tzinfo=UTC)
        first = reminders.next_recurring(
            recurrence="daily",
            local_time="09:00",
            weekday=None,
            zone_name="America/New_York",
            after=after,
        )
        second = reminders.next_recurring(
            recurrence="daily",
            local_time="09:00",
            weekday=None,
            zone_name="America/New_York",
            after=first,
        )
        assert first.astimezone(UTC).hour == 13 and second.astimezone(UTC).hour == 14


@pytest.mark.asyncio
class TestOnlyWhatTheySaw:
    async def test_a_save_without_the_shown_schedule_is_refused(self, people, on):
        draft = {"title": "Pay rent", "date": "tomorrow", "local_time": "09:00"}
        with pytest.raises(TodayError):
            await reminders.create(people.me, draft, schedule_key="nope", now=NOW)
        shown = await reminders.preview(people.me, draft, now=NOW)
        changed = {**draft, "local_time": "10:00"}
        with pytest.raises(TodayError):
            await reminders.create(
                people.me, changed, schedule_key=shown["schedule_key"], now=NOW
            )

    async def test_a_saved_reminder_is_listed(self, people, on):
        saved = await _save(
            people.me, {"title": "Pay rent", "date": "tomorrow", "local_time": "09:00"}
        )
        listed = await reminders.list_for(people.me)
        assert [r["id"] for r in listed["reminders"]] == [saved["id"]]
        assert listed["reminders"][0]["when"].startswith("Fri 9 Oct 2026, 09:00")

    async def test_a_stale_edit_is_a_conflict(self, people, on):
        draft = {"title": "Pay rent", "date": "tomorrow", "local_time": "09:00"}
        saved = await _save(people.me, draft)
        moved = {**draft, "local_time": "11:00"}
        key = (await reminders.preview(people.me, moved, now=NOW))["schedule_key"]
        await reminders.update(
            people.me, saved["id"], moved, revision=1, schedule_key=key, now=NOW
        )
        with pytest.raises(Conflict) as caught:
            await reminders.update(
                people.me, saved["id"], moved, revision=1, schedule_key=key, now=NOW
            )
        assert caught.value.stored["local_time"] == "11:00"


@pytest.mark.asyncio
class TestEventsMoveTheirReminders:
    async def _event(self, viewer):
        return await reminders.create_event(
            viewer,
            title="Dentist",
            day="2026-10-12",
            local_time="15:00",
            reminders=[reminders.AT_EVENT, reminders.DAY_BEFORE],
            now=NOW,
        )

    async def test_an_event_has_its_offsets_as_reminders(self, people, on):
        event = await self._event(people.me)
        whens = sorted(r["when"] for r in event["reminders"])
        assert whens == [
            "Mon 12 Oct 2026, 15:00 IST (Asia/Kolkata)",
            "Sun 11 Oct 2026, 15:00 IST (Asia/Kolkata)",
        ]

    async def test_moving_the_event_recalculates_each_reminder_once(self, people, on):
        event = await self._event(people.me)
        moved = await reminders.move_event(
            people.me,
            event["id"],
            day="2026-10-13",
            local_time="11:30",
            revision=1,
            now=NOW,
        )
        assert moved["was"].startswith("Mon 12 Oct 2026, 15:00")
        news = sorted(c["new"] for c in moved["changes"])
        assert news == [
            "Mon 12 Oct 2026, 11:30 IST (Asia/Kolkata)",
            "Tue 13 Oct 2026, 11:30 IST (Asia/Kolkata)",
        ]
        listed = await reminders.list_for(people.me)
        assert len(listed["reminders"]) == 2
        for r in listed["reminders"]:
            assert [h["why"] for h in r["history"]] == ["event_moved"]
        # The same move from a second tab holds the old revision: refused.
        with pytest.raises(Conflict):
            await reminders.move_event(
                people.me,
                event["id"],
                day="2026-10-14",
                local_time="11:30",
                revision=1,
                now=NOW,
            )

    async def test_a_reminder_moved_into_the_past_is_named_not_chosen(self, people, on):
        event = await self._event(people.me)
        moved = await reminders.move_event(
            people.me,
            event["id"],
            day="2026-10-08",
            local_time="20:00",
            revision=1,
            now=NOW,
        )
        conflicts = [c for c in moved["changes"] if c["conflict"]]
        assert len(conflicts) == 1 and "already passed" in conflicts[0]["conflict"]

    async def test_cancelling_the_event_cancels_its_reminders_once(self, people, on):
        event = await self._event(people.me)
        first = await reminders.cancel_event(people.me, event["id"], now=NOW)
        assert len(first["cancelled_reminders"]) == 2
        second = await reminders.cancel_event(people.me, event["id"], now=NOW)
        assert second["cancelled_reminders"] == []
        assert (await reminders.list_for(people.me))["reminders"] == []

    async def test_no_event_time_is_invented(self, people, on):
        with pytest.raises(TodayError):
            await reminders.create_event(
                people.me, title="Call Ravi", day="tomorrow", local_time="", now=NOW
            )


@pytest.mark.asyncio
class TestDeliveredOnce:
    async def test_the_tick_delivers_once_however_often_it_runs(self, people, on):
        saved = await _save(
            people.me, {"title": "Pay rent", "date": "tomorrow", "local_time": "09:00"}
        )
        due = datetime(2026, 10, 9, 3, 31, tzinfo=UTC)
        first = await ticks.deliver_due_reminders(now=due)
        second = await ticks.deliver_due_reminders(now=due)
        assert first["delivered"] == 1 and second["delivered"] == 0
        rows = await _deliveries(saved["id"])
        assert len(rows) == 1 and rows[0].status == "sent" and rows[0].evidence
        assert (await reminders.get(people.me, saved["id"]))["status"] == "done"

    async def test_a_worker_restart_between_send_and_advance_does_not_resend(
        self, people, on
    ):
        saved = await _save(
            people.me, {"title": "Pay rent", "date": "tomorrow", "local_time": "09:00"}
        )
        due = datetime(2026, 10, 9, 3, 31, tzinfo=UTC)
        async with db_client.async_session() as session:
            row = await session.get(TodayReminderModel, saved["id"])
        from api.services.today import delivery

        # The worker sent and died before advancing the reminder.
        await delivery.deliver(
            organization_id=row.organization_id,
            user_id=row.user_id,
            subject_kind="reminder",
            subject_id=row.id,
            occurrence_key=row.remind_at.isoformat(),
            channel="in_app",
            text="x",
        )
        await ticks.deliver_due_reminders(now=due)
        assert len(await _deliveries(saved["id"])) == 1
        assert (await reminders.get(people.me, saved["id"]))["status"] == "done"

    async def test_a_daily_reminder_comes_round_again(self, people, on):
        saved = await _save(
            people.me,
            {"title": "Stand-up", "recurrence": "daily", "local_time": "09:00"},
        )
        await ticks.deliver_due_reminders(now=datetime(2026, 10, 9, 3, 31, tzinfo=UTC))
        after = await reminders.get(people.me, saved["id"])
        assert (
            after["status"] == "active"
            and after["remind_at"] == "2026-10-10T03:30:00+00:00"
        )

    async def test_too_late_is_missed_not_sent(self, people, on):
        saved = await _save(
            people.me, {"title": "Pay rent", "date": "tomorrow", "local_time": "09:00"}
        )
        counts = await ticks.deliver_due_reminders(
            now=datetime(2026, 10, 9, 8, 0, tzinfo=UTC)
        )
        assert counts["missed"] == 1
        assert await _deliveries(saved["id"]) == []
        assert (await reminders.get(people.me, saved["id"]))["status"] == "missed"

    async def test_a_paused_reminder_is_not_delivered(self, people, on):
        saved = await _save(
            people.me, {"title": "Pay rent", "date": "tomorrow", "local_time": "09:00"}
        )
        await reminders.set_status(people.me, saved["id"], "pause", now=NOW)
        await ticks.deliver_due_reminders(now=datetime(2026, 10, 9, 3, 31, tzinfo=UTC))
        assert await _deliveries(saved["id"]) == []

    async def test_nothing_runs_while_the_switch_is_off(self, people, on, monkeypatch):
        saved = await _save(
            people.me, {"title": "Pay rent", "date": "tomorrow", "local_time": "09:00"}
        )
        from api import constants

        monkeypatch.setattr(constants, "TODAY_REMINDERS_ENABLED", False)
        counts = await ticks.deliver_due_reminders(
            now=datetime(2026, 10, 9, 3, 31, tzinfo=UTC)
        )
        assert counts == {"delivered": 0, "missed": 0}
        assert await _deliveries(saved["id"]) == []

    async def test_push_is_needs_setup_never_a_fake_success(self, people, on):
        saved = await _save(
            people.me,
            {
                "title": "Pay rent",
                "date": "tomorrow",
                "local_time": "09:00",
                "channel": "push",
            },
        )
        await ticks.deliver_due_reminders(now=datetime(2026, 10, 9, 3, 31, tzinfo=UTC))
        rows = await _deliveries(saved["id"])
        assert rows[0].status == "needs_setup" and "not set up" in rows[0].detail


@pytest.mark.asyncio
class TestTheirsAlone:
    async def test_a_colleague_cannot_read_or_change_it(self, people, on):
        saved = await _save(
            people.me, {"title": "Pay rent", "date": "tomorrow", "local_time": "09:00"}
        )
        assert (await reminders.list_for(people.colleague))["reminders"] == []
        with pytest.raises(NotFound):
            await reminders.get(people.colleague, saved["id"])
        with pytest.raises(NotFound):
            await reminders.set_status(people.colleague, saved["id"], "cancel")
        with pytest.raises(NotFound):
            await reminders.get(people.stranger, saved["id"])

    async def test_a_colleagues_event_cannot_be_linked(self, people, on):
        event = await reminders.create_event(
            people.colleague,
            title="Board",
            day="2026-10-12",
            local_time="10:00",
            now=NOW,
        )
        with pytest.raises(NotFound):
            await reminders.preview(
                people.me,
                {"title": "x", "event_id": event["id"], "offset_minutes": 0},
                now=NOW,
            )


@pytest.mark.asyncio
class TestArrival:
    async def test_off_the_routes_are_not_there(self, people):
        async with client_as(people.me_user) as client:
            assert (await client.get("/api/v1/today/reminders")).status_code == 404

    async def test_on_a_person_can_preview_save_and_test(self, people, on):
        draft = {"title": "Pay rent", "date": "tomorrow", "local_time": "09:00"}
        async with client_as(people.me_user) as client:
            shown = (
                await client.post("/api/v1/today/reminders/preview", json=draft)
            ).json()
            assert shown["sentence"].startswith("Once. Next:")
            saved = await client.post(
                "/api/v1/today/reminders",
                json={**draft, "schedule_key": shown["schedule_key"]},
            )
            assert saved.status_code == 200, saved.text
            rid = saved.json()["id"]
            tested = (await client.post(f"/api/v1/today/reminders/{rid}/test")).json()
            again = (await client.post(f"/api/v1/today/reminders/{rid}/test")).json()
            assert tested["is_test"] and tested["status"] == "sent"
            # One labelled test occurrence per minute, not a second schedule.
            assert again["duplicate"] is True
            listed = (await client.get("/api/v1/today/reminders")).json()
            assert len(listed["reminders"]) == 1
            states = {c["channel"]: c["state"] for c in listed["channel_states"]}
            assert states == {
                "in_app": "available",
                "whatsapp": "needs_setup",
                "push": "needs_setup",
            }
            detail = (await client.get(f"/api/v1/today/reminders/{rid}")).json()
            assert detail["deliveries"][0]["is_test"] is True
        async with client_as(people.colleague_user) as client:
            assert (
                await client.get(f"/api/v1/today/reminders/{rid}")
            ).status_code == 404

    async def test_resolve_date_route(self, people, on):
        async with client_as(people.me_user) as client:
            body = (
                await client.post(
                    "/api/v1/today/resolve-date",
                    json={"words": "tomorrow", "local_time": "09:00"},
                )
            ).json()
        assert body["full"].endswith("09:00 IST (Asia/Kolkata)")

    async def test_counted_in_one_row_per_save(self, people, on):
        draft = {"title": "Pay rent", "date": "tomorrow", "local_time": "09:00"}
        await _save(people.me, draft)
        async with db_client.async_session() as session:
            count = await session.scalar(
                text("SELECT count(*) FROM today_reminders WHERE user_id = :u"),
                {"u": people.me.user_id},
            )
        assert count == 1
