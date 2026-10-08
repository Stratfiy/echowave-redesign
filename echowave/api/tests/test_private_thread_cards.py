"""A card on someone's private Decibyl conversation is theirs to decide
(phase 3 of stream `today`; design "Scope"; D-1b).

The queue and the dock already hid such a card from a colleague, but
``POST /timeline/actions/settle`` let a plain member approve it anyway: in
the integrated build, B confirmed a routine A had set from A's private chat.
Done when a card on a private conversation can be settled or edited only
by whoever may read that conversation -- the same rule the timeline reads
by -- while cards with their own owner rule (orders, care, meetings,
browser and desktop steps, identity acts) keep theirs.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from api import constants
from api.db import db_client
from api.enums import AgentEventActor, AgentEventKind
from api.services.workflow import actions
from api.tests.today_helpers import clean, client_as, make_people


@pytest.fixture
async def people(test_engine):
    p = await make_people()
    yield p
    await clean(p)


@pytest.fixture
def private(monkeypatch):
    monkeypatch.setattr(constants, "DECIBYL_PRIVATE_THREADS_ENABLED", True)


def _routine_card() -> dict:
    payload = {
        "state": actions.PROPOSED,
        "label": "Schedule Invoices: every weekday at 09:00",
        "action": actions.SCHEDULE_ROUTINE,
        "args": {
            "workflow_id": None,
            "name": "Invoices",
            "instruction": "List unpaid invoices",
            "cadence": "weekdays",
            "anchor": "clock",
            "at_minute": 540,
            "offset_minutes": 0,
            "weekday": 0,
            "said": "every weekday at 09:00",
        },
        "reversible": True,
    }
    payload["version"] = actions.payload_version(payload)
    return payload


async def _private_card(people, payload=None) -> int:
    org, author = people.me.organization_id, people.me.user_id
    thread = str(uuid4())
    await db_client.record_agent_event(
        organization_id=org,
        kind=AgentEventKind.MESSAGE.value,
        actor=AgentEventActor.HUMAN.value,
        summary="schedule it",
        payload={"body": "schedule it", "author_id": author},
        thread_id=thread,
    )
    payload = payload or _routine_card()
    return int(
        await db_client.record_agent_event(
            organization_id=org,
            kind=AgentEventKind.ACTION_PROPOSED.value,
            actor=AgentEventActor.AGENT.value,
            summary=payload["label"],
            payload=payload,
            thread_id=thread,
        )
    )


def _quiet():
    return (
        patch.object(actions.approvals, "check", new=AsyncMock()),
        patch.object(actions, "_audit", new=AsyncMock()),
        patch("api.tasks.arq.enqueue_job", new=AsyncMock()),
    )


@pytest.mark.asyncio
class TestAColleaguesPrivateCard:
    async def test_a_colleague_cannot_approve_it(self, people, private):
        event_id = await _private_card(people)
        a, b, c = _quiet()
        with a, b, c:
            with pytest.raises(actions.ActionError) as caught:
                await actions.settle(
                    organization_id=people.me.organization_id,
                    event_id=event_id,
                    verb="confirm",
                    user_id=people.colleague.user_id,
                    version=_routine_card()["version"],
                )
            assert str(caught.value) == actions.NOT_HERE
            # Its author still can.
            payload = await actions.settle(
                organization_id=people.me.organization_id,
                event_id=event_id,
                verb="confirm",
                user_id=people.me.user_id,
                version=_routine_card()["version"],
            )
        assert payload["state"] == actions.ARMED

    async def test_nor_decline_it_over_http(self, people, private):
        event_id = await _private_card(people)
        async with client_as(people.colleague_user) as client:
            r = await client.post(
                "/api/v1/timeline/actions/settle",
                json={"event_id": event_id, "verb": "decline"},
            )
        assert r.status_code == 409 and r.json()["detail"] == actions.NOT_HERE
        event = await db_client.get_agent_event(
            event_id, organization_id=people.me.organization_id
        )
        assert event.payload["state"] == actions.PROPOSED

    async def test_nor_edit_it(self, people, private, monkeypatch):
        monkeypatch.setattr(constants, "TASK_LEDGER_ENABLED", True)
        send = {
            "state": actions.PROPOSED,
            "label": "Send the quote via gmail",
            "action": actions.RUN_TOOL,
            "args": {
                "tool_uuid": "t",
                "toolkit": "gmail",
                "arguments": {"to": "x@example.com"},
            },
        }
        event_id = await _private_card(people, send)
        with (
            patch.object(actions.audit_log, "record", new=AsyncMock()),
            pytest.raises(actions.ActionError),
        ):
            await actions.revise(
                organization_id=people.me.organization_id,
                event_id=event_id,
                arguments={"to": "attacker@example.com"},
                user_id=people.colleague.user_id,
            )

    async def test_with_private_threads_off_the_workspace_decides_as_before(
        self, people
    ):
        event_id = await _private_card(people)
        a, b, c = _quiet()
        with a, b, c:
            payload = await actions.settle(
                organization_id=people.me.organization_id,
                event_id=event_id,
                verb="decline",
                user_id=people.colleague.user_id,
            )
        assert payload["state"] == actions.DECLINED

    async def test_a_card_with_its_own_owner_keeps_its_rule(self, people, private):
        """A meeting follow-up on a thread someone else started is still
        its capturer's to settle: the owner rule decides, not the thread."""
        payload = {
            "state": actions.PROPOSED,
            "label": "Add a task: send the deck",
            "action": actions.MEETING_FOLLOW_UP,
            "args": {
                "owner_user_id": people.colleague.user_id,
                "task": "Send the deck",
            },
        }
        event_id = await _private_card(people, payload)
        assert actions.answer_refusal(payload, people.colleague.user_id) is None
        event = await db_client.get_agent_event(
            event_id, organization_id=people.me.organization_id
        )
        assert await actions.thread_refusal(event, people.colleague.user_id) is None


