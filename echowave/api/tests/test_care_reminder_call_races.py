"""Medicine reminder calls at the edges: races, duplicates and unknown outcomes.

Stage 1 of the reminder-call contract (docs/plans/reminder-calls.md). These
pin what the care path does today where a general "call me to remind me"
would reuse it, and mark where it falls short of the contract:

* two ticks at once place one call (pinned);
* a dose claimed by a tick and then paused, removed or marked "I took it"
  before the dial rings nobody (fixed here: ``place`` re-reads both);
* a post-call report delivered twice tells the family once (pinned);
* the care path's calling-hours exemption (pinned, so nobody copies it by
  accident: general reminders must not inherit it);
* silence is ``unknown``, never "did not answer"; a provider timeout after
  the run was recorded is ``unknown`` and never re-dialled; a crash between
  claim and dial is a verified "could not call"; an answer after the sweep
  is kept; delayed and duplicated reports alert once, with a correction only
  where the family was told something false (F3, fixed here);
* "I took it" after a missed call overwrites the call's outcome (xfail: it
  needs the separate delivery and task states).

The clock is passed in; no call is placed: ``calls._dial`` or
``dial_workflow`` is replaced in every test that would reach a carrier.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from api import constants
from api.db import db_client
from api.db.care_models import CareMedicineModel
from api.services.care import calls, circle, medicines
from api.tests import test_care_medicine_calls as base
from api.tests.test_care_medicine_calls import _active, _at, _doses

#: The care suite's household (Amma, Priya told of missed doses, Ravi not).
home = base.home

pytestmark = pytest.mark.asyncio


async def _medicine(medicine_id: int):
    async with db_client.async_session() as session:
        return await session.get(CareMedicineModel, medicine_id)


async def _alerts(h) -> list[str]:
    return [a["title"] for a in (await circle.family_view(h.priya.id))[0]["alerts"]]


def _run(dose_id: int, answer: str | None):
    gathered = {"extracted_variables": {"dose_taken": answer}} if answer else {}
    return SimpleNamespace(
        initial_context={"care_dose_id": dose_id},
        gathered_context=gathered,
        annotations={},
    )


async def _report(h, run) -> None:
    """The post-call hook, as the carrier's completion would run it."""
    with (
        patch.object(db_client, "get_workflow_run", new=AsyncMock(return_value=run)),
        patch.object(
            db_client,
            "get_organization_id_by_workflow_run_id",
            new=AsyncMock(return_value=h.org),
        ),
    ):
        await calls.record_run_outcome(999)


@pytest.fixture
def dial(monkeypatch):
    """The carrier, replaced. Reminders are set up in test mode (the card
    needs a line or test mode); ``_live`` then turns test mode off so the
    real placing path runs up to this stand-in."""
    fake = AsyncMock(return_value=999)
    monkeypatch.setattr(calls, "_dial", fake)
    return fake


async def _live(h, monkeypatch, **kwargs) -> int:
    medicine_id = await _active(h, **kwargs)
    monkeypatch.setattr(constants, "CARE_CALLS_FAKE", "")
    return medicine_id


class TestDuplicates:
    async def test_two_ticks_at_once_place_one_call(self, home, dial, monkeypatch):
        await _live(home, monkeypatch)
        now = _at(8, 1)
        placed = await asyncio.gather(calls.tick(now), calls.tick(now))
        assert sum(placed) == 1
        dial.assert_awaited_once()
        assert len(await _doses(home.org)) == 1

    async def test_a_post_call_report_delivered_twice_tells_the_family_once(
        self, home, dial, monkeypatch
    ):
        await _live(home, monkeypatch)
        await calls.tick(_at(8, 1))
        dose_id = (await _doses(home.org))[0].id
        await _report(home, _run(dose_id, "not_yet"))
        await _report(home, _run(dose_id, "not_yet"))
        rows = await _doses(home.org)
        assert rows[0].state == calls.NOT_TAKEN
        assert len(await _alerts(home)) == 1


