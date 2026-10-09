"""Today reminders: delivery and the task kept apart, and cancel re-checked.

Stage 1 of the reminder-call contract (docs/plans/reminder-calls.md). A
general reminder call will reuse this schedule (preview, schedule key, the
occurrence-keyed delivery row), so these pin the two places it once
disagreed with the contract's separate delivery and task states:

* a one-off reminder whose only delivery could not be made (push not set
  up) reads "missed", never "done"; the delivery row says why (fixed);
* a reminder cancelled after the tick read it is not sent: the claim
  re-checks it under a row lock, so a cancel at the same moment is ordered
  against the claim (fixed; real concurrent transactions below).
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest
from sqlalchemy import select, text, update

from api.db import db_client
from api.db.today_models import TodayDeliveryModel, TodayReminderModel
from api.services.today import reminders, ticks
from api.tests.today_helpers import clean, make_people, switch_on

NOW = datetime(2026, 10, 8, 4, 30, tzinfo=UTC)
DUE = datetime(2026, 10, 9, 3, 31, tzinfo=UTC)

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def people(test_engine):
    p = await make_people()
    yield p
    await clean(p)


@pytest.fixture
def on(monkeypatch):
    switch_on(monkeypatch, "today_reminders")


async def _save(viewer, draft):
    shown = await reminders.preview(viewer, draft, now=NOW)
    return await reminders.create(
        viewer, draft, schedule_key=shown["schedule_key"], now=NOW
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


async def test_a_delivered_one_off_reads_done(people, on):
    """Pinned: the happy path the contract keeps."""
    saved = await _save(
        people.me,
        {"title": "Send the proposal", "date": "tomorrow", "local_time": "09:00"},
    )
    await ticks.deliver_due_reminders(now=DUE)
    assert (await _deliveries(saved["id"]))[0].status == "sent"
    assert (await reminders.get(people.me, saved["id"]))["status"] == "done"


async def test_an_undelivered_one_off_does_not_read_done(people, on):
    saved = await _save(
        people.me,
        {
            "title": "Send the proposal",
            "date": "tomorrow",
            "local_time": "09:00",
            "channel": "push",
        },
    )
    await ticks.deliver_due_reminders(now=DUE)
    assert (await _deliveries(saved["id"]))[0].status == "needs_setup"
    assert (await reminders.get(people.me, saved["id"]))["status"] != "done"


async def test_a_cancel_after_the_tick_read_it_is_not_sent(people, on, monkeypatch):
    saved = await _save(
        people.me,
        {"title": "Send the proposal", "date": "tomorrow", "local_time": "09:00"},
    )
    real_text = ticks._reminder_text

    async def cancel_then_text(row):
        # The person cancels while the tick is between reading and sending.
        await reminders.set_status(people.me, saved["id"], "cancel", now=DUE)
        return await real_text(row)

    monkeypatch.setattr(ticks, "_reminder_text", cancel_then_text)
    await ticks.deliver_due_reminders(now=DUE)
    assert await _deliveries(saved["id"]) == []
    assert (await reminders.get(people.me, saved["id"]))["status"] == "cancelled"


async def test_the_undelivered_one_off_stays_on_the_list_with_why(people, on):
    """Delivery and task are both visible: the reminder is still waiting
    (``missed``, on the list), and its delivery row says why it did not go."""
    saved = await _save(
        people.me,
        {
            "title": "Send the proposal",
            "date": "tomorrow",
            "local_time": "09:00",
            "channel": "push",
        },
    )
    await ticks.deliver_due_reminders(now=DUE)
    listed = await reminders.list_for(people.me)
    assert [r["id"] for r in listed["reminders"]] == [saved["id"]]
    assert listed["reminders"][0]["status"] == "missed"
    assert listed["reminders"][0]["last_delivered_at"] is None
    delivery = (await _deliveries(saved["id"]))[0]
    assert delivery.reason_code == "push_needs_setup"
    assert delivery.detail


async def test_an_undelivered_recurring_reminder_moves_to_its_next_time(people, on):
    """Pinned: a daily reminder that could not be delivered stays active and
    comes round again; only one-offs settle."""
    saved = await _save(
        people.me,
        {
            "title": "Stand-up notes",
            "recurrence": "daily",
            "local_time": "09:00",
            "channel": "push",
        },
    )
    first = saved["remind_at"]
    await ticks.deliver_due_reminders(now=datetime.fromisoformat(first))
    after = await reminders.get(people.me, saved["id"])
    assert after["status"] == "active"
    assert after["remind_at"] > first
    assert after["last_delivered_at"] is None


async def test_a_pause_after_the_tick_read_it_is_not_sent(people, on, monkeypatch):
    saved = await _save(
        people.me,
        {"title": "Send the proposal", "date": "tomorrow", "local_time": "09:00"},
    )
    real_text = ticks._reminder_text

    async def pause_then_text(row):
        await reminders.set_status(people.me, saved["id"], "pause", now=DUE)
        return await real_text(row)

    monkeypatch.setattr(ticks, "_reminder_text", pause_then_text)
    await ticks.deliver_due_reminders(now=DUE)
    assert await _deliveries(saved["id"]) == []
    assert (await reminders.get(people.me, saved["id"]))["status"] == "paused"


async def _wait_for_a_lock_wait(timeout: float = 10.0) -> None:
    """Until some session in this database is waiting on a row lock."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        async with db_client.async_session() as session:
            waiting = await session.scalar(
                text(
                    "SELECT count(*) FROM pg_stat_activity WHERE datname = "
                    "current_database() AND wait_event_type = 'Lock'"
                )
            )
        if waiting:
            return
        await asyncio.sleep(0.02)
    raise AssertionError("the tick never waited on the reminder's row")


async def test_a_cancel_in_flight_when_the_tick_claims_wins(people, on):
    """Real concurrency: the cancel's transaction holds the row (uncommitted)
    while the tick tries to claim. The claim waits for it, re-reads, finds
    the reminder cancelled, and sends nothing."""
    saved = await _save(
        people.me,
        {"title": "Send the proposal", "date": "tomorrow", "local_time": "09:00"},
    )
    async with db_client.async_session() as cancelling:
        await cancelling.execute(
            update(TodayReminderModel)
            .where(TodayReminderModel.id == saved["id"])
            .values(status="cancelled")
        )
        tick = asyncio.create_task(ticks.deliver_due_reminders(now=DUE))
        await _wait_for_a_lock_wait()
        assert not tick.done()
        await cancelling.commit()
    await tick
    assert await _deliveries(saved["id"]) == []
    assert (await reminders.get(people.me, saved["id"]))["status"] == "cancelled"


async def test_cancels_racing_ticks_never_send_after_and_never_read_done(people, on):
    """Many reminders, each cancelled at the same moment as two ticks run.
    Whatever the interleaving: at most one delivery per reminder, and every
    reminder ends cancelled (a claim before the cancel may have sent it; the
    tick never writes "done" over the cancel)."""
    saved = [
        await _save(
            people.me,
            {"title": f"Thing {i}", "date": "tomorrow", "local_time": "09:00"},
        )
        for i in range(8)
    ]
    await asyncio.gather(
        ticks.deliver_due_reminders(now=DUE),
        ticks.deliver_due_reminders(now=DUE),
        *(reminders.set_status(people.me, s["id"], "cancel", now=DUE) for s in saved),
    )
    for s in saved:
        assert len(await _deliveries(s["id"])) <= 1
        assert (await reminders.get(people.me, s["id"]))["status"] == "cancelled"
