"""Care's medicine calls and the shared daily cap (decision D3, default
chosen pending the founder: docs/plans/reminder-calls.md).

With ``reminder_calls`` on, a care call takes a slot of the person's one
daily allowance -- the same counter call-when-done and reminder calls use,
on the person's local day -- reserved right before the dial and given back
only on a verified non-dispatch. Past the cap the dose is ``failed``
(``daily_cap``) and the family is told Decibyl could not call. With
``reminder_calls`` off, care is exactly as before: uncapped. The calling
window exemption is unchanged either way.

No call is placed: ``calls._dial`` is replaced.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from sqlalchemy import text

from api import constants
from api.db import db_client
from api.db.care_models import CareMedicineModel
from api.services.call_when_done import allowance
from api.services.care import calls
from api.services.reminder_calls import policy
from api.tests import test_care_medicine_calls as base
from api.tests.test_care_medicine_calls import _active, _at, _doses

home = base.home

pytestmark = pytest.mark.asyncio


@pytest.fixture
def dial(monkeypatch):
    fake = AsyncMock(return_value=999)
    monkeypatch.setattr(calls, "_dial", fake)
    return fake


async def _live(h, monkeypatch) -> CareMedicineModel:
    medicine_id = await _active(h)
    monkeypatch.setattr(constants, "CARE_CALLS_FAKE", "")
    async with db_client.async_session() as session:
        return await session.get(CareMedicineModel, medicine_id)


async def _fill(med, used: int) -> None:
    day = allowance.local_day(med.timezone, calls._now())
    async with db_client.async_session() as session:
        await session.execute(
            text(
                "INSERT INTO person_call_allowances (user_id, local_day, used, updated_at) "
                "VALUES (:u, :d, :n, now()) ON CONFLICT (user_id, local_day) "
                "DO UPDATE SET used = :n"
            ),
            {"u": med.person_user_id, "d": day, "n": used},
        )
        await session.commit()


async def _forget(med) -> None:
    async with db_client.async_session() as session:
        await session.execute(
            text("DELETE FROM person_call_allowances WHERE user_id = :u"),
            {"u": med.person_user_id},
        )
        await session.commit()


async def test_with_reminder_calls_on_a_full_day_is_not_rung(home, dial, monkeypatch):
    monkeypatch.setattr(constants, "REMINDER_CALLS_ENABLED", True)
    med = await _live(home, monkeypatch)
    await _fill(med, constants.CALL_WHEN_DONE_DAILY_CAP)
    try:
        await calls.tick(_at(8, 1))
        dial.assert_not_awaited()
        dose = (await _doses(home.org))[0]
        assert dose.state == calls.FAILED and dose.reason == "daily_cap"
    finally:
        await _forget(med)


async def test_with_reminder_calls_on_a_care_call_holds_a_slot(home, dial, monkeypatch):
    monkeypatch.setattr(constants, "REMINDER_CALLS_ENABLED", True)
    med = await _live(home, monkeypatch)
    try:
        await calls.tick(_at(8, 1))
        dial.assert_awaited_once()
        assert await allowance.used(med.person_user_id, med.timezone, calls._now()) == 1
    finally:
        await _forget(med)


async def test_a_refused_dial_gives_the_slot_back(home, monkeypatch):
    monkeypatch.setattr(constants, "REMINDER_CALLS_ENABLED", True)
    med = await _live(home, monkeypatch)
    monkeypatch.setattr(
        calls,
        "_dial",
        AsyncMock(side_effect=calls.CallRefused("No line.", "needs_setup")),
    )
    try:
        await calls.tick(_at(8, 1))
        assert (await _doses(home.org))[0].reason == "needs_setup"
        assert await allowance.used(med.person_user_id, med.timezone, calls._now()) == 0
    finally:
        await _forget(med)


async def test_with_reminder_calls_off_care_is_uncapped(home, dial, monkeypatch):
    monkeypatch.setattr(constants, "REMINDER_CALLS_ENABLED", False)
    med = await _live(home, monkeypatch)
    await _fill(med, constants.CALL_WHEN_DONE_DAILY_CAP)
    try:
        await calls.tick(_at(8, 1))
        dial.assert_awaited_once()
        assert (await _doses(home.org))[0].state == calls.CALLING
    finally:
        await _forget(med)


async def test_the_switch_keeps_care_out_of_the_cap(home, dial, monkeypatch):
    monkeypatch.setattr(constants, "REMINDER_CALLS_ENABLED", True)
    monkeypatch.setattr(policy, "CARE_SHARES_THE_CAP", False)
    med = await _live(home, monkeypatch)
    await _fill(med, constants.CALL_WHEN_DONE_DAILY_CAP)
    try:
        await calls.tick(_at(8, 1))
        dial.assert_awaited_once()
    finally:
        await _forget(med)
