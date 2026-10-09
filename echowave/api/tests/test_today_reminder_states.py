"""Today reminders: where one ``status`` carries both delivery and the task.

Stage 1 of the reminder-call contract (docs/plans/reminder-calls.md). A
general reminder call will reuse this schedule (preview, schedule key, the
occurrence-keyed delivery row), so these pin what it does today and mark the
two places it disagrees with the contract's separate delivery and task
states:

* a one-off reminder whose only delivery could not be made (push not set
  up) still reads "done" (xfail);
* a reminder cancelled after the tick read it is still sent (xfail: the
  contract rechecks cancellation right before dispatch).
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from api.db import db_client
from api.db.today_models import TodayDeliveryModel
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


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="Gap: a one-off reminder moves to 'done' after its occurrence "
    "whatever the delivery said, so one that reached nobody (needs_setup, "
    "failed, unknown) reads as finished. The contract keeps task state "
    "(open/snoozed/user_reported_done/cancelled) apart from delivery state.",
)
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


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="Gap: the tick reads due reminders, then delivers; a cancel in "
    "between is not rechecked, so the occurrence still goes out. The "
    "contract rechecks cancellation immediately before dispatch.",
)
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
