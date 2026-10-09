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
* the daily cap counts across workspaces (pinned);
* a late answer after the sweep, a provider timeout, a claim that never
  dialled, and cancelling once the call is queued (xfail: these need the
  dispatch record, the unknown state and a cancellable occurrence).

No call is placed: ``calls._dial`` is replaced, and the line is a stand-in.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from api import constants
from api.db import db_client
from api.services import call_when_done as cwd
from api.services.call_when_done import calls, optin
from api.services.compliance import dnd
from api.tests import care_support as cs
from api.tests.test_call_when_done import (  # noqa: F401
    _confirm_number,
    _finish,
    _ist,
    _notices,
    _rows,
    _task,
    home,
)

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
    async def test_a_person_removed_after_the_call_was_queued_is_not_rung(
        self, home
    ):
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
        other = await cs.workspace(home.asha.id)
        await db_client.add_user_to_organization(home.asha.id, other)
        await _confirm_number(home)
        from api.services.call_when_done import number

        event_id = await number.propose(
            other, home.asha.id, "+919876543210", thread_id="t1"
        )
        await cs.press(other, event_id, home.asha.id)
        await _queued(home)
        await calls.tick(home.clock["now"] + timedelta(minutes=2))
        home.clock["now"] = _ist(13, 0)
        await _queued(home, "Second", org=other)
        await calls.tick(home.clock["now"] + timedelta(minutes=2))
        home.dial.assert_awaited_once()
        second = (await _rows("done_calls", other))[0]
        assert second["state"] == cwd.NOTIFIED and second["reason"] == "daily_cap"
        await _forget(other)


async def _forget(org: int) -> None:
    from sqlalchemy import text

    async with db_client.async_session() as session:
        for table in ("done_callbacks", "done_calls", "done_call_numbers"):
            await session.execute(
                text(f"DELETE FROM {table} WHERE organization_id = :o"), {"o": org}
            )
        await session.commit()
    await cs.cleanup(org)


class TestUnknownOutcomes:
    @pytest.mark.xfail(
        strict=True,
        raises=AssertionError,
        reason="Gap: the sweep settles a call with no report as not_answered and "
        "says 'you did not pick up'; a real answer arriving later cannot move "
        "it. The contract keeps delivery 'unknown' until reconciled.",
    )
    async def test_an_answer_after_the_sweep_is_kept(self, home):
        await _confirm_number(home)
        await _queued(home)
        await calls.tick(home.clock["now"] + timedelta(minutes=2))
        call_id = (await _rows("done_calls", home.org))[0]["id"]
        await calls.sweep(home.clock["now"] + timedelta(hours=1))
        await _report(home, _run(call_id, answered=True), call_id)
        assert (await _rows("done_calls", home.org))[0]["state"] == cwd.ANSWERED

    @pytest.mark.xfail(
        strict=True,
        raises=AssertionError,
        reason="Gap: a timeout inside dial_workflow is recorded as failed and the "
        "person is told 'I couldn't call you', though the carrier may have rung "
        "them. The contract records 'unknown' and reconciles first.",
    )
    async def test_a_provider_timeout_is_unknown_not_failed(self, home):
        home.dial.side_effect = asyncio.TimeoutError()
        await _confirm_number(home)
        await _queued(home)
        await calls.tick(home.clock["now"] + timedelta(minutes=2))
        call = (await _rows("done_calls", home.org))[0]
        assert call["state"] != cwd.FAILED
        assert "couldn't call you" not in (await _notices(home.org))[-1]

    @pytest.mark.xfail(
        strict=True,
        raises=AssertionError,
        reason="Gap: a call claimed by a tick that died before dialling is swept "
        "as 'you did not pick up', though nothing rang. The contract reserves a "
        "dispatch record; only a dispatched call can be unanswered.",
    )
    async def test_a_claim_that_never_dialled_is_not_reported_as_unanswered(
        self, home
    ):
        await _confirm_number(home)
        await _queued(home)
        call_id = (await _rows("done_calls", home.org))[0]["id"]
        assert await calls._claim(call_id, home.clock["now"])  # worker dies here
        await calls.sweep(home.clock["now"] + timedelta(hours=1))
        assert (await _rows("done_calls", home.org))[0]["state"] != cwd.NOT_ANSWERED


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
