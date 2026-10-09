"""Reminder calls: the verification fixes (gaps G1, G2, G3 and G7 of the
9 October intelligence verification; audit F5a, F5b, F5c, F5e, P6).

* **A cancelled occurrence never rings** (F5b, the rollout blocker): a
  cancel that lands after the gate passed but before the provider is asked
  is seen by the conditional claim in ``calls.place`` / its ``link`` hook,
  under the same row locks the cancel takes, and ``schedule.cancel`` skips
  queued attempts in its own transaction. Real Postgres, real concurrency.
* **Snooze** (F5c, P6): one transaction; a length that is missing, unclear
  or out of bounds is asked back on the thread, never guessed.
* **The window** (F5a, decision D2): one constant in ``policy``; under the
  default 09:00 start, 08:30 is a card for 09:00 that says why.
* **Today** (F5e): "Call me" in the reminder editor returns the same card.

No call is placed: ``calls._dial`` is the stand-in from ``test_reminder_calls``
and ``home.provider`` is the moment the provider would be asked.
"""

from __future__ import annotations

import asyncio
import random
from datetime import UTC, datetime, time, timedelta

import pytest
from sqlalchemy import text

from api.db import db_client
from api.services import reminder_calls as rc
from api.services.call_when_done import allowance
from api.services.reminder_calls import calls, cards, gate, policy, schedule, tools
from api.services.today.scope import full_local
from api.services.workflow import actions
from api.tests import care_support as cs
from api.tests import test_reminder_calls as base
from api.tests.test_reminder_calls import (
    IST,
    _cards,
    _confirm_number,
    _dispatches,
    _ist,
    _notices,
    _press,
    _report,
    _ring,
    _rows,
    _run,
    _set,
)

home = base.home

pytestmark = pytest.mark.asyncio


async def _used(h, days: int = 1) -> int:
    return await allowance.used(h.asha.id, "Asia/Kolkata", _ist(10, 30, days=days))


# --- F5b: a cancelled occurrence never rings ------------------------------------


