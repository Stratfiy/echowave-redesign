"""Call me when it's done, at the edges: races, duplicates and unknown outcomes.

Stage 1 of the reminder-call contract (docs/plans/reminder-calls.md). Pins
what this path does where a general reminder call would reuse it, and marks
where it falls short:

* two ticks at once place one call; a post-call report delivered twice says
  "you did not pick up" once (pinned);
* the 09:00-21:00 window holds even where a deployment switches do-not-call
  enforcement off (fixed: the gate skipped the window with the list);
* a person removed from the workspace after the call was queued is not rung
  (fixed: membership is re-checked before the dial);
* the daily cap is a reservation per person per local day, across
  workspaces: two workspaces at once with one slot left ring once; the day
  turns over at the person's midnight; a crash after the reservation gives
  the slot back only when nothing was requested (F4);
* no report is ``unknown``, not "not answered"; a provider timeout after the
  run was recorded is ``unknown``, never re-dialled; a claim that never
  dialled is queued again; late and duplicated reports settle once, with a
  correction only where a line said something false (F3);
* cancelling once the call is queued (xfail: needs a cancellable occurrence).

No call is placed: ``calls._dial`` is replaced, and the line is a stand-in.
Run evidence is a stand-in run shaped as the status webhook leaves it.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import text

from api import constants
from api.db import db_client
from api.services import call_when_done as cwd
from api.services.call_when_done import allowance, calls, optin
from api.services.compliance import dnd
from api.tests import care_support as cs
from api.tests import test_call_when_done as base
from api.tests.test_call_when_done import (
    IST,
    _confirm_number,
    _finish,
    _ist,
    _notices,
    _rows,
    _task,
)

#: The suite's workspace: Asha, a stand-in line, and ``_dial`` replaced.
home = base.home

pytestmark = pytest.mark.asyncio


async def _queued(h, title: str = "Deploy the site", org: int | None = None):
    org = org or h.org
    task = await _task(org, h.asha.id, title)
    await optin.opt_in(org, h.asha.id, thread_id="t1", task_id=task.id)
    await _finish(org, task)
    return task


async def _report(h, run, call_id: int) -> None:
    with (
        patch.object(db_client, "get_workflow_run", new=AsyncMock(return_value=run)),
        patch.object(
            db_client,
            "get_organization_id_by_workflow_run_id",
            new=AsyncMock(return_value=h.org),
        ),
    ):
        await calls.record_run_outcome(4242)


def _run(call_id: int, *, answered: bool):
    return SimpleNamespace(
        initial_context={"done_call_id": call_id},
        answered_at=object() if answered else None,
        billable_seconds=30 if answered else 0,
    )


class TestDuplicates:
    async def test_two_ticks_at_once_place_one_call(self, home):
        await _confirm_number(home)
        await _queued(home)
        due = home.clock["now"] + timedelta(minutes=2)
        handled = await asyncio.gather(calls.tick(due), calls.tick(due))
        assert sum(handled) == 1
        home.dial.assert_awaited_once()

    async def test_a_report_delivered_twice_says_not_answered_once(self, home):
        await _confirm_number(home)
        await _queued(home)
        await calls.tick(home.clock["now"] + timedelta(minutes=2))
        call_id = (await _rows("done_calls", home.org))[0]["id"]
        before = len(await _notices(home.org))
        await _report(home, _run(call_id, answered=False), call_id)
        await _report(home, _run(call_id, answered=False), call_id)
        call = (await _rows("done_calls", home.org))[0]
        assert call["state"] == cwd.NOT_ANSWERED
        said = (await _notices(home.org))[before:]
        assert len([s for s in said if "you did not pick up" in s]) == 1


class TestQuietHours:
    async def test_the_window_holds_with_do_not_call_enforcement_off(
        self, home, monkeypatch
    ):
        """``dnd.assert_may_call`` returns early, window and all, when a
        deployment sets DND_ENFORCEMENT_ENABLED=false. The window for these
        calls is the founder's rule, not the list's, so it is asked first."""
        monkeypatch.setattr(dnd, "DND_ENFORCEMENT_ENABLED", False)
        await _confirm_number(home)
        await _queued(home)
        assert await calls.tick(_ist(21, 5)) == 1
        home.dial.assert_not_awaited()
        call = (await _rows("done_calls", home.org))[0]
        assert call["state"] == cwd.QUEUED and call["due_at"] == _ist(9, 0, days=1)

    async def test_the_last_minute_before_nine_pm_still_rings(self, home):
        await _confirm_number(home)
        await _queued(home)
        assert await calls.tick(_ist(20, 59)) == 1
        home.dial.assert_awaited_once()


class TestRecipient:
    async def test_a_person_removed_after_the_call_was_queued_is_not_rung(self, home):
        await _confirm_number(home)
        await _queued(home)
        await db_client.remove_user_from_organization(home.asha.id, home.org)
        await calls.tick(home.clock["now"] + timedelta(minutes=2))
        home.dial.assert_not_awaited()
        call = (await _rows("done_calls", home.org))[0]
        assert call["state"] == cwd.FAILED and call["reason"] == "not_member"


class TestTheDailyCap:
    async def test_the_cap_counts_calls_from_every_workspace(self, home, monkeypatch):
        """The cap protects the person, not a workspace's budget."""
        monkeypatch.setattr(constants, "CALL_WHEN_DONE_DAILY_CAP", 1)
        other = await _second_workspace(home)
        await _queued(home)
        await calls.tick(home.clock["now"] + timedelta(minutes=2))
        home.clock["now"] = _ist(13, 0)
        await _queued(home, "Second", org=other)
        await calls.tick(home.clock["now"] + timedelta(minutes=2))
        home.dial.assert_awaited_once()
        second = (await _rows("done_calls", other))[0]
        assert second["state"] == cwd.NOTIFIED and second["reason"] == "daily_cap"
        await _forget(other)

    async def test_two_workspaces_at_once_with_one_slot_left_ring_once(
        self, home, monkeypatch
    ):
        """F4: both calls are claimed and placed concurrently, each from its
        own workspace, with one slot left. The reservation is one
        conditional upsert, so at most one dispatch; the other is told the
        day's limit is reached and holds no slot."""
        monkeypatch.setattr(constants, "CALL_WHEN_DONE_DAILY_CAP", 3)
        other = await _second_workspace(home)
        await _use_slots(home, 2)
        await _queued(home, "Mine")
        await _queued(home, "Theirs", org=other)
        mine = (await _rows("done_calls", home.org))[-1]["id"]
        theirs = (await _rows("done_calls", other))[0]["id"]
        now = home.clock["now"] + timedelta(minutes=2)
        assert await calls._claim(mine, now) and await calls._claim(theirs, now)
        await asyncio.gather(calls.place(mine, now=now), calls.place(theirs, now=now))
        assert home.dial.await_count == 1
        states = sorted(
            [
                (await _call(mine))["state"],
                (await _call(theirs))["state"],
            ]
        )
        assert states == sorted([cwd.CALLING, cwd.NOTIFIED])
        assert await allowance.used(home.asha.id, "Asia/Kolkata", now) == 3
        await _forget(other)

    async def test_many_reservations_at_once_never_pass_the_cap(
        self, home, monkeypatch
    ):
        monkeypatch.setattr(constants, "CALL_WHEN_DONE_DAILY_CAP", 5)
        ids = [await _bare_call(home) for _ in range(12)]
        now = home.clock["now"]
        took = await asyncio.gather(
            *(allowance.reserve(i, "Asia/Kolkata", now) for i in ids)
        )
        assert sum(took) == 5
        assert await allowance.used(home.asha.id, "Asia/Kolkata", now) == 5
        # A call that already holds a slot is not charged twice.
        holder = ids[took.index(True)]
        assert await allowance.reserve(holder, "Asia/Kolkata", now)
        assert await allowance.used(home.asha.id, "Asia/Kolkata", now) == 5

    async def test_the_day_is_the_persons_own_midnight_to_midnight(
        self, home, monkeypatch
    ):
        monkeypatch.setattr(constants, "CALL_WHEN_DONE_DAILY_CAP", 1)
        late = _ist(23, 59)
        early = _ist(0, 1, days=1)
        first, second, third = (
            await _bare_call(home),
            await _bare_call(home),
            await _bare_call(home),
        )
        assert await allowance.reserve(first, "Asia/Kolkata", late)
        assert not await allowance.reserve(second, "Asia/Kolkata", late)
        # Two minutes later it is the next day in Kolkata: a fresh slot.
        assert await allowance.reserve(second, "Asia/Kolkata", early)
        assert not await allowance.reserve(third, "Asia/Kolkata", early)
        # 00:01 in Kolkata is still 22:31 the day before in Dubai: for a
        # person there it is the first day's slot, already taken.
        assert (
            allowance.local_day("Asia/Dubai", early)
            == late.astimezone(ZoneInfo("Asia/Dubai")).date()
        )
        assert not await allowance.reserve(third, "Asia/Dubai", early)

    async def test_the_window_and_the_cap_turn_over_at_the_persons_midnight(
        self, home, monkeypatch
    ):
        """End to end: five calls on Monday, the sixth finish at 20:50 is a
        notice; the seventh, finished at 23:30, waits for 09:00 Tuesday and
        rings then, on Tuesday's allowance."""
        monkeypatch.setattr(constants, "CALL_WHEN_DONE_DAILY_CAP", 5)
        await _confirm_number(home)
        await _use_slots(home, 5)
        home.clock["now"] = _ist(20, 50)
        await _queued(home, "Sixth")
        assert (await _rows("done_calls", home.org))[-1]["reason"] == "daily_cap"
        home.clock["now"] = _ist(23, 30)
        await _queued(home, "Seventh")
        queued = (await _rows("done_calls", home.org))[-1]
        assert queued["state"] == cwd.QUEUED
        assert queued["due_at"] == _ist(9, 0, days=1)
        await calls.tick(_ist(9, 0, days=1) + timedelta(seconds=5))
        home.dial.assert_awaited_once()
        rung = await _call(queued["id"])
        assert rung["allowance_day"] == _ist(9, 0, days=1).astimezone(IST).date()

    async def test_a_crash_after_the_reservation_gives_the_slot_back(
        self, home, monkeypatch
    ):
        """The worker reserved the slot and died before asking the provider
        (no run recorded). Verified non-dispatch: the sweep releases the
        slot and queues the call again; the next tick rings once."""
        monkeypatch.setattr(constants, "CALL_WHEN_DONE_DAILY_CAP", 1)
        await _confirm_number(home)
        await _queued(home)
        call_id = (await _rows("done_calls", home.org))[0]["id"]
        now = home.clock["now"] + timedelta(minutes=2)
        assert await calls._claim(call_id, now)
        assert await allowance.reserve(call_id, "Asia/Kolkata", now)  # dies here
        assert await allowance.remaining(home.asha.id, "Asia/Kolkata", now) == 0
        later = now + timedelta(minutes=30)
        await calls.sweep(later)
        call = await _call(call_id)
        assert call["state"] == cwd.QUEUED and call["allowance_day"] is None
        assert await allowance.remaining(home.asha.id, "Asia/Kolkata", later) == 1
        assert await calls.tick(later + timedelta(minutes=1)) == 1
        home.dial.assert_awaited_once()
        assert await allowance.remaining(home.asha.id, "Asia/Kolkata", later) == 0
        assert "did not pick up" not in " ".join(await _notices(home.org))

    async def test_a_crash_after_the_provider_was_asked_keeps_the_slot(
        self, home, monkeypatch
    ):
        """With a run recorded the call may have rung: it counts."""
        monkeypatch.setattr(constants, "CALL_WHEN_DONE_DAILY_CAP", 1)
        home.dial.side_effect = _accepted_then(TimeoutError())
        await _confirm_number(home)
        await _queued(home)
        now = home.clock["now"] + timedelta(minutes=2)
        await calls.tick(now)
        call = (await _rows("done_calls", home.org))[0]
        assert call["state"] == cwd.UNKNOWN and call["allowance_day"] is not None
        await calls.sweep(now + timedelta(hours=1))
        assert await allowance.remaining(home.asha.id, "Asia/Kolkata", now) == 0

    async def test_a_busy_line_gives_the_slot_back(self, home, monkeypatch):
        """The provider's concurrency limit is not the person's cap: a dial
        refused for a busy line was never placed, and its slot returns."""
        monkeypatch.setattr(constants, "CALL_WHEN_DONE_DAILY_CAP", 1)
        home.dial.side_effect = calls._Refused("line_busy")
        await _confirm_number(home)
        await _queued(home)
        now = home.clock["now"] + timedelta(minutes=2)
        await calls.tick(now)
        call = (await _rows("done_calls", home.org))[0]
        assert call["state"] == cwd.FAILED and call["reason"] == "line_busy"
        assert call["allowance_day"] is None
        assert await allowance.remaining(home.asha.id, "Asia/Kolkata", now) == 1