class TestCancellationRaces:
    async def test_paused_after_the_tick_claimed_the_dose_rings_nobody(
        self, home, dial, monkeypatch
    ):
        medicine_id = await _live(home, monkeypatch)
        med = await _medicine(medicine_id)
        dose_id = await calls._claim(med, _at(8))
        await medicines.pause(home.org, home.amma.id, medicine_id)
        await calls.place(dose_id)
        dial.assert_not_awaited()
        rows = await _doses(home.org)
        assert rows[0].state == calls.CANCELLED and rows[0].alerted_at is None
        assert await _alerts(home) == []
        # The sweep does not turn a stopped dose into "did not answer".
        assert await calls.sweep(datetime.now(UTC) + timedelta(hours=2)) == 0

    async def test_removed_after_the_tick_claimed_the_dose_rings_nobody(
        self, home, dial, monkeypatch
    ):
        medicine_id = await _live(home, monkeypatch)
        med = await _medicine(medicine_id)
        dose_id = await calls._claim(med, _at(20))
        await medicines.remove(home.org, home.amma.id, medicine_id)
        await calls.place(dose_id)
        dial.assert_not_awaited()
        assert (await _doses(home.org))[0].state == calls.CANCELLED

    async def test_i_took_it_after_the_claim_rings_nobody(
        self, home, dial, monkeypatch
    ):
        medicine_id = await _live(home, monkeypatch)
        med = await _medicine(medicine_id)
        dose_id = await calls._claim(med, _at(8))
        await medicines.mark_taken(home.org, home.amma.id, medicine_id, due_at=_at(8))
        await calls.place(dose_id)
        dial.assert_not_awaited()
        assert (await _doses(home.org))[0].state == calls.TAKEN
        assert await _alerts(home) == []


class TestTheCallingWindow:
    async def test_care_calls_are_exempt_from_the_calling_window(
        self, home, monkeypatch
    ):
        """Pinned on purpose. The person confirmed these exact times on a
        card, so the care path drops the 09:00-21:00 window (and keeps the
        do-not-call list). General reminder calls must NOT inherit this: the
        contract models quiet hours and an explicit, approved exception."""
        made = await medicines.propose(
            home.org,
            home.amma.id,
            label="Night tablet",
            times=["22:30"],
            phone="98765 43210",
            language="ta-IN",
        )
        from api.tests import care_support as cs

        await cs.press(home.org, made["event_id"], home.amma.id)
        monkeypatch.setattr(constants, "CARE_CALLS_FAKE", "")
        dialled = AsyncMock(return_value=555)
        with (
            patch.object(
                db_client,
                "get_default_telephony_configuration",
                new=AsyncMock(return_value=SimpleNamespace(id=1)),
            ),
            patch(
                "api.services.telephony.factory.get_telephony_provider_by_id",
                new=AsyncMock(return_value=SimpleNamespace(PROVIDER_NAME="fake")),
            ),
            patch("api.services.telephony.outbound.dial_workflow", new=dialled),
        ):
            assert await calls.tick(_at(22, 31)) == 1
        dialled.assert_awaited_once()


def _accepted_then(error: Exception, run_id: int = 999):
    """The real dial's shape: the run is recorded (``on_run_created``), the
    provider is asked, and the request then fails on our side."""

    async def dial(med, dose, *, on_run_created=None):
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
        "annotations": {},
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


def _runs(run):
    return patch.object(db_client, "get_workflow_run", new=AsyncMock(return_value=run))


def _later(**kw) -> datetime:
    return datetime.now(UTC) + timedelta(**kw)