async def _armed_routine(people, event_id: int):
    return await db_client.create_routine(
        organization_id=people.me.organization_id,
        workflow_id=None,
        name="Invoices from my chat",
        instruction="List unpaid invoices",
        cadence="weekdays",
        anchor="clock",
        at_minute=540,
        is_active=True,
        armed_by_card_event_id=event_id,
    )


@pytest.mark.asyncio
class TestARoutineSetInAPrivateChat:
    """With routine_start_on, a routine confirmed on a card in A's private
    chat is armed by that card. Its name and instruction are A's words, so a
    colleague who cannot read that chat must not see the routine, and its
    runs report back into that chat, not the shared original thread."""

    async def test_a_colleague_does_not_see_it_listed(self, people, private):
        event_id = await _private_card(people)
        await _armed_routine(people, event_id)
        async with client_as(people.colleague_user) as client:
            listed = (await client.get("/api/v1/routines")).json()["routines"]
        assert [r["name"] for r in listed] == []
        async with client_as(people.me_user) as client:
            listed = (await client.get("/api/v1/routines")).json()["routines"]
        assert [r["name"] for r in listed] == ["Invoices from my chat"]

    async def test_its_run_reports_into_the_chat_it_came_from(self, people, private):
        from api.services.workflow import routine_runner

        event_id = await _private_card(people)
        card = await db_client.get_agent_event(
            event_id, organization_id=people.me.organization_id
        )
        routine = await _armed_routine(people, event_id)
        seen = {}

        async def fake_answer(organization_id, text, **kwargs):
            seen["thread_id"] = kwargs.get("thread_id")
            return "3 unpaid invoices, ₹12,400 in all."

        with patch("api.services.workflow.decibyl.answer", new=fake_answer):
            await routine_runner.run_routine(routine.id)
        assert seen["thread_id"] == card.thread_id
        rows = await db_client.agent_events(
            organization_id=people.me.organization_id,
            kinds=[AgentEventKind.DELIVERABLE.value],
            assistant_thread=True,
            thread_id=card.thread_id,
        )
        assert [r.summary for r in rows] == ["3 unpaid invoices, ₹12,400 in all."]

    async def test_the_tick_writes_its_start_line_there_too(self, people, private):
        from datetime import UTC, datetime

        from api.tasks import routines as tick

        event_id = await _private_card(people)
        card = await db_client.get_agent_event(
            event_id, organization_id=people.me.organization_id
        )
        await _armed_routine(people, event_id)
        redis = AsyncMock()
        # Thursday 8 October 2026, 09:01 in Kolkata.
        monday_nine = datetime(2026, 10, 8, 3, 31, tzinfo=UTC)
        with (
            patch.object(tick, "datetime", wraps=datetime) as clock,
            patch.object(
                tick.onboarding_credits, "settle_in_own_session", new=AsyncMock()
            ),
        ):
            clock.now.return_value = monday_nine
            await tick.fire_due_routines({"redis": redis})
        rows = await db_client.agent_events(
            organization_id=people.me.organization_id,
            kinds=[AgentEventKind.ROUTINE_FIRED.value],
            assistant_thread=True,
            thread_id=card.thread_id,
        )
        assert len(rows) == 1