async def _second_workspace(h) -> int:
    other = await cs.workspace(h.asha.id)
    await db_client.add_user_to_organization(h.asha.id, other)
    await _confirm_number(h)
    from api.services.call_when_done import number

    event_id = await number.propose(other, h.asha.id, "+919876543210", thread_id="t1")
    await cs.press(other, event_id, h.asha.id)
    return other


async def _bare_call(h) -> int:
    """A done_calls row for Asha, claimed, as a placing worker holds it."""
    from api.db.call_when_done_models import DoneCallModel

    async with db_client.async_session() as session:
        row = DoneCallModel(
            organization_id=h.org,
            user_id=h.asha.id,
            state=cwd.CALLING,
            due_at=h.clock["now"],
            created_at=h.clock["now"],
            attempts=1,
        )
        session.add(row)
        await session.commit()
        return row.id


async def _use_slots(h, n: int) -> None:
    for _ in range(n):
        assert await allowance.reserve(
            await _bare_call(h), "Asia/Kolkata", h.clock["now"]
        )
    async with db_client.async_session() as session:
        await session.execute(
            text(
                "UPDATE done_calls SET state = 'answered' "
                "WHERE organization_id = :o AND state = 'calling'"
            ),
            {"o": h.org},
        )
        await session.commit()


async def _call(call_id: int):
    async with db_client.async_session() as session:
        return (
            (
                await session.execute(
                    text("SELECT * FROM done_calls WHERE id = :i"), {"i": call_id}
                )
            )
            .mappings()
            .one()
        )


