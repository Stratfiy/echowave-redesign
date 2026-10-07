"""Today as one ordered list, Activity, missed calls handled, and routines
from chat that start on once confirmed (handoff 22, 31.4; screens 07, 09,
10; founder: approvals docked, "Will do, every Monday at 10.").

Done when: the list is in the handoff's order; "Nothing due in Decibyl"
appears only when every section answered and nothing is there, and a
section that failed says so instead of looking empty; a missing calendar is
a separate setup line; suggestions are at most three, explained and
dismissible; a missed call is called back only through a card a person
approves; Activity shows finished work with its evidence and keeps provider
internals out; and a routine set from chat runs once its card is confirmed
-- with the flag off, it is saved switched off exactly as before.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

import pytest

from api import constants
from api.db import db_client
from api.enums import AgentEventActor, AgentEventKind
from api.services.today import activity, approvals, listing
from api.services.today.scope import NotFound
from api.services.workflow import actions, routines, task_ledger
from api.tests.today_helpers import clean, client_as, make_people, switch_on

NOW = datetime(2026, 10, 8, 4, 30, tzinfo=UTC)


@pytest.fixture
async def people(test_engine):
    p = await make_people()
    yield p
    await clean(p)


@pytest.fixture
def on(monkeypatch):
    switch_on(monkeypatch, "today_list")


def _no_calls():
    return patch.object(db_client, "list_missed_calls", AsyncMock(return_value=[]))


def _missed(id_=41, caller="+919800000001", outcome="pending"):
    return SimpleNamespace(
        id=id_,
        caller=caller,
        received_at=NOW - timedelta(hours=1),
        outcome=outcome,
        organization_id=None,
    )


async def _card(org: int, state: str = actions.PROPOSED, **extra) -> int:
    payload = {
        "state": state,
        "label": "Turn Front desk on",
        "action": actions.TURN_BOT_ON,
        "args": {"workflow_id": 1, "bot_name": "Front desk", "is_live": True},
        "reversible": True,
        **extra,
    }
    return int(
        await db_client.record_agent_event(
            organization_id=org,
            kind=AgentEventKind.ACTION_PROPOSED.value,
            actor=AgentEventActor.AGENT.value,
            summary=payload["label"],
            payload=payload,
        )
    )


@pytest.mark.asyncio
class TestTheOrderedList:
    async def test_nothing_due_only_when_every_section_answered(self, people, on):
        with _no_calls():
            view = await listing.today(people.me, now=NOW)
        assert view["order"] == [
            "approvals",
            "due",
            "brief",
            "upcoming",
            "suggestions",
            "end_of_day",
        ]
        assert view["empty"] is True and view["empty_copy"] == "Nothing due in Decibyl"
        assert view["timezone"] == "Asia/Kolkata"
        assert view["date_label"] == "Thursday 8 October 2026"

    async def test_a_failed_section_is_failed_not_empty(self, people, on):
        with (
            _no_calls(),
            patch.object(
                approvals, "pending", AsyncMock(side_effect=RuntimeError("db"))
            ),
        ):
            view = await listing.today(people.me, now=NOW)
        assert view["sections"]["approvals"]["state"] == "failed"
        assert view["empty"] is False and view["empty_copy"] is None

    async def test_a_missing_calendar_is_its_own_setup_line(self, people, on):
        with _no_calls():
            view = await listing.today(people.me, now=NOW)
        assert view["missing_sources"] == [
            {
                "kind": "calendar",
                "message": "Connect your calendar to include appointments",
            }
        ]

    async def test_approvals_come_first_with_the_servers_count(self, people, on):
        await _card(people.me.organization_id)
        with _no_calls():
            view = await listing.today(people.me, now=NOW)
        assert view["sections"]["approvals"]["count"] == 1
        assert view["empty"] is False

    async def test_due_work_is_listed_with_full_local_times(self, people, on):
        await task_ledger.create(
            organization_id=people.me.organization_id,
            title="Send the GST filing",
            created_by=people.me.user_id,
            due_at=NOW + timedelta(hours=2),
        )
        with patch.object(
            db_client,
            "list_missed_calls",
            AsyncMock(return_value=[_missed(), _missed(42, outcome="called_back")]),
        ):
            view = await listing.today(people.me, now=NOW)
        due = view["sections"]["due"]["items"]
        kinds = [i["kind"] for i in due]
        assert sorted(kinds) == ["missed_call", "task"]
        task = next(i for i in due if i["kind"] == "task")
        assert task["when"] == "Thu 8 Oct 2026, 12:00 IST (Asia/Kolkata)"
        # A call already returned is handled, not due.
        assert [i["id"] for i in due if i["kind"] == "missed_call"] == [41]

    async def test_a_colleagues_task_is_not_in_my_today(self, people, on):
        await task_ledger.create(
            organization_id=people.me.organization_id,
            title="Their errand",
            created_by=people.colleague.user_id,
            due_at=NOW + timedelta(hours=2),
        )
        with _no_calls():
            view = await listing.today(people.me, now=NOW)
        assert view["sections"]["due"]["items"] == []

    async def test_at_most_three_suggestions_each_with_why_and_dismissible(
        self, people, on, monkeypatch
    ):
        switch_on(monkeypatch, "daily_brief")
        with patch.object(
            db_client, "list_missed_calls", AsyncMock(return_value=[_missed()])
        ):
            view = await listing.today(people.me, now=NOW)
            suggestions = view["sections"]["suggestions"]["items"]
            assert 1 <= len(suggestions) <= 3
            assert all(s["why"] for s in suggestions)
            keys = [s["key"] for s in suggestions]
            assert "offer_daily_brief" in keys
            await listing.dismiss(people.me, "offer_daily_brief")
            again = await listing.today(people.me, now=NOW)
        assert "offer_daily_brief" not in [
            s["key"] for s in again["sections"]["suggestions"]["items"]
        ]

    async def test_no_brief_offer_while_its_switch_is_off(self, people, on):
        with _no_calls():
            view = await listing.today(people.me, now=NOW)
        assert view["sections"]["brief"] is None
        assert "offer_daily_brief" not in [
            s["key"] for s in view["sections"]["suggestions"]["items"]
        ]


@pytest.mark.asyncio
class TestMissedCallsAskFirst:
    async def test_call_back_is_a_card_not_a_call(self, people, on, monkeypatch):
        monkeypatch.setattr(constants, "DECIBYL_PRIVATE_THREADS_ENABLED", True)
        row = _missed()
        row.organization_id = people.me.organization_id
        placed = AsyncMock()
        with (
            patch.object(db_client, "get_missed_call", AsyncMock(return_value=row)),
            patch("api.services.telephony.missed_call.place_callback", placed),
        ):
            result = await listing.propose_callback(people.me, row.id)
        placed.assert_not_awaited()
        card = await approvals.preview(people.me, result["event_id"])
        assert card["action"] == actions.RETURN_MISSED_CALL
        assert card["screen_state"] == "pending"
        assert card["recipient"] == "+919800000001"
        # It is on my own conversation: a colleague cannot see it.
        with pytest.raises(NotFound):
            await approvals.preview(people.colleague, result["event_id"])

    async def test_an_unknown_call_is_not_here(self, people, on):
        with patch.object(db_client, "get_missed_call", AsyncMock(return_value=None)):
            with pytest.raises(NotFound):
                await listing.propose_callback(people.me, 999)


@pytest.mark.asyncio
class TestActivity:
    async def test_finished_work_shows_with_its_evidence(self, people, on):
        org = people.me.organization_id
        done = await _card(
            org,
            actions.DONE,
            done={"at": NOW.isoformat(), "note": "Front desk is now on."},
        )
        unknown = await _card(org, actions.OUTCOME_UNKNOWN)
        await _card(org)  # proposed: in the approvals queue, not Activity
        listed = await activity.list_for(people.me, now=NOW)
        by_id = {(i["kind"], i["id"]): i for i in listed["items"]}
        assert by_id[("card", done)]["state"] == "completed"
        assert by_id[("card", done)]["evidence"] == "Front desk is now on."
        assert by_id[("card", unknown)]["evidence"] == task_ledger.UNKNOWN_COPY
        assert len([i for i in listed["items"] if i["kind"] == "card"]) == 2

    async def test_filters(self, people, on):
        org = people.me.organization_id
        await _card(org, actions.DONE, done={"at": NOW.isoformat(), "note": "ok"})
        await _card(org, actions.FAILED, error="That agent no longer exists.")
        failed = await activity.list_for(people.me, state="failed", now=NOW)
        assert [i["state"] for i in failed["items"]] == ["failed"]
        assert (await activity.list_for(people.me, helper="Nobody", now=NOW))[
            "items"
        ] == []

    async def test_card_detail_has_stages_and_never_offers_a_blind_retry(
        self, people, on
    ):
        org = people.me.organization_id
        event_id = await _card(
            org,
            actions.OUTCOME_UNKNOWN,
            confirmed={"by": people.me.user_id, "at": NOW.isoformat()},
        )
        detail = await activity.detail(people.me, "card", event_id)
        assert [s["label"] for s in detail["stages"]][:2] == ["Proposed", "Approved"]
        assert detail["can_retry"] is False and detail["check_delivery"] is True
        assert detail["evidence"] == task_ledger.UNKNOWN_COPY

    async def test_task_detail_is_scoped(self, people, on):
        task, _ = await task_ledger.create(
            organization_id=people.me.organization_id,
            title="Chase Ravi",
            created_by=people.me.user_id,
        )
        detail = await activity.detail(people.me, "task", task.id)
        assert detail["goal"] == "Chase Ravi" and detail["can_cancel"] is True
        assert detail["stages"][1]["label"] == "Queued"
        with pytest.raises(NotFound):
            await activity.detail(people.stranger, "task", task.id)


@pytest.mark.asyncio
class TestRoutinesFromChat:
    def _payload(self):
        return {
            "action": actions.SCHEDULE_ROUTINE,
            "args": {
                "name": "Unpaid invoices",
                "instruction": "List unpaid invoices",
                "cadence": "weekly",
                "anchor": "clock",
                "at_minute": 600,
                "offset_minutes": 0,
                "weekday": 0,
                "said": "every Monday at 10",
            },
            "state": actions.RUNNING,
        }

    async def test_confirmed_it_starts_on_and_says_so(self, people, monkeypatch):
        switch_on(monkeypatch, "routine_start_on")
        org = people.me.organization_id
        payload = self._payload()
        line = await actions._execute(org, payload, event_id=777)
        assert line == "Will do, every Monday at 10:00."
        routine = await db_client.get_routine(
            payload["result"]["routine_id"], organization_id=org
        )
        assert routine.is_active is True and routine.tested_at is None
        assert routine.armed_by_card_event_id == 777
        spec = routines.spec_from_model(routine)
        assert routines.may_arm(spec)
        # Monday 12 October 2026, 10:00 in Kolkata: the tick fires it.
        monday = datetime(2026, 10, 12, 10, 1, tzinfo=ZoneInfo("Asia/Kolkata"))
        assert routines.decide(spec, now=monday, zone=ZoneInfo("Asia/Kolkata")).fire

    async def test_off_it_is_saved_switched_off_as_before(self, people):
        org = people.me.organization_id
        payload = self._payload()
        line = await actions._execute(org, payload, event_id=777)
        assert line.startswith(
            "Saved Unpaid invoices (every Monday at 10), switched off."
        )
        rows = await db_client.routines_for_organization(organization_id=org)
        assert [r.is_active for r in rows] == [False]
        assert rows[0].armed_by_card_event_id is None

    async def test_a_decibyl_routine_can_be_deleted(self, people):
        org = people.me.organization_id
        routine = await db_client.create_routine(
            organization_id=org, workflow_id=None, name="x", instruction="y"
        )
        assert await db_client.delete_routine(
            routine.id, organization_id=org, workflow_id=None
        )
        assert await db_client.get_routine(routine.id, organization_id=org) is None


@pytest.mark.asyncio
class TestArrival:
    async def test_off_today_is_not_there(self, people):
        async with client_as(people.me_user) as client:
            assert (await client.get("/api/v1/today")).status_code == 404
            assert (await client.get("/api/v1/today/activity")).status_code == 404

    async def test_on_today_activity_and_routine_preview(self, people, on):
        await _card(people.me.organization_id)
        async with client_as(people.me_user) as client:
            with _no_calls():
                today = await client.get("/api/v1/today")
            assert today.status_code == 200, today.text
            assert today.json()["sections"]["approvals"]["count"] == 1
            assert (await client.get("/api/v1/today/activity")).status_code == 200
            preview = (
                await client.post(
                    "/api/v1/today/routines/preview",
                    json={
                        "cadence": "weekly",
                        "anchor": "clock",
                        "at_minute": 600,
                        "weekday": 0,
                    },
                )
            ).json()
            assert preview["schedule"] == "Every Monday at 10:00"
            assert preview["sentence"].startswith(
                "Every Monday at 10:00. Next run: Mon "
            )
            dismissed = await client.post(
                "/api/v1/today/suggestions/offer_daily_brief/dismiss", json={}
            )
            assert dismissed.status_code == 200