class TestACancelledOccurrenceNeverRings:
    async def test_cancel_between_the_gate_and_the_dial_prevents_the_call(
        self, home, monkeypatch
    ):
        """Was a strict xfail in the verification branch's conformance tests."""
        saved = await _set(home)
        real = gate.may_dial

        async def gate_then_cancel(dispatch_id, now):
            verdict = await real(dispatch_id, now)
            await schedule.cancel(home.org, home.asha.id, saved["id"])
            return verdict

        monkeypatch.setattr(gate, "may_dial", gate_then_cancel)
        await calls.tick(_ist(10, 30, days=1))
        home.dial.assert_not_awaited()
        home.provider.assert_not_awaited()
        dispatch = (await _dispatches(home))[0]
        assert (dispatch["state"], dispatch["reason"]) == (rc.SKIPPED, "cancelled")
        assert dispatch["workflow_run_id"] is None
        assert await _used(home) == 0  # the slot the gate took went back
        home.told.assert_not_awaited()  # the person stopped it: nothing sent

    async def test_cancel_as_the_run_is_recorded_prevents_the_call(
        self, home, monkeypatch
    ):
        """The last moment: the run exists, the provider is next."""
        saved = await _set(home)
        real = calls._claim_to_dial

        async def cancel_then_claim(dispatch_id, organization_id, run_id):
            if run_id is not None:
                await schedule.cancel(home.org, home.asha.id, saved["id"])
            return await real(dispatch_id, organization_id, run_id)

        monkeypatch.setattr(calls, "_claim_to_dial", cancel_then_claim)
        await calls.tick(_ist(10, 30, days=1))
        home.provider.assert_not_awaited()
        dispatch = (await _dispatches(home))[0]
        assert (dispatch["state"], dispatch["reason"]) == (rc.SKIPPED, "cancelled")
        assert dispatch["workflow_run_id"] is None
        assert await _used(home) == 0

    async def test_done_in_the_app_between_the_gate_and_the_dial_prevents_the_call(
        self, home, monkeypatch
    ):
        await _set(home)
        await calls._materialise(_ist(10, 30, days=1))
        occurrence = (await _rows("reminder_call_occurrences", home.org))[0]
        real = gate.may_dial

        async def gate_then_done(dispatch_id, now):
            verdict = await real(dispatch_id, now)
            await schedule.mark_done(home.org, home.asha.id, occurrence["id"])
            return verdict

        monkeypatch.setattr(gate, "may_dial", gate_then_done)
        await calls.tick(_ist(10, 30, days=1))
        home.provider.assert_not_awaited()
        dispatch = (await _dispatches(home))[0]
        assert (dispatch["state"], dispatch["reason"]) == (rc.SKIPPED, "done")
        assert await _used(home) == 0

    async def test_cancel_skips_a_queued_attempt_in_the_same_transaction(self, home):
        saved = await _set(home)
        await calls._materialise(_ist(10, 30, days=1))
        assert (await _dispatches(home))[0]["state"] == rc.QUEUED
        assert await schedule.cancel(home.org, home.asha.id, saved["id"])
        dispatch = (await _dispatches(home))[0]
        assert (dispatch["state"], dispatch["reason"]) == (rc.SKIPPED, "cancelled")
        assert dispatch["outcome_history"][-1]["source"] == "person"
        occurrence = (await _rows("reminder_call_occurrences", home.org))[0]
        assert occurrence["task_state"] == rc.TASK_CANCELLED
        await calls.tick(_ist(10, 30, days=1))
        home.dial.assert_not_awaited()

    async def test_a_cancel_after_the_provider_was_asked_leaves_the_ring_alone(
        self, home
    ):
        """Past the point of no return the attempt is not rewritten as
        "skipped": it rang, and its record says so."""
        row = await _ring(home)
        saved = (await _rows("reminder_call_schedules", home.org))[0]
        assert await schedule.cancel(home.org, home.asha.id, saved["id"])
        dispatch = (await _dispatches(home))[0]
        assert dispatch["id"] == row["id"]
        assert dispatch["state"] == rc.ACCEPTED
        assert await _used(home) == 1

    async def test_a_cancel_holding_its_locks_makes_the_claim_wait_and_not_ring(
        self, home
    ):
        """Real Postgres row locks: a cancel still in its transaction when
        the tick reaches the queued attempt. The claim waits, then finds it
        skipped."""
        saved = await _set(home)
        await calls._materialise(_ist(10, 30, days=1))
        async with db_client.async_session() as cancelling:
            assert await schedule._stop(
                cancelling, home.org, home.asha.id, saved["id"], _ist(10, 30, days=1)
            )
            ticking = asyncio.create_task(calls.tick(_ist(10, 30, days=1)))
            await asyncio.sleep(0.5)
            assert not ticking.done()  # waiting on the cancel's row lock
            await cancelling.commit()
        await asyncio.wait_for(ticking, timeout=10)
        home.dial.assert_not_awaited()
        home.provider.assert_not_awaited()
        dispatch = (await _dispatches(home))[0]
        assert (dispatch["state"], dispatch["reason"]) == (rc.SKIPPED, "cancelled")
        assert await _used(home) == 0

    async def test_a_stop_in_flight_makes_the_dial_claim_wait_and_not_ring(self, home):
        """A writer that stops the schedule without touching the attempt and
        has not committed when the gate reads: the gate passes on what was
        committed, the conditional claim waits on the schedule's row lock,
        then refuses."""
        saved = await _set(home)
        await calls._materialise(_ist(10, 30, days=1))
        async with db_client.async_session() as stopping:
            await stopping.execute(
                text(
                    "UPDATE reminder_call_schedules SET state = 'cancelled' "
                    "WHERE id = :id"
                ),
                {"id": saved["id"]},
            )
            ticking = asyncio.create_task(calls.tick(_ist(10, 30, days=1)))
            await asyncio.sleep(0.5)
            assert not ticking.done()  # the claim waits on the schedule row
            assert (await _dispatches(home))[0]["state"] == rc.DISPATCHING
            await stopping.commit()
        await asyncio.wait_for(ticking, timeout=10)
        home.dial.assert_not_awaited()
        home.provider.assert_not_awaited()
        dispatch = (await _dispatches(home))[0]
        assert (dispatch["state"], dispatch["reason"]) == (rc.SKIPPED, "cancelled")
        assert await _used(home) == 0

    async def test_racing_ticks_and_cancels_never_ring_a_cancelled_occurrence(
        self, home
    ):
        """Ten reminders, each cancelled while two ticks race to ring it, at
        random offsets. Every attempt ends rung (and recorded as such, its
        slot held) or skipped (never asked of the provider, its slot given
        back); no deadlock, no lost slot, nothing in between."""
        jitter = random.Random(582)
        asked: list[int] = []
        home.provider.side_effect = lambda dispatch_id: asked.append(dispatch_id)

        async def cancel_after(delay: float, schedule_id: int) -> bool:
            await asyncio.sleep(delay)
            return await schedule.cancel(home.org, home.asha.id, schedule_id)

        async def tick_after(delay: float, at) -> int:
            await asyncio.sleep(delay)
            return await calls.tick(at)

        for day in range(1, 11):
            home.clock["now"] = _ist(10, 0, days=day - 1)
            saved = await _set(home)
            due = _ist(10, 30, days=day)
            if day % 2 == 0:
                # Half already have their queued attempt (the race is then
                # with the claim and the dial); half race the tick that
                # makes it.
                await calls._materialise(due)
            delays = [jitter.uniform(0, 0.04) for _ in range(3)]
            results = await asyncio.gather(
                cancel_after(delays[0], saved["id"]),
                tick_after(delays[1], due),
                tick_after(delays[2], due),
            )
            assert results[0] is True
        rows = await _dispatches(home)
        # A cancel that won the race to the schedule leaves no attempt at all.
        assert 5 <= len(rows) <= 10
        schedules = await _rows("reminder_call_schedules", home.org)
        assert {s["state"] for s in schedules} == {rc.CANCELLED}
        for row in rows:
            if row["id"] in asked:
                assert row["state"] == rc.ACCEPTED, row
                assert row["workflow_run_id"] is not None
                assert row["allowance_day"] is not None
            else:
                assert (row["state"], row["reason"]) == (rc.SKIPPED, "cancelled"), row
                assert row["allowance_day"] is None
        assert len(asked) == len(set(asked))  # never twice
        total = sum([await _used(home, days=d) for d in range(1, 11)])
        assert total == len(asked)


