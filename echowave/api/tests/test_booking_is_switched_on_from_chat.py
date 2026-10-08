"""An owner switches booking on from Chat, on one card.

Found building a clinic receptionist end to end (October 2026). Built in
Chat, the receptionist could not book until the owner went to three places:
the booking policy (Settings, Voice, Calls), the agent's own hours, and the
booking tool on its steps. Asked "let it book appointments, Monday to
Saturday 10 to 7, 30 minutes", Decibyl had no tool for any of it -- and the
product's rule is that nobody is sent to another screen to finish
something.

``set_up_booking`` is one card that shows exactly what Confirm sets, and
Confirm (by an admin) sets all three. And the clinic template no longer
tells an agent holding the booking tool that it "does not have the live
schedule".
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from api.db import db_client
from api.services.voice import appointments, booking_setup
from api.services.workflow import actions
from api.tests.support.voice import all_on, clean, make_people

DEFINITION = {
    "nodes": [
        {"id": "s", "type": "startCall", "data": {"name": "Answer", "prompt": "Hi"}},
        {"id": "a", "type": "agentNode", "data": {"name": "Book", "prompt": "Book"}},
        {"id": "e", "type": "endCall", "data": {"name": "Close", "prompt": "Bye"}},
    ],
    "edges": [],
}

ASK = {
    "agent": "Sunrise front desk",
    "mode": "book",
    "hours": [
        {
            "days": ["mon", "tue", "wed", "thu", "fri", "sat"],
            "open": "10:00",
            "close": "19:00",
        }
    ],
    "duration_minutes": 30,
    "services": ["Consultation", "Cleaning"],
    "escalate_to": "098765 43210",
    "why": "Asked in the thread",
}


@pytest.fixture
async def people(test_engine, monkeypatch):
    all_on(monkeypatch)
    p = await make_people("booking-chat")
    p.agent = await db_client.create_workflow(
        name="Sunrise front desk",
        workflow_definition=DEFINITION,
        user_id=p.a.id,
        organization_id=p.org,
    )
    yield p
    await clean(p)


async def _card(people, arguments=ASK) -> dict:
    return await actions.resolve(
        organization_id=people.org,
        workflow_id=None,
        arguments={**arguments, "action": actions.SET_UP_BOOKING},
    )


@pytest.mark.asyncio
class TestTheCard:
    async def test_it_shows_exactly_what_confirm_sets(self, people):
        card = await _card(people)
        preview = card["preview"]
        assert "Agent: Sunrise front desk" in preview
        assert "book the appointment" in preview
        assert "Monday 10:00-19:00" in preview and "Saturday 10:00-19:00" in preview
        assert "Sunday" not in preview
        assert "30 minutes" in preview
        assert "Consultation, Cleaning" in preview
        assert "+919876543210" in preview
        assert card["state"] == actions.PROPOSED

    async def test_no_hours_is_asked_for_not_invented(self, people):
        with pytest.raises(actions.ActionError, match="opening hours"):
            await _card(people, {**ASK, "hours": []})

    async def test_an_agent_from_nowhere_is_refused(self, people):
        with pytest.raises(actions.ActionError, match="No agent"):
            await _card(people, {**ASK, "agent": "Somebody else's bot"})

    async def test_a_bad_length_is_refused_before_the_card(self, people):
        with pytest.raises(actions.ActionError):
            await _card(people, {**ASK, "duration_minutes": 7})


@pytest.mark.asyncio
class TestConfirm:
    async def _run(self, people, confirmer: int) -> str:
        card = await _card(people)
        card["confirmed"] = {"by": confirmer}
        return await actions._execute(people.org, card)

    async def test_an_admin_switches_on_all_three(self, people):
        said = await self._run(people, people.a.id)
        assert "tester" in said and "publish" in said
        policy = await appointments.get_policy(people.org)
        assert policy["booking"] == "book"
        assert policy["call_workflow_id"] == people.agent.id
        assert policy["duration_minutes"] == 30
        assert policy["services"] == ["Consultation", "Cleaning"]
        draft = await db_client.get_draft_version(people.agent.id)
        hours = draft.workflow_configurations["agent_schedule"]
        assert hours["enabled"] and len(hours["slots"]) == 6
        tool_uuid = await appointments.ensure_tool(
            organization_id=people.org, user_id=people.a.id
        )
        steps = {
            n["id"]: n["data"].get("tool_uuids") or []
            for n in draft.workflow_json["nodes"]
        }
        assert tool_uuid in steps["a"] and tool_uuid in steps["s"]

    async def test_a_member_who_is_not_an_admin_cannot(self, people):
        with pytest.raises(actions.ActionError, match="admin"):
            await self._run(people, people.b.id)
        assert (await appointments.get_policy(people.org))["booking"] == "off"


class TestDecibylHoldsIt:
    def test_offered_with_its_rule_while_booking_is_on(self, monkeypatch):
        from api.services.workflow import decibyl

        all_on(monkeypatch)
        assert booking_setup.TOOL_NAME in {
            t["name"] for t in decibyl.office_tools(None)
        }
        assert booking_setup.TOOL_NAME in decibyl.system_prompt(None)

    def test_not_offered_while_off(self, monkeypatch):
        from api import constants
        from api.services.workflow import decibyl

        monkeypatch.setattr(constants, "CALL_APPOINTMENT_ENABLED", False)
        assert booking_setup.TOOL_NAME not in {
            t["name"] for t in decibyl.office_tools(None)
        }


class TestTheTemplateDoesNotContradictTheTool:
    def test_the_booking_step_uses_the_tool_when_it_has_it(self):
        from api.services.agent_templates.catalogue import get_template

        template = get_template("clinic_appointment")
        booking = next(n for n in template.nodes if n.name == "Book appointment")
        assert appointments.SLOTS_TOOL in booking.prompt
        assert appointments.BOOK_TOOL in booking.prompt
        # The old line, said unconditionally, told a receptionist holding the
        # booking tool that it had no schedule.
        assert "You do not have the live schedule, so never" not in booking.prompt


@pytest.mark.asyncio
async def test_decibyl_dispatches_it_as_a_card(people):
    from types import SimpleNamespace

    from api.services.workflow import decibyl

    propose = AsyncMock(return_value={"status": "proposed"})
    with patch.object(decibyl.actions, "propose", propose):
        await decibyl._tool(
            people.org, SimpleNamespace(id="c1", name="set_up_booking", arguments=ASK)
        )
    assert propose.await_args.kwargs["arguments"]["action"] == actions.SET_UP_BOOKING
