"""Reminder calls, Stage 2 (services/reminder_calls; docs/plans/reminder-calls.md).

Done when: what the person said becomes an exact draft in their own zone,
with nothing moved silently out of calling hours; the number card needs "I
am 18 or over"; a confirmed card saves a schedule at the card's version;
the tick makes one occurrence per due time however many ticks run, and the
gate refuses -- in order, with the reason stored -- a cancelled or
superseded reminder, a removed member, no line, an unconfirmed or
non-adult number, quiet hours (even with do-not-call enforcement off), a
listed number and a full day (one cap shared with call-when-done and care);
a provider timeout after the run is recorded is ``unknown`` and never
re-dialled; a claim that never dialled is never "no answer"; duplicate and
late reports settle once and late truth wins; no answer gets one retry 15
minutes later inside the window, counted against the cap, then a
notification; nobody but the person hears the reminder; and with the flag
off nothing happens.

No call is placed: ``calls._dial`` is replaced (it records the run the way
``dial_workflow`` does, then returns it) and the line is a stand-in row.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import text

from api import constants
from api.db import db_client
from api.enums import AgentEventKind
from api.services import member_preferences
from api.services import reminder_calls as rc
from api.services.call_when_done import allowance
from api.services.compliance import dnd
from api.services.identity import notifications
from api.services.reminder_calls import (
    ReminderCallError,
    agent,
    calls,
    cards,
    draft,
    gate,
    number,
    policy,
    schedule,
    tools,
)
from api.services.telephony import call_evidence
from api.services.workflow import actions
from api.tests import care_support as cs

pytestmark = pytest.mark.asyncio

IST = ZoneInfo("Asia/Kolkata")
PHONE = "+919876543210"
RUN = 4242


def _ist(hour: int, minute: int = 0, *, days: int = 0) -> datetime:
    """Monday 12 October 2026 in Kolkata, plus ``days``."""
    local = datetime(2026, 10, 12, hour, minute, tzinfo=IST) + timedelta(days=days)
    return local.astimezone(UTC)


@pytest.fixture
async def home(test_engine, monkeypatch):
    monkeypatch.setattr(constants, "REMINDER_CALLS_ENABLED", True)
    monkeypatch.setattr(constants, "TASK_LEDGER_ENABLED", True)
    asha = await cs.person("rc-asha")
    bob = await cs.person("rc-bob")
    org = await cs.workspace(asha.id)
    other_org = await cs.workspace(bob.id)
    await db_client.add_user_to_organization(asha.id, org)
    await db_client.add_user_to_organization(bob.id, other_org)
    line = AsyncMock(return_value=SimpleNamespace(id=7))
    monkeypatch.setattr(db_client, "get_default_telephony_configuration", line)
    zone = {"name": "Asia/Kolkata"}

    async def timezone_of(user_id):
        return zone["name"]

    monkeypatch.setattr(member_preferences, "timezone_of", timezone_of)
    dial_fails = {"before_run": None, "after_run": None}

    async def fake_dial(dispatch_id, dialable, *, on_run_created=None):
        if dial_fails["before_run"]:
            raise dial_fails["before_run"]
        if on_run_created is not None:
            await on_run_created(RUN)
        if dial_fails["after_run"]:
            raise dial_fails["after_run"]
        return RUN

    dial = AsyncMock(side_effect=fake_dial)
    monkeypatch.setattr(calls, "_dial", dial)
    told = AsyncMock(return_value={"push": "sent"})
    monkeypatch.setattr(notifications, "notify", told)
    clock = {"now": _ist(10, 0)}
    monkeypatch.setattr(calls, "_now", lambda: clock["now"])
    monkeypatch.setattr(cards, "_now", lambda: clock["now"])
    monkeypatch.setattr(schedule, "_now", lambda: clock["now"])
    yield SimpleNamespace(
        asha=asha,
        bob=bob,
        org=org,
        other_org=other_org,
        line=line,
        dial=dial,
        dial_fails=dial_fails,
        told=told,
        clock=clock,
        zone=zone,
    )
    async with db_client.async_session() as session:
        for o in (org, other_org):
            for table in (
                "reminder_call_dispatches",
                "reminder_call_occurrences",
                "reminder_call_schedules",
                "reminder_call_numbers",
                "agent_events",
            ):
                await session.execute(
                    text(f"DELETE FROM {table} WHERE organization_id = :o"), {"o": o}
                )
        for who in (asha, bob):
            await session.execute(
                text("DELETE FROM person_call_allowances WHERE user_id = :u"),
                {"u": who.id},
            )
        await session.commit()


# --- helpers -------------------------------------------------------------------


async def _press(h, event_id: int, *, attested: bool = False, verb: str = "confirm"):
    event = await db_client.get_agent_event(event_id, organization_id=h.org)
    version = (event.payload or {}).get("version")
    with patch("api.tasks.arq.enqueue_job", new=AsyncMock()):
        await actions.settle(
            organization_id=h.org,
            event_id=event_id,
            verb=verb,
            user_id=h.asha.id,
            version=version,
            attested=attested,
        )
    if verb == "confirm":
        await actions.run(event_id, h.org)
    event = await db_client.get_agent_event(event_id, organization_id=h.org)
    return event.payload or {}


async def _confirm_number(h, phone: str = PHONE, *, then=None) -> int:
    event_id = await number.propose(h.org, h.asha.id, phone, thread_id="t1", then=then)
    payload = await _press(h, event_id, attested=True)
    assert payload["state"] == "done", payload
    return event_id


async def _cards(h, action: str):
    async with db_client.async_session() as session:
        rows = (
            await session.execute(
                text(
                    "SELECT id, payload FROM agent_events WHERE organization_id = :o "
                    "AND kind = :k ORDER BY id"
                ),
                {"o": h.org, "k": AgentEventKind.ACTION_PROPOSED.value},
            )
        ).all()
    return [(i, p) for i, p in rows if (p or {}).get("action") == action]


async def _set(h, *, title="Send the proposal", date="tomorrow", time="10:30", **more):
    """Ask, confirm the number (with the reminder riding on it), confirm the
    reminder: what a person does on the thread."""
    cleaned = await draft.clean(
        h.org,
        h.asha.id,
        {"title": title, "date": date, "time": time, **more},
        now=h.clock["now"],
    )
    found = await number.ready(h.org, h.asha.id)
    if found is None:
        await _confirm_number(h, then=cleaned)
        event_id = (await _cards(h, actions.REMINDER_CALL))[-1][0]
    else:
        told = await cards.propose(
            h.org, h.asha.id, {**cleaned, "phone": found}, thread_id="t1"
        )
        event_id = told["event_id"]
    payload = await _press(h, event_id)
    assert payload["state"] == "done", payload
    return (await _rows("reminder_call_schedules", h.org))[-1]


async def _rows(table: str, org: int):
    async with db_client.async_session() as session:
        return (
            (
                await session.execute(
                    text(
                        f"SELECT * FROM {table} WHERE organization_id = :o ORDER BY id"
                    ),
                    {"o": org},
                )
            )
            .mappings()
            .all()
        )


async def _dispatches(h):
    return await _rows("reminder_call_dispatches", h.org)


async def _notices(org: int) -> list[str]:
    async with db_client.async_session() as session:
        rows = (
            await session.execute(
                text(
                    "SELECT payload FROM agent_events WHERE organization_id = :o "
                    "AND kind = :k ORDER BY id"
                ),
                {"o": org, "k": AgentEventKind.MESSAGE.value},
            )
        ).all()
    return [
        (p or {}).get("body", "") for (p,) in rows if (p or {}).get("reminder_call")
    ]


def _run(
    dispatch_id: int,
    *,
    answered: bool = True,
    reached: str | None = "person",
    reply: str | None = None,
    snooze: str | None = None,
):
    extracted = {}
    if reached:
        extracted["reached"] = reached
    if reply:
        extracted["reminder_reply"] = reply
    if snooze:
        extracted["snooze_minutes"] = snooze
    return SimpleNamespace(
        initial_context={"reminder_dispatch_id": dispatch_id},
        answered_at=object() if answered else None,
        billable_seconds=30 if answered else 0,
        gathered_context={"extracted_variables": extracted},
    )


async def _report(h, run) -> None:
    with (
        patch.object(db_client, "get_workflow_run", new=AsyncMock(return_value=run)),
        patch.object(
            db_client,
            "get_organization_id_by_workflow_run_id",
            new=AsyncMock(return_value=h.org),
        ),
    ):
        await calls.record_run_outcome(RUN)


def _evidence(monkeypatch, evidence: str) -> None:
    monkeypatch.setattr(call_evidence, "read", AsyncMock(return_value=(evidence, None)))


async def _ring(h, at: datetime | None = None):
    """The first ring of a fresh reminder for tomorrow 10:30: set, then the
    tick at its time. Returns the dispatch row."""
    await _set(h)
    await calls.tick(at or _ist(10, 30, days=1))
    return (await _dispatches(h))[-1]


async def _fill_day(h, used: int, day=None) -> None:
    day = day or _ist(10, 30, days=1).astimezone(IST).date()
    async with db_client.async_session() as session:
        await session.execute(
            text(
                "INSERT INTO person_call_allowances (user_id, local_day, used, updated_at) "
                "VALUES (:u, :d, :n, now()) ON CONFLICT (user_id, local_day) "
                "DO UPDATE SET used = :n"
            ),
            {"u": h.asha.id, "d": day, "n": used},
        )
        await session.commit()


# --- the draft -------------------------------------------------------------------


class TestTheDraft:
    async def test_tomorrow_at_ten_to_midnight_is_the_next_day(self, home):
        made = await draft.clean(
            home.org,
            home.asha.id,
            {"title": "Send the proposal", "date": "tomorrow", "time": "10:30"},
            now=_ist(23, 50),
        )
        assert made["first_due_at"] == _ist(10, 30, days=1).isoformat()
        assert made["asked_time"] is None

    async def test_tomorrow_just_after_midnight_is_the_calendar_day_after(self, home):
        """At 00:10 "tomorrow" is the person's calendar tomorrow; the card shows
        the full date, so the reading is never hidden."""
        made = await draft.clean(
            home.org,
            home.asha.id,
            {"title": "Send the proposal", "date": "tomorrow", "time": "10:30"},
            now=_ist(0, 10, days=1),
        )
        assert made["first_due_at"] == _ist(10, 30, days=2).isoformat()
        label, _ = cards.summary({**made, "phone": PHONE})
        assert label.startswith("Wed 14 Oct 2026, 10:30 IST")

    async def test_a_person_in_dubai_is_rung_at_their_time(self, home):
        home.zone["name"] = "Asia/Dubai"
        made = await draft.clean(
            home.org,
            home.asha.id,
            {"title": "Call the bank", "date": "2026-10-13", "time": "10:30"},
            now=_ist(10, 0),
        )
        assert made["timezone"] == "Asia/Dubai"
        assert datetime.fromisoformat(made["first_due_at"]) == datetime(
            2026, 10, 13, 6, 30, tzinfo=UTC
        )

    async def test_no_timezone_on_file_asks_which_city(self, home, monkeypatch):
        home.zone["name"] = None
        from api.services.call_when_done import calls as done_calls

        monkeypatch.setattr(done_calls, "org_timezone", AsyncMock(return_value=None))
        with pytest.raises(ReminderCallError, match="Which city"):
            await draft.clean(
                home.org, home.asha.id, {"title": "x", "time": "10:30"}, now=_ist(10)
            )

    async def test_next_friday_said_on_a_friday_is_a_week_away(self, home):
        friday = _ist(10, 0, days=4)  # Friday 16 October
        assert friday.astimezone(IST).weekday() == 4
        later = await draft.clean(
            home.org,
            home.asha.id,
            {"title": "x", "date": "next friday", "time": "11:00"},
            now=friday,
        )
        assert later["date"] == "2026-10-23"
        same = await draft.clean(
            home.org,
            home.asha.id,
            {"title": "x", "date": "friday", "time": "11:00"},
            now=friday,
        )
        assert same["date"] == "2026-10-16"

    async def test_half_past_nine_at_night_is_offered_as_nine_in_the_morning(
        self, home
    ):
        made = await draft.clean(
            home.org,
            home.asha.id,
            {"title": "Water the plants", "date": "today", "time": "21:30"},
            now=_ist(10, 0),
        )
        assert made["asked_time"] == "21:30"
        assert made["local_time"] == "09:00"
        assert made["first_due_at"] == _ist(9, 0, days=1).isoformat()
        shown = cards.resolve(
            {**made, "phone": PHONE, "person_user_id": home.asha.id}, ""
        )
        assert "You asked for 21:30, which is outside calling hours" in shown["why"]
        assert "9:00 and 21:00" in shown["why"]

    async def test_a_time_already_past_is_refused(self, home):
        with pytest.raises(ReminderCallError, match="already passed"):
            await draft.clean(
                home.org,
                home.asha.id,
                {"title": "x", "date": "today", "time": "09:30"},
                now=_ist(10, 0),
            )

    async def test_times_as_people_type_them(self):
        from datetime import time

        assert draft.parse_time("08:30") == time(8, 30)
        assert draft.parse_time("8.30") == time(8, 30)
        assert draft.parse_time("0830") == time(8, 30)
        assert draft.parse_time("8 pm") == time(20, 0)
        assert draft.parse_time("12 am") == time(0, 0)
        assert draft.parse_time("8") is None
        assert draft.parse_time("25:00") is None
        assert draft.parse_time("13 pm") is None

    async def test_in_two_hours(self, home):
        made = await draft.clean(
            home.org, home.asha.id, {"title": "x", "in_minutes": 120}, now=_ist(10, 0)
        )
        assert made["first_due_at"] == _ist(12, 0).isoformat()

    async def test_the_card_reads_back_everything_that_will_ring(self, home):
        made = await draft.clean(
            home.org,
            home.asha.id,
            {"title": "Send the proposal", "time": "10:30", "date": "tomorrow",
             "language": "ta"},
            now=_ist(10, 0),
        )  # fmt: skip
        label, effect = cards.summary({**made, "phone": PHONE})
        assert (
            label
            == "Tue 13 Oct 2026, 10:30 IST (Asia/Kolkata) · +91 98••••3210 · Tamil"
        )
        assert "“Send the proposal” · once" in effect
        assert "if no answer: one retry at 10:45, then a notification" in effect


# --- the number card (D4) ------------------------------------------------------------


class TestTheNumberCard:
    async def test_confirm_needs_i_am_eighteen_or_over(self, home):
        event_id = await number.propose(home.org, home.asha.id, PHONE, thread_id="t1")
        payload = (
            await db_client.get_agent_event(event_id, organization_id=home.org)
        ).payload
        assert payload["attestation"] == "I am 18 or over"
        with pytest.raises(actions.ActionError, match="I am 18 or over"):
            await _press(home, event_id, attested=False)
        assert await number.ready(home.org, home.asha.id) is None
        await _press(home, event_id, attested=True)
        assert await number.ready(home.org, home.asha.id) == PHONE
        row = (await _rows("reminder_call_numbers", home.org))[0]
        assert row["adult_confirmed_at"] is not None

    async def test_confirm_all_cannot_tick_it_for_the_person(self, home):
        event_id = await number.propose(home.org, home.asha.id, PHONE, thread_id="t1")
        event = await db_client.get_agent_event(event_id, organization_id=home.org)
        with patch("api.tasks.arq.enqueue_job", new=AsyncMock()):
            results = await actions.settle_many(
                organization_id=home.org,
                items=[{"event_id": event_id, "version": event.payload["version"]}],
                user_id=home.asha.id,
            )
        assert results[0]["ok"] is False

    async def test_confirming_the_number_puts_the_reminder_card_on_the_thread(
        self, home
    ):
        cleaned = await draft.clean(
            home.org,
            home.asha.id,
            {"title": "Send the proposal", "date": "tomorrow", "time": "10:30"},
            now=home.clock["now"],
        )
        await _confirm_number(home, then=cleaned)
        reminder_cards = await _cards(home, actions.REMINDER_CALL)
        assert len(reminder_cards) == 1
        assert reminder_cards[0][1]["args"]["title"] == "Send the proposal"
        # Not saved until that card is confirmed too.
        assert await _rows("reminder_call_schedules", home.org) == []

    async def test_no_line_offers_an_app_reminder_and_schedules_nothing(self, home):
        home.line.return_value = None
        told = await tools.ask(
            home.org,
            home.asha.id,
            {"title": "x", "date": "tomorrow", "time": "10:30", "phone_number": PHONE},
            thread_id="t1",
            now=home.clock["now"],
        )
        assert told["status"] == "no_line"
        assert "app reminder" in told["note"]
        assert await _cards(home, actions.REMINDER_CALL_NUMBER) == []


# --- saving and the tick --------------------------------------------------------------


class TestScheduleAndTick:
    async def test_a_confirmed_card_is_a_schedule_at_the_cards_version(self, home):
        saved = await _set(home)
        event_id = (await _cards(home, actions.REMINDER_CALL))[-1][0]
        event = await db_client.get_agent_event(event_id, organization_id=home.org)
        assert saved["version"] == event.payload["confirmed"]["version"]
        assert saved["next_due_at"] == _ist(10, 30, days=1)
        assert saved["retry_policy"] == {"max_retries": 1, "gap_minutes": 15}

    async def test_the_tick_rings_once_at_the_time(self, home):
        await _set(home)
        assert await calls.tick(_ist(10, 29, days=1)) == 0
        assert await calls.tick(_ist(10, 30, days=1)) == 1
        home.dial.assert_awaited_once()
        row = (await _dispatches(home))[0]
        assert row["state"] == rc.ACCEPTED and row["workflow_run_id"] == RUN
        assert row["allowance_day"] == _ist(10, 30, days=1).astimezone(IST).date()

    async def test_two_ticks_at_once_ring_once(self, home):
        await _set(home)
        due = _ist(10, 30, days=1)
        handled = await asyncio.gather(calls.tick(due), calls.tick(due))
        assert sum(handled) == 1
        home.dial.assert_awaited_once()
        assert len(await _rows("reminder_call_occurrences", home.org)) == 1

    async def test_a_daily_reminder_makes_one_occurrence_a_day(self, home):
        await _set(home, recurrence="daily")
        await calls.tick(_ist(10, 30, days=1))
        await calls.tick(_ist(10, 30, days=2))
        occurrences = await _rows("reminder_call_occurrences", home.org)
        assert [o["due_at"] for o in occurrences] == [
            _ist(10, 30, days=1),
            _ist(10, 30, days=2),
        ]
        assert home.dial.await_count == 2

    async def test_a_tick_that_was_down_sends_a_notification_not_a_late_call(
        self, home
    ):
        await _set(home)
        await calls.tick(_ist(11, 30, days=1))
        home.dial.assert_not_awaited()
        row = (await _dispatches(home))[0]
        assert row["state"] == rc.SKIPPED and row["reason"] == "late"
        home.told.assert_awaited_once()


# --- the gate, one check at a time ----------------------------------------------------


class TestTheGate:
    async def _skipped(self, h, at=None) -> dict:
        await calls.tick(at or _ist(10, 30, days=1))
        h.dial.assert_not_awaited()
        row = (await _dispatches(h))[-1]
        assert row["state"] == rc.SKIPPED
        return row

    async def test_a_cancelled_reminder_is_not_rung_or_announced(self, home):
        saved = await _set(home)
        await calls._materialise(_ist(10, 30, days=1))
        await schedule.cancel(home.org, home.asha.id, saved["id"])
        row = await self._skipped(home)
        assert row["reason"] == "cancelled"
        home.told.assert_not_awaited()

    async def test_an_occurrence_of_an_older_version_is_superseded(self, home):
        await _set(home)
        await calls._materialise(_ist(10, 30, days=1))
        async with db_client.async_session() as session:
            await session.execute(
                text(
                    "UPDATE reminder_call_schedules SET version = 'newer' WHERE organization_id = :o"
                ),
                {"o": home.org},
            )
            await session.commit()
        assert (await self._skipped(home))["reason"] == "superseded"

    async def test_a_person_removed_from_the_workspace_is_not_rung(self, home):
        await _set(home)
        await db_client.remove_user_from_organization(home.asha.id, home.org)
        assert (await self._skipped(home))["reason"] == "not_member"
        home.told.assert_not_awaited()

    async def test_no_line_at_the_time(self, home):
        await _set(home)
        home.line.return_value = None
        assert (await self._skipped(home))["reason"] == "no_line"
        home.told.assert_awaited_once()

    async def test_a_number_taken_back_is_not_rung(self, home):
        await _set(home)
        number_card = (await _cards(home, actions.REMINDER_CALL_NUMBER))[0][0]
        await _press(home, number_card, verb="undo")
        assert (await self._skipped(home))["reason"] == "no_number"

    async def test_a_number_without_the_adult_confirmation_is_not_rung(self, home):
        await _set(home)
        async with db_client.async_session() as session:
            await session.execute(
                text(
                    "UPDATE reminder_call_numbers SET adult_confirmed_at = NULL WHERE organization_id = :o"
                ),
                {"o": home.org},
            )
            await session.commit()
        assert (await self._skipped(home))["reason"] == "not_adult"

    async def test_quiet_hours_hold_even_with_do_not_call_enforcement_off(
        self, home, monkeypatch
    ):
        monkeypatch.setattr(dnd, "DND_ENFORCEMENT_ENABLED", False)
        await _set(home)
        async with db_client.async_session() as session:
            await session.execute(
                text(
                    "UPDATE reminder_call_schedules SET next_due_at = :d WHERE organization_id = :o"
                ),
                {"o": home.org, "d": _ist(21, 5, days=1)},
            )
            await session.commit()
        row = await self._skipped(home, _ist(21, 5, days=1))
        assert row["reason"] == "quiet_hours"
        assert row["allowance_day"] is None
        assert "outside calling hours" in (await _notices(home.org))[-1]

    async def test_a_listed_number_is_never_rung(self, home, monkeypatch):
        await _set(home)
        monkeypatch.setattr(
            db_client, "is_number_dnd_listed", AsyncMock(return_value=True)
        )
        assert (await self._skipped(home))["reason"] == "dnd"

    async def test_a_full_day_is_not_rung_and_holds_no_slot(self, home):
        await _set(home)
        await _fill_day(home, 5)
        row = await self._skipped(home)
        assert row["reason"] == "cap" and row["allowance_day"] is None
        assert (
            "the day's call limit is reached (5 calls a day)"
            in (await _notices(home.org))[-1]
        )

    async def test_the_checks_are_asked_in_order(self, home):
        """Removed and no line at once: the first check answers."""
        await _set(home)
        await db_client.remove_user_from_organization(home.asha.id, home.org)
        home.line.return_value = None
        assert (await self._skipped(home))["reason"] == "not_member"
        assert gate.ORDER.index("not_member") < gate.ORDER.index("no_line")


# --- the shared cap (D3) --------------------------------------------------------------


class TestTheSharedCap:
    async def test_call_when_done_calls_count_toward_the_same_five(self, home):
        """One counter per person per local day, whichever path rings."""
        await _set(home)
        day = _ist(10, 30, days=1)
        await _fill_day(home, 4)
        await calls.tick(day)
        home.dial.assert_awaited_once()
        assert await allowance.used(home.asha.id, "Asia/Kolkata", day) == 5
        assert await allowance.remaining(home.asha.id, "Asia/Kolkata", day) == 0
        # Care's share of the same counter: tests/test_care_shared_cap.py.


# --- what came of the call --------------------------------------------------------------


class TestOutcomes:
    async def test_answered_and_done(self, home):
        row = await _ring(home)
        await _report(home, _run(row["id"], reply="done"))
        row = (await _dispatches(home))[0]
        assert row["state"] == rc.ANSWERED and row["reason"] is None
        occurrence = (await _rows("reminder_call_occurrences", home.org))[0]
        assert occurrence["task_state"] == rc.USER_REPORTED_DONE
        assert (await _notices(home.org))[-1] == (
            "I called you with your reminder at 10:30. You said it's done."
        )
        home.told.assert_not_awaited()

    async def test_a_report_delivered_twice_changes_nothing_the_second_time(self, home):
        row = await _ring(home)
        await _report(home, _run(row["id"], reply="done"))
        before = await _notices(home.org)
        await _report(home, _run(row["id"], reply="done"))
        assert await _notices(home.org) == before
        assert len((await _dispatches(home))[0]["outcome_history"]) == 2

    async def test_a_provider_timeout_after_the_run_is_unknown_not_failed(
        self, home, monkeypatch
    ):
        home.dial_fails["after_run"] = TimeoutError("provider timed out")
        _evidence(monkeypatch, call_evidence.PENDING)
        row = await _ring(home)
        assert row["state"] == rc.UNKNOWN and row["workflow_run_id"] == RUN
        # Never re-dialled, never released: it may have rung.
        await calls.tick(_ist(10, 31, days=1))
        await calls.sweep(_ist(11, 0, days=1))
        assert home.dial.await_count == 1
        assert row["allowance_day"] is not None
        assert "can't confirm the call reached you" in (await _notices(home.org))[-1]
        # Late truth wins: the post-call report says they answered.
        await _report(home, _run(row["id"], reply="done"))
        settled = (await _dispatches(home))[0]
        assert settled["state"] == rc.ANSWERED
        assert [e["to"] for e in settled["outcome_history"]] == [
            rc.UNKNOWN,
            rc.ANSWERED,
        ]

    async def test_a_5xx_before_the_run_is_a_verified_non_dispatch(self, home):
        home.dial_fails["before_run"] = RuntimeError("503 from the provider")
        row = await _ring(home)
        assert row["state"] == rc.SKIPPED and row["reason"] == "call_error"
        assert row["allowance_day"] is None
        assert (
            await allowance.used(home.asha.id, "Asia/Kolkata", _ist(10, 30, days=1))
            == 0
        )

    async def test_a_5xx_after_the_run_is_unknown(self, home, monkeypatch):
        from fastapi import HTTPException

        home.dial_fails["after_run"] = HTTPException(status_code=503, detail="busy")
        _evidence(monkeypatch, call_evidence.PENDING)
        row = await _ring(home)
        assert row["state"] == rc.UNKNOWN and row["allowance_day"] is not None

    async def test_a_4xx_from_the_provider_is_failed_and_holds_no_slot(self, home):
        from fastapi import HTTPException

        home.dial_fails["after_run"] = HTTPException(
            status_code=400, detail="invalid destination number"
        )
        row = await _ring(home)
        assert row["state"] == rc.FAILED and row["reason"] == "rejected"
        assert row["allowance_day"] is None
        assert len(await _dispatches(home)) == 1  # no retry from failed
        assert "would not place a call to that number" in (await _notices(home.org))[-1]

    async def test_an_invalid_number_at_the_carrier_is_failed(self, home, monkeypatch):
        row = await _ring(home)
        _evidence(monkeypatch, call_evidence.CARRIER_FAILED)
        await calls.sweep(_ist(10, 55, days=1))
        row = (await _dispatches(home))[0]
        assert row["state"] == rc.FAILED and row["reason"] == "not_connected"
        assert len(await _dispatches(home)) == 1  # no retry from failed
        home.told.assert_awaited_once()

    async def test_a_report_before_the_run_id_is_written_is_kept(self, home):
        await _set(home)
        await calls._materialise(_ist(10, 30, days=1))
        dispatch_id = (await _dispatches(home))[0]["id"]
        assert await calls._claim(dispatch_id, _ist(10, 30, days=1))
        await _report(home, _run(dispatch_id, reply="done"))
        row = (await _dispatches(home))[0]
        assert row["state"] == rc.ANSWERED and row["workflow_run_id"] == RUN

    async def test_a_claim_that_never_dialled_is_not_unanswered(self, home):
        await _set(home)
        await calls._materialise(_ist(10, 30, days=1))
        dispatch_id = (await _dispatches(home))[0]["id"]
        assert await calls._claim(dispatch_id, _ist(10, 30, days=1))
        # The worker died here. The sweep finds no run: never requested.
        await calls.sweep(_ist(11, 0, days=1))
        row = (await _dispatches(home))[0]
        assert row["state"] == rc.SKIPPED and row["reason"] == "not_dialled"
        home.dial.assert_not_awaited()

    async def test_no_report_by_the_sweep_is_unknown_and_a_late_answer_lands(
        self, home, monkeypatch
    ):
        row = await _ring(home)
        _evidence(monkeypatch, call_evidence.PENDING)
        await calls.sweep(_ist(10, 55, days=1))
        assert (await _dispatches(home))[0]["state"] == rc.UNKNOWN
        await _report(home, _run(row["id"], reply="done"))
        assert (await _dispatches(home))[0]["state"] == rc.ANSWERED
        assert len(await _dispatches(home)) == 1  # never retried from unknown

    async def test_snooze_rings_again_later(self, home):
        row = await _ring(home)
        await _report(home, _run(row["id"], reply="snooze", snooze="15"))
        occurrences = await _rows("reminder_call_occurrences", home.org)
        assert occurrences[0]["task_state"] == rc.SNOOZED
        assert occurrences[1]["due_at"] == _ist(10, 45, days=1)
        assert "call again at 10:45" in (await _notices(home.org))[-1]
        await calls.tick(_ist(10, 45, days=1))
        assert home.dial.await_count == 2


# --- no answer: the one retry (D1) --------------------------------------------------------


class TestRetry:
    async def test_one_retry_fifteen_minutes_later_then_a_notification(self, home):
        row = await _ring(home)
        await _report(home, _run(row["id"], answered=False, reached=None))
        rows = await _dispatches(home)
        assert rows[0]["state"] == rc.NO_ANSWER
        assert rows[1]["attempt"] == 2 and rows[1]["state"] == rc.QUEUED
        assert rows[1]["due_at"] == _ist(10, 45, days=1)
        assert "I'll try once more at 10:45" in (await _notices(home.org))[-1]
        home.told.assert_not_awaited()

        await calls.tick(_ist(10, 45, days=1))
        assert home.dial.await_count == 2
        # The retry is a ring: it holds its own slot of the day.
        assert (
            await allowance.used(home.asha.id, "Asia/Kolkata", _ist(11, 0, days=1)) == 2
        )
        second = (await _dispatches(home))[1]
        await _report(home, _run(second["id"], answered=False, reached=None))
        assert len(await _dispatches(home)) == 2  # no third ring
        home.told.assert_awaited_once()
        assert home.told.await_args.kwargs["dedupe_key"] == (
            f"reminder-call:{second['occurrence_id']}"
        )
        assert "I called twice" in (await _notices(home.org))[-1]

    async def test_no_retry_outside_the_window(self, home):
        await _set(home, time="20:50")
        await calls.tick(_ist(20, 50, days=1))
        row = (await _dispatches(home))[0]
        await _report(home, _run(row["id"], answered=False, reached=None))
        assert len(await _dispatches(home)) == 1
        home.told.assert_awaited_once()

    async def test_a_late_answer_cancels_the_retry_and_corrects_the_thread(self, home):
        row = await _ring(home)
        with patch.object(
            call_evidence,
            "read",
            new=AsyncMock(return_value=(call_evidence.NOT_CONNECTED, None)),
        ):
            await calls.sweep(_ist(10, 55, days=1))
        assert (await _dispatches(home))[1]["state"] == rc.QUEUED
        await _report(home, _run(row["id"], reply="done"))
        rows = await _dispatches(home)
        assert rows[0]["state"] == rc.ANSWERED
        assert (
            rows[1]["state"] == rc.SKIPPED and rows[1]["reason"] == "answered_earlier"
        )
        assert any(n.startswith("Correction:") for n in await _notices(home.org))

    async def test_no_retry_when_retries_are_off(self, home, monkeypatch):
        monkeypatch.setattr(policy, "MAX_RETRIES", 0)
        row = await _ring(home)
        await _report(home, _run(row["id"], answered=False, reached=None))
        assert len(await _dispatches(home)) == 1
        home.told.assert_awaited_once()


# --- privacy (D6, D7) -----------------------------------------------------------------------


class TestPrivacy:
    async def test_only_the_read_out_node_holds_the_reminder(self):
        nodes = {n["id"]: n for n in agent.definition()["nodes"]}
        with_text = [
            node_id
            for node_id, node in nodes.items()
            if "{{reminder_text}}" in str(node["data"])
        ]
        assert with_text == ["agent-1"]
        assert "{{reminder_text}}" not in str(agent.definition()["edges"])

    async def test_the_greeting_asks_who_it_is_and_says_nothing_else(self):
        context = agent.call_context(
            title="Pay the hospital bill", language="en", name="Asha Rao"
        )
        assert context["reminder_greeting"] == (
            "Hello, this is Decibyl with the reminder you asked for. Am I "
            "speaking with Asha?"
        )
        for lang in agent.GREETINGS:
            assert "Pay the hospital bill" not in agent.greeting(lang, name="Asha")

    async def test_somebody_else_and_voicemail_hear_the_content_free_line(self):
        not_them = {n["id"]: n for n in agent.definition()["nodes"]}["agent-2"]
        prompt = not_them["data"]["prompt"]
        assert "This is Decibyl with a message for {{reminder_first_name}}" in prompt
        assert "voicemail" in prompt

    async def test_someone_else_answering_leaves_the_task_open(self, home):
        row = await _ring(home)
        await _report(home, _run(row["id"], reached="someone_else"))
        row = (await _dispatches(home))[0]
        assert row["state"] == rc.ANSWERED and row["reason"] == "someone_else"
        occurrence = (await _rows("reminder_call_occurrences", home.org))[0]
        assert occurrence["task_state"] == rc.OPEN
        home.told.assert_awaited_once()
        line = (await _notices(home.org))[-1]
        assert "Someone else answered" in line and "only asked them" in line

    async def test_voicemail_is_not_reached_and_gets_the_retry(self, home):
        row = await _ring(home)
        await _report(home, _run(row["id"], reached="voicemail"))
        rows = await _dispatches(home)
        assert rows[0]["state"] == rc.NO_ANSWER and rows[0]["reason"] == "voicemail"
        assert rows[1]["state"] == rc.QUEUED
        assert (
            "went to voicemail, so I left no details" in (await _notices(home.org))[-1]
        )


# --- the app side, and the switch ------------------------------------------------------------


class TestTheAppAndTheSwitch:
    async def test_done_in_the_app_stops_the_retry(self, home):
        row = await _ring(home)
        await _report(home, _run(row["id"], answered=False, reached=None))
        await schedule.mark_done(home.org, home.asha.id, row["occurrence_id"])
        await calls.tick(_ist(10, 45, days=1))
        assert home.dial.await_count == 1
        assert (await _dispatches(home))[1]["reason"] == "cancelled"

    async def test_put_it_back_cancels_the_reminder(self, home):
        await _set(home)
        event_id = (await _cards(home, actions.REMINDER_CALL))[-1][0]
        await _press(home, event_id, verb="undo")
        assert (await _rows("reminder_call_schedules", home.org))[0][
            "state"
        ] == rc.CANCELLED
        await calls.tick(_ist(10, 30, days=1))
        home.dial.assert_not_awaited()

    async def test_routes_list_and_cancel_only_the_persons_own(self, home):
        saved = await _set(home)
        async with cs.client(home.asha.id, home.org) as client:
            listed = await client.get("/api/v1/reminder-calls")
            assert listed.status_code == 200
            assert listed.json()["reminders"][0]["title"] == "Send the proposal"
            assert listed.json()["reminders"][0]["number"] == "+91 98••••3210"
        async with cs.client(home.bob.id, home.other_org) as client:
            refused = await client.post(f"/api/v1/reminder-calls/{saved['id']}/cancel")
            assert refused.status_code == 404
        async with cs.client(home.asha.id, home.org) as client:
            done = await client.post(f"/api/v1/reminder-calls/{saved['id']}/cancel")
            assert done.status_code == 200

    async def test_off_nothing_happens(self, home, monkeypatch):
        await _set(home)
        monkeypatch.setattr(constants, "REMINDER_CALLS_ENABLED", False)
        assert await calls.tick(_ist(10, 30, days=1)) == 0
        home.dial.assert_not_awaited()
        async with cs.client(home.asha.id, home.org) as client:
            assert (await client.get("/api/v1/reminder-calls")).status_code == 404
        from api.services.workflow import decibyl

        names = [t["name"] for t in decibyl.office_tools(home.org)]
        assert tools.TOOL_NAME not in names
        with pytest.raises(ReminderCallError):
            await tools.ask(home.org, home.asha.id, {"title": "x"}, thread_id=None)