# --- F5c / P6: snooze ---------------------------------------------------------------


class TestSnooze:
    async def test_a_snooze_reported_twice_makes_exactly_one_new_occurrence(self, home):
        row = await _ring(home)
        await _report(home, _run(row["id"], reply="snooze", snooze="15"))
        await _report(home, _run(row["id"], reply="snooze", snooze="15"))
        occurrences = await _rows("reminder_call_occurrences", home.org)
        assert len(occurrences) == 2
        assert occurrences[0]["task_state"] == rc.SNOOZED
        assert occurrences[1]["task_state"] == rc.OPEN
        assert occurrences[1]["due_at"] == _ist(10, 45, days=1)
        await calls.tick(_ist(10, 45, days=1))
        await calls.tick(_ist(10, 46, days=1))
        assert home.dial.await_count == 2

    @pytest.mark.parametrize("said", [None, "", "later", "a bit", "300", "2"])
    async def test_an_unclear_snooze_is_asked_back_not_guessed(self, home, said):
        row = await _ring(home)
        await _report(home, _run(row["id"], reply="snooze", snooze=said))
        occurrences = await _rows("reminder_call_occurrences", home.org)
        assert len(occurrences) == 1  # nothing set on a guess
        assert occurrences[0]["task_state"] == rc.OPEN
        line = (await _notices(home.org))[-1]
        assert "I didn't catch when, so I haven't set another call" in line
        assert "How many minutes from now should I call (5 to 120)?" in line
        await calls.tick(_ist(10, 45, days=1))
        assert home.dial.await_count == 1

    @pytest.mark.parametrize(
        ("said", "minutes"),
        [("15", 15), (" 20 min", 20), ("30 minutes", 30), ("5", 5), ("120", 120)],
    )
    def test_a_clear_length_is_read(self, said, minutes):
        assert calls.snooze_minutes(said) == minutes

    async def test_the_snooze_and_its_next_ring_are_one_transaction(
        self, home, monkeypatch
    ):
        """A failure writing the follow-up ring leaves the task open, not
        snoozed with nothing to ring it again."""
        row = await _ring(home)

        async def broken(*args, **kwargs):
            raise RuntimeError("worker died")

        monkeypatch.setattr(calls, "_new_occurrence", broken)
        await _report(home, _run(row["id"], reply="snooze", snooze="15"))
        occurrences = await _rows("reminder_call_occurrences", home.org)
        assert len(occurrences) == 1
        assert occurrences[0]["task_state"] == rc.OPEN
        assert occurrences[0]["snoozed_until"] is None

    def test_the_call_asks_for_a_length_rather_than_picking_one(self):
        from api.services.reminder_calls import agent

        read_out = agent.definition()["nodes"][2]["data"]["prompt"]
        assert "ask how many minutes from now" in read_out
        assert "never guess" in agent.EXTRACTION_SNOOZE["prompt"]
        assert not hasattr(policy, "SNOOZE_DEFAULT_MINUTES")