async def _forget(org: int) -> None:
    async with db_client.async_session() as session:
        for table in ("done_callbacks", "done_calls", "done_call_numbers"):
            await session.execute(
                text(f"DELETE FROM {table} WHERE organization_id = :o"), {"o": org}
            )
        await session.commit()
    await cs.cleanup(org)


def _accepted_then(error: Exception, run_id: int = 4242):
    """The real dial's shape: the run is recorded (``on_run_created``), the
    provider is asked, and the request then fails on our side."""

    async def dial(call, dialable, *, on_run_created=None):
        await on_run_created(run_id)
        raise error

    return dial


def _evidence(**kw):
    """A run as the telephony status webhook and the pipeline leave it."""
    base = {
        "is_completed": False,
        "answered_at": None,
        "billable_seconds": None,
        "gathered_context": {},
        "initial_context": {},
    }
    base.update(kw)
    return SimpleNamespace(**base)


NO_ANSWER = {
    "is_completed": True,
    "gathered_context": {
        "call_tags": ["not_connected", "telephony_no-answer"],
        "mapped_call_disposition": "no-answer",
    },
}


class TestUnknownOutcomes:
    async def test_an_answer_after_the_sweep_is_kept(self, home):
        await _confirm_number(home)
        await _queued(home)
        await calls.tick(home.clock["now"] + timedelta(minutes=2))
        call_id = (await _rows("done_calls", home.org))[0]["id"]
        await calls.sweep(home.clock["now"] + timedelta(hours=1))
        await _report(home, _run(call_id, answered=True), call_id)
        assert (await _rows("done_calls", home.org))[0]["state"] == cwd.ANSWERED

    async def test_no_report_is_unknown_and_said_so_never_not_answered(self, home):
        await _confirm_number(home)
        await _queued(home)
        await calls.tick(home.clock["now"] + timedelta(minutes=2))
        with patch.object(
            db_client, "get_workflow_run", new=AsyncMock(return_value=_evidence())
        ):
            await calls.sweep(home.clock["now"] + timedelta(hours=1))
            await calls.sweep(home.clock["now"] + timedelta(hours=2))
        call = (await _rows("done_calls", home.org))[0]
        assert call["state"] == cwd.UNKNOWN and call["reason"] == "no_outcome"
        said = await _notices(home.org)
        assert not any("did not pick up" in s for s in said)
        assert len([s for s in said if "can't confirm the call reached you" in s]) == 1
        # The result itself is in that one line.
        assert "Deployed. Three pages changed." in said[-1]
        home.dial.assert_awaited_once()

    async def test_a_provider_timeout_is_unknown_not_failed(self, home):
        """Provider accepted, client timed out: the run was recorded before
        the provider was asked, so the call may have rung."""
        home.dial.side_effect = _accepted_then(TimeoutError())
        await _confirm_number(home)
        await _queued(home)
        await calls.tick(home.clock["now"] + timedelta(minutes=2))
        call = (await _rows("done_calls", home.org))[0]
        assert call["state"] != cwd.FAILED
        assert "couldn't call you" not in (await _notices(home.org))[-1]
        assert call["state"] == cwd.UNKNOWN and call["reason"] == "dial_unconfirmed"
        assert call["workflow_run_id"] == 4242
        # Never re-dialled while unknown, however often the tick and the
        # sweep run.
        for minutes in (3, 10, 30, 90):
            await calls.tick(home.clock["now"] + timedelta(minutes=minutes))
            await calls.sweep(home.clock["now"] + timedelta(minutes=minutes))
        home.dial.assert_awaited_once()

    async def test_a_timeout_then_the_carriers_no_answer_settles_once(self, home):
        home.dial.side_effect = _accepted_then(TimeoutError())
        await _confirm_number(home)
        await _queued(home)
        await calls.tick(home.clock["now"] + timedelta(minutes=2))
        before = len(await _notices(home.org))
        with patch.object(
            db_client,
            "get_workflow_run",
            new=AsyncMock(return_value=_evidence(**NO_ANSWER)),
        ):
            await calls.sweep(home.clock["now"] + timedelta(minutes=30))
            await calls.sweep(home.clock["now"] + timedelta(minutes=40))
        call = (await _rows("done_calls", home.org))[0]
        assert call["state"] == cwd.NOT_ANSWERED
        # Already told "can't confirm" with the result: nothing more.
        assert len(await _notices(home.org)) == before
        assert [(e["from"], e["to"]) for e in call["outcome_history"]] == [
            (cwd.CALLING, cwd.UNKNOWN),
            (cwd.UNKNOWN, cwd.NOT_ANSWERED),
        ]

    async def test_a_timeout_with_the_answer_already_recorded_is_answered(self, home):
        """Reconciled right away: the carrier's answered stamp is already
        on the run, so it is answered, and nothing is said."""
        home.dial.side_effect = _accepted_then(TimeoutError())
        await _confirm_number(home)
        await _queued(home)
        before = len(await _notices(home.org))
        with patch.object(
            db_client,
            "get_workflow_run",
            new=AsyncMock(return_value=_evidence(answered_at=home.clock["now"])),
        ):
            await calls.tick(home.clock["now"] + timedelta(minutes=2))
        assert (await _rows("done_calls", home.org))[0]["state"] == cwd.ANSWERED
        assert len(await _notices(home.org)) == before

    async def test_a_claim_that_never_dialled_is_not_reported_as_unanswered(self, home):
        await _confirm_number(home)
        await _queued(home)
        call_id = (await _rows("done_calls", home.org))[0]["id"]
        assert await calls._claim(call_id, home.clock["now"])  # worker dies here
        await calls.sweep(home.clock["now"] + timedelta(hours=1))
        assert (await _rows("done_calls", home.org))[0]["state"] != cwd.NOT_ANSWERED

    async def test_a_crash_before_the_run_is_recorded_is_dialled_once_later(self, home):
        """Worker crash before run-id linkage: ``dial_workflow`` records the
        run before asking the provider, so no run recorded means nothing
        was requested. Queued again, rung once, nobody told "missed"."""
        await _confirm_number(home)
        await _queued(home)
        call_id = (await _rows("done_calls", home.org))[0]["id"]
        assert await calls._claim(call_id, home.clock["now"])  # dies here
        later = home.clock["now"] + timedelta(hours=1)
        assert await calls.sweep(later) == 1
        call = await _call(call_id)
        assert call["state"] == cwd.QUEUED and call["workflow_run_id"] is None
        assert call["outcome_history"][-1]["reason"] == "not_dialled"
        assert await calls.tick(later + timedelta(seconds=1)) == 1
        assert await calls.tick(later + timedelta(minutes=2)) == 0
        home.dial.assert_awaited_once()
        said = " ".join(await _notices(home.org))
        assert "did not pick up" not in said and "couldn't call" not in said

    async def test_an_error_before_any_run_is_retried_then_told(self, home):
        """A dial that keeps failing before a run exists is a verified
        non-dispatch each time: queued again, at most MAX_ATTEMPTS claims,
        then the person is told -- once."""
        home.dial.side_effect = RuntimeError("boom")
        await _confirm_number(home)
        await _queued(home)
        now = home.clock["now"] + timedelta(minutes=2)
        for n in range(calls.MAX_ATTEMPTS + 2):
            await calls.tick(now + timedelta(minutes=n))
        assert home.dial.await_count == calls.MAX_ATTEMPTS
        call = (await _rows("done_calls", home.org))[0]
        assert call["state"] == cwd.NOTIFIED and call["reason"] == "call_error"
        said = await _notices(home.org)
        assert len([s for s in said if "couldn't call you" in s]) == 1

    async def test_a_delayed_and_duplicated_outcome_settles_once(self, home):
        await _confirm_number(home)
        await _queued(home)
        await calls.tick(home.clock["now"] + timedelta(minutes=2))
        call_id = (await _rows("done_calls", home.org))[0]["id"]
        await calls.sweep(home.clock["now"] + timedelta(hours=1))  # unknown
        before = len(await _notices(home.org))
        await _report(home, _run(call_id, answered=False), call_id)
        await _report(home, _run(call_id, answered=False), call_id)
        await _report(home, _run(call_id, answered=True), call_id)
        await _report(home, _run(call_id, answered=True), call_id)
        await _report(home, _run(call_id, answered=False), call_id)  # stale
        call = (await _rows("done_calls", home.org))[0]
        assert call["state"] == cwd.ANSWERED
        # "Unknown" was said with the result; nothing it said was false, so
        # no further line.
        assert len(await _notices(home.org)) == before
        assert [(e["from"], e["to"]) for e in call["outcome_history"]] == [
            (cwd.CALLING, cwd.UNKNOWN),
            (cwd.UNKNOWN, cwd.NOT_ANSWERED),
            (cwd.NOT_ANSWERED, cwd.ANSWERED),
        ]

    async def test_an_answer_after_a_verified_no_answer_is_corrected_once(self, home):
        await _confirm_number(home)
        await _queued(home)
        await calls.tick(home.clock["now"] + timedelta(minutes=2))
        call_id = (await _rows("done_calls", home.org))[0]["id"]
        await _report(home, _run(call_id, answered=False), call_id)
        assert "you did not pick up" in (await _notices(home.org))[-1]
        await _report(home, _run(call_id, answered=True), call_id)
        await _report(home, _run(call_id, answered=True), call_id)
        said = await _notices(home.org)
        assert len([s for s in said if s.startswith("Correction:")]) == 1
        assert (await _call(call_id))["state"] == cwd.ANSWERED

    async def test_a_report_for_another_run_is_ignored(self, home):
        await _confirm_number(home)
        await _queued(home)
        await calls.tick(home.clock["now"] + timedelta(minutes=2))
        call_id = (await _rows("done_calls", home.org))[0]["id"]
        with (
            patch.object(
                db_client,
                "get_workflow_run",
                new=AsyncMock(return_value=_run(call_id, answered=True)),
            ),
            patch.object(
                db_client,
                "get_organization_id_by_workflow_run_id",
                new=AsyncMock(return_value=home.org),
            ),
        ):
            await calls.record_run_outcome(9999)  # not the call's run (4242)
        assert (await _call(call_id))["state"] == cwd.CALLING


class TestCancellation:
    @pytest.mark.xfail(
        strict=True,
        raises=AssertionError,
        reason="Gap: once the finish has queued the call (for example overnight "
        "until 09:00), 'cancel' only moves pending callbacks, so the person "
        "cannot stop the call. The contract makes each occurrence cancellable "
        "and rechecks cancellation right before the dial.",
    )
    async def test_cancelling_a_queued_call_stops_it(self, home):
        await _confirm_number(home)
        home.clock["now"] = _ist(22, 30)
        await _queued(home)
        callback = (await _rows("done_callbacks", home.org))[0]
        await optin.cancel(home.org, home.asha.id, callback["id"])
        await calls.tick(_ist(9, 0, days=1) + timedelta(seconds=5))
        home.dial.assert_not_awaited()
