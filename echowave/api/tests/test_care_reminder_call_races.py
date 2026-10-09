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
* an answer that arrives after the sweep, a provider timeout, a crash between
  claim and dial, and "I took it" after a missed call all lose or misstate
  what happened (xfail: they need the separate delivery state and the
  reconcile-before-retry step the contract describes).

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
from api.tests.test_care_medicine_calls import _active, _at, _doses, home  # noqa: F401

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


class TestUnknownOutcomes:
    @pytest.mark.xfail(
        strict=True,
        raises=AssertionError,
        reason="Gap: the sweep settles a call with no report as not_answered and "
        "tells the family; a real answer arriving later cannot move it. The "
        "contract keeps delivery 'unknown' until reconciled.",
    )
    async def test_an_answer_that_arrives_after_the_sweep_is_kept(
        self, home, dial, monkeypatch
    ):
        await _live(home, monkeypatch)
        await calls.tick(_at(8, 1))
        dose_id = (await _doses(home.org))[0].id
        await calls.sweep(datetime.now(UTC) + timedelta(hours=1))
        await _report(home, _run(dose_id, "taken"))
        assert (await _doses(home.org))[0].state == calls.TAKEN

    @pytest.mark.xfail(
        strict=True,
        raises=AssertionError,
        reason="Gap: a provider timeout during the dial is recorded as failed "
        "('could not call') although the carrier may have placed the call; "
        "the contract records 'unknown' and reconciles before alerting or "
        "retrying.",
    )
    async def test_a_provider_timeout_is_unknown_not_failed(self, home, monkeypatch):
        monkeypatch.setattr(
            calls, "_dial", AsyncMock(side_effect=asyncio.TimeoutError())
        )
        await _live(home, monkeypatch)
        await calls.tick(_at(8, 1))
        rows = await _doses(home.org)
        assert rows[0].state != calls.FAILED
        assert await _alerts(home) == []

    @pytest.mark.xfail(
        strict=True,
        raises=AssertionError,
        reason="Gap: a dose claimed by a tick that died before dialling is swept "
        "as 'did not answer' and the family is told, though nothing rang. "
        "The contract reserves a dispatch record and only a dispatched call "
        "can be 'not answered'.",
    )
    async def test_a_claim_that_never_dialled_is_not_reported_as_unanswered(
        self, home, dial, monkeypatch
    ):
        medicine_id = await _live(home, monkeypatch)
        med = await _medicine(medicine_id)
        await calls._claim(med, _at(8))  # the worker dies here
        await calls.sweep(datetime.now(UTC) + timedelta(hours=1))
        assert (await _doses(home.org))[0].state != calls.NOT_ANSWERED
        assert await _alerts(home) == []

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
        await calls.sweep(datetime.now(UTC) + timedelta(hours=1))
        await medicines.mark_taken(home.org, home.amma.id, medicine_id, due_at=_at(8))
        assert (await _doses(home.org))[0].state == calls.NOT_ANSWERED