# --- F5a / D2: 08:30 and the window --------------------------------------------------


async def _ask_830_tamil(h):
    await _confirm_number(h)
    told = await tools.ask(
        h.org,
        h.asha.id,
        {
            "title": "Send the proposal",
            "date": "tomorrow",
            "time": "8:30 am",
            "language": "ta",
        },
        thread_id="t1",
        now=h.clock["now"],
    )
    assert told["status"] == "proposed", told
    event_id, payload = (await _cards(h, actions.REMINDER_CALL))[-1]
    return event_id, payload


class TestTheWindow:
    async def test_default_window_card_says_0830_cannot_ring_and_offers_0900(
        self, home
    ):
        event_id, payload = await _ask_830_tamil(home)
        assert payload["args"]["asked_time"] == "08:30"
        assert payload["args"]["local_time"] == "09:00"
        assert payload["args"]["language"] == "ta"
        assert payload["label"].startswith("Tue 13 Oct 2026, 09:00 IST (Asia/Kolkata)")
        assert payload["label"].endswith("Tamil")
        assert payload["why"] == (
            "You asked for 08:30, which is outside calling hours: reminder calls "
            "ring only between 9:00 and 21:00 your time, so 08:30 can't ring. "
            "This card is for 09:00, the nearest time that can. Confirm it, or "
            "say another time."
        )
        done = await _press(home, event_id)
        assert done["state"] == "done"
        saved = (await _rows("reminder_call_schedules", home.org))[-1]
        assert saved["local_time"] == "09:00"
        assert saved["language"] == "ta"
        assert saved["timezone"] == "Asia/Kolkata"
        assert saved["next_due_at"] == _ist(9, 0, days=1)
        assert saved["version"] == done["confirmed"]["version"]

    async def test_one_constant_moves_the_window_for_the_card_and_the_gate(
        self, home, monkeypatch
    ):
        """The founder's one-line change: only ``policy`` is patched."""
        monkeypatch.setattr(policy, "CALLING_WINDOW_START", "08:00")
        event_id, payload = await _ask_830_tamil(home)
        assert payload["args"]["asked_time"] is None
        assert payload["args"]["local_time"] == "08:30"
        assert "between 8:00 and 21:00" in payload["effect"]
        await _press(home, event_id)
        saved = (await _rows("reminder_call_schedules", home.org))[-1]
        assert (saved["local_time"], saved["language"]) == ("08:30", "ta")
        assert saved["next_due_at"] == _ist(8, 30, days=1)
        assert await calls.tick(_ist(8, 29, days=1)) == 0
        assert await calls.tick(_ist(8, 30, days=1)) == 1
        home.dial.assert_awaited_once()

    def test_the_window_is_read_from_policy(self, monkeypatch):
        assert policy.window_words() == "9:00 and 21:00"
        monkeypatch.setattr(policy, "CALLING_WINDOW_START", "08:00")
        assert policy.window_words() == "8:00 and 21:00"
        assert policy.within_window("Asia/Kolkata", _ist(8, 15))
        assert cards.outside_window_words("07:30", "08:00").startswith(
            "You asked for 07:30, which is outside calling hours: reminder calls "
            "ring only between 8:00 and 21:00"
        )