class TestUnknownOutcomes:
    async def test_an_answer_that_arrives_after_the_sweep_is_kept(
        self, home, dial, monkeypatch
    ):
        await _live(home, monkeypatch)
        await calls.tick(_at(8, 1))
        dose_id = (await _doses(home.org))[0].id
        await calls.sweep(datetime.now(UTC) + timedelta(hours=1))
        await _report(home, _run(dose_id, "taken"))
        assert (await _doses(home.org))[0].state == calls.TAKEN

    async def test_silence_is_unknown_and_the_family_is_not_told_missed(
        self, home, dial, monkeypatch
    ):
        await _live(home, monkeypatch)
        await calls.tick(_at(8, 1))
        with _runs(_evidence()):
            await calls.sweep(_later(minutes=30))
            await calls.sweep(_later(minutes=60))
        dose = (await _doses(home.org))[0]
        assert dose.state == calls.UNKNOWN and dose.reason == "no_outcome"
        assert await _alerts(home) == []
        dial.assert_awaited_once()

    async def test_unknown_for_too_long_tells_the_family_could_not_confirm_once(
        self, home, dial, monkeypatch
    ):
        await _live(home, monkeypatch)
        await calls.tick(_at(8, 1))
        with _runs(_evidence()):
            await calls.sweep(_later(minutes=30))
            await calls.sweep(_later(minutes=calls.UNKNOWN_ALERT_MINUTES + 5))
            await calls.sweep(_later(minutes=calls.UNKNOWN_ALERT_MINUTES + 10))
        assert await _alerts(home) == [
            (
                "Decibyl could not confirm whether the 08:00 reminder call for BP "
                "tablet reached Amma."
            )
        ]
        # The carrier's no-answer, arriving later: recorded, no second alert.
        with _runs(_evidence(**NO_ANSWER)):
            await calls.sweep(_later(minutes=calls.UNKNOWN_ALERT_MINUTES + 15))
        dose = (await _doses(home.org))[0]
        assert dose.state == calls.NOT_ANSWERED
        assert len(await _alerts(home)) == 1
        dial.assert_awaited_once()

    async def test_answered_without_a_report_becomes_unclear_not_missed(
        self, home, dial, monkeypatch
    ):
        await _live(home, monkeypatch)
        await calls.tick(_at(8, 1))
        answered = _evidence(answered_at=datetime.now(UTC))
        with _runs(answered):
            await calls.sweep(_later(minutes=30))
            assert (await _doses(home.org))[0].reason == "answered_no_report"
            await calls.sweep(_later(minutes=calls.UNKNOWN_ALERT_MINUTES + 5))
        assert (await _doses(home.org))[0].state == calls.UNCLEAR
        assert await _alerts(home) == [
            "Amma answered the 08:00 call for BP tablet but did not say it was taken."
        ]

    async def test_a_provider_timeout_is_unknown_not_failed(self, home, monkeypatch):
        """Provider accepted, client timed out: the run was recorded before
        the provider was asked, so the call may have rung."""
        dial = AsyncMock(side_effect=_accepted_then(TimeoutError()))
        monkeypatch.setattr(calls, "_dial", dial)
        await _live(home, monkeypatch)
        await calls.tick(_at(8, 1))
        rows = await _doses(home.org)
        assert rows[0].state != calls.FAILED
        assert await _alerts(home) == []
        assert rows[0].state == calls.UNKNOWN and rows[0].workflow_run_id == 999
        # Never re-dialled: the next ticks see the dose's row and pass.
        await calls.tick(_at(8, 2))
        await calls.tick(_at(8, 5))
        dial.assert_awaited_once()

    async def test_an_error_before_any_run_is_a_verified_could_not_call(
        self, home, monkeypatch
    ):
        monkeypatch.setattr(calls, "_dial", AsyncMock(side_effect=RuntimeError("x")))
        await _live(home, monkeypatch)
        await calls.tick(_at(8, 1))
        dose = (await _doses(home.org))[0]
        assert dose.state == calls.FAILED and dose.reason == "call_error"
        assert await _alerts(home) == [
            (
                "Decibyl could not call Amma about BP tablet at 08:00: something "
                "went wrong on our side."
            )
        ]

    async def test_a_claim_that_never_dialled_is_not_reported_as_unanswered(
        self, home, dial, monkeypatch
    ):
        """Worker crash before run-id linkage. Nothing rang, so the dose is
        not "did not answer"; the family is told Decibyl could not call --
        which is true -- and not that the person missed it."""
        medicine_id = await _live(home, monkeypatch)
        med = await _medicine(medicine_id)
        await calls._claim(med, _at(8))  # the worker dies here
        await calls.sweep(datetime.now(UTC) + timedelta(hours=1))
        dose = (await _doses(home.org))[0]
        assert dose.state != calls.NOT_ANSWERED
        assert dose.state == calls.FAILED and dose.reason == "not_dialled"
        alerts = await _alerts(home)
        assert len(alerts) == 1 and "could not call Amma" in alerts[0]
        assert not any("did not answer" in a for a in alerts)
        dial.assert_not_awaited()

    async def test_a_delayed_and_duplicated_outcome_settles_once(
        self, home, dial, monkeypatch
    ):
        await _live(home, monkeypatch)
        await calls.tick(_at(8, 1))
        dose_id = (await _doses(home.org))[0].id
        await calls.sweep(_later(minutes=30))  # unknown, nobody told
        await _report(home, _run(dose_id, "not_yet"))
        await _report(home, _run(dose_id, "not_yet"))
        await _report(home, _run(dose_id, "taken"))  # contradicting, too late
        dose = (await _doses(home.org))[0]
        assert dose.state == calls.NOT_TAKEN
        assert len(await _alerts(home)) == 1
        assert [(e["from"], e["to"]) for e in dose.outcome_history] == [
            (calls.CALLING, calls.UNKNOWN),
            (calls.UNKNOWN, calls.NOT_TAKEN),
        ]

    async def test_taken_after_a_verified_no_answer_is_corrected_once(
        self, home, dial, monkeypatch
    ):
        await _live(home, monkeypatch)
        await calls.tick(_at(8, 1))
        dose_id = (await _doses(home.org))[0].id
        with _runs(_evidence(**NO_ANSWER)):
            await calls.sweep(_later(minutes=30))
        assert await _alerts(home) == [
            "Amma did not answer the 08:00 reminder call for BP tablet."
        ]
        await _report(home, _run(dose_id, "taken"))
        await _report(home, _run(dose_id, "taken"))
        alerts = await _alerts(home)
        assert len(alerts) == 2
        assert any(a.startswith("Update: Amma did answer") for a in alerts)
        assert (await _doses(home.org))[0].state == calls.TAKEN

    async def test_a_report_for_another_run_is_ignored(self, home, dial, monkeypatch):
        await _live(home, monkeypatch)
        await calls.tick(_at(8, 1))
        dose_id = (await _doses(home.org))[0].id
        with (
            _runs(_run(dose_id, "taken")),
            patch.object(
                db_client,
                "get_organization_id_by_workflow_run_id",
                new=AsyncMock(return_value=home.org),
            ),
        ):
            await calls.record_run_outcome(123456)  # the dose's run is 999
        assert (await _doses(home.org))[0].state == calls.CALLING

    @pytest.mark.xfail(
        strict=True,
        raises=AssertionError,
        reason="Gap: 'I took it' overwrites the dose's single state, so a missed "
        "call that was already reported becomes 'taken' and the call's own "
        "outcome is gone. The contract keeps delivery state and task state "
        "apart.",
    )
    async def test_i_took_it_after_a_missed_call_keeps_the_calls_outcome(
        self, home, dial, monkeypatch
    ):
        medicine_id = await _live(home, monkeypatch)
        await calls.tick(_at(8, 1))
        with _runs(_evidence(**NO_ANSWER)):
            await calls.sweep(datetime.now(UTC) + timedelta(hours=1))
        await medicines.mark_taken(home.org, home.amma.id, medicine_id, due_at=_at(8))
        assert (await _doses(home.org))[0].state == calls.NOT_ANSWERED