# --- F5e: Today ------------------------------------------------------------------


class TestToday:
    async def test_call_me_in_the_editor_returns_the_number_card_then_the_reminder(
        self, home
    ):
        # Over HTTP the draft reads the real clock; the card and the
        # schedule read the test's, so the two agree on now.
        home.clock["now"] = datetime.now(UTC)
        tomorrow = (datetime.now(IST) + timedelta(days=1)).date()
        shown = full_local(
            datetime.combine(tomorrow, time(11, 0), tzinfo=IST), "Asia/Kolkata"
        )
        body = {
            "title": "Pay the electricity bill",
            "date": "tomorrow",
            "time": "11:00",
            "recurrence": "once",
            "timezone": "Asia/Kolkata",
            "phone_number": "+91 98765 43210",
        }
        async with cs.client(home.asha.id, home.org) as client:
            first = await client.post("/api/v1/reminder-calls/ask", json=body)
            assert first.status_code == 200, first.text
            card = first.json()["card"]
            assert card["payload"]["action"] == actions.REMINDER_CALL_NUMBER
            # Today asks again itself once the number is confirmed: the
            # number card carries no reminder of its own.
            assert "then" not in card["payload"]["args"]
        await _press(home, card["id"], attested=True)
        async with cs.client(home.asha.id, home.org) as client:
            again = await client.get(f"/api/v1/reminder-calls/cards/{card['id']}")
            assert again.json()["payload"]["state"] == "done"
            second = await client.post(
                "/api/v1/reminder-calls/ask", json={**body, "phone_number": None}
            )
            reminder = second.json()["card"]
            assert reminder["payload"]["action"] == actions.REMINDER_CALL
            assert reminder["payload"]["label"].startswith(f"{shown} · ")
        assert len(await _cards(home, actions.REMINDER_CALL)) == 1
        await _press(home, reminder["id"])
        async with cs.client(home.asha.id, home.org) as client:
            listed = (await client.get("/api/v1/reminder-calls")).json()["reminders"]
        assert listed[0]["title"] == "Pay the electricity bill"
        assert listed[0]["next_due_label"] == shown
        assert shown.endswith("11:00 IST (Asia/Kolkata)")

    async def test_no_line_says_so_and_offers_nothing_to_confirm(self, home):
        home.line.return_value = None
        async with cs.client(home.asha.id, home.org) as client:
            told = await client.post(
                "/api/v1/reminder-calls/ask",
                json={"title": "Call mum", "date": "tomorrow", "time": "11:00"},
            )
        assert told.status_code == 200
        assert told.json()["status"] == "no_line"
        assert told.json()["card"] is None
        assert "Choose In the app or Push instead" in told.json()["message"]

    async def test_a_card_is_the_persons_own(self, home):
        event_id = await _confirm_number(home)
        async with cs.client(home.bob.id, home.other_org) as client:
            refused = await client.get(f"/api/v1/reminder-calls/cards/{event_id}")
        assert refused.status_code == 404
        async with cs.client(home.asha.id, home.org) as client:
            mine = await client.get(f"/api/v1/reminder-calls/cards/{event_id}")
        assert mine.status_code == 200

    async def test_an_unknown_timezone_is_refused_not_replaced(self, home):
        async with cs.client(home.asha.id, home.org) as client:
            told = await client.post(
                "/api/v1/reminder-calls/ask",
                json={
                    "title": "Call mum",
                    "date": "tomorrow",
                    "time": "11:00",
                    "timezone": "Mars/Olympus",
                },
            )
        assert told.status_code == 422
        assert "timezone is not one I know" in told.json()["detail"]
