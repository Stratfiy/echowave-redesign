""" "Call it for me" (launch stream voice; handoff 6, 10).

Done when: Decibyl can only propose a call (a card), never dial; the card
says who, about what, and the exact opening announcement, and cannot be
undone; it is refused honestly when no phone line or helper is set up; the
approved card runs once through the dial guards (do-not-disturb, calling
window) and the call opens with the announcement even where the agent's own
AI line is off; a refusal dials nothing; and a failure mid-dial is
"outcome unknown", never "failed" and never redialled.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import delete

from api import constants
from api.db import db_client
from api.db.models import OrganizationModel
from api.db.shell_models import UserOnboardingModel
from api.enums import AgentEventActor, AgentEventKind
from api.services import acting
from api.services.compliance import dnd
from api.services.voice import call_for_me, readiness
from api.services.workflow import actions
from api.services.workflow import pipecat_engine as engine_module
from api.services.workflow.pipecat_engine import PipecatEngine
from api.tests.support.voice import clean, make_people


@pytest.fixture
async def people(test_engine):
    p = await make_people("voice-c")
    async with db_client.async_session() as session:
        session.add(UserOnboardingModel(user_id=p.a.id, preferred_name="Asha Rao"))
        await session.commit()
    yield p
    await clean(p)
    async with db_client.async_session() as session:
        await session.execute(
            delete(UserOnboardingModel).where(UserOnboardingModel.user_id == p.a.id)
        )
        await session.commit()


@pytest.fixture
def calls_ready(monkeypatch):
    monkeypatch.setattr(constants, "CALL_FOR_ME_ENABLED", True)

    async def available(**_):
        return readiness.Readiness(readiness.AVAILABLE)

    monkeypatch.setattr(readiness, "calls", available)


ASK = {
    "phone_number": "098765 43210",
    "callee": "Green Trends salon",
    "purpose": "Book a haircut for Saturday afternoon",
    "details": "First name Asha",
    "why": "Asked to call the salon",
}


@pytest.mark.asyncio
class TestTheCard:
    async def test_off_it_cannot_be_proposed(self, people):
        with pytest.raises(call_for_me.CallNotPossible):
            await call_for_me.resolve(
                organization_id=people.org, arguments=ASK, user_id=people.a.id
            )

    async def test_no_phone_line_is_needs_setup_in_words(self, people, monkeypatch):
        monkeypatch.setattr(constants, "CALL_FOR_ME_ENABLED", True)
        with pytest.raises(call_for_me.CallNotPossible) as refused:
            await call_for_me.resolve(
                organization_id=people.org, arguments=ASK, user_id=people.a.id
            )
        assert "No phone line is connected" in str(refused.value)
        assert "Settings, Phone numbers" in str(refused.value)

    async def test_the_card_says_exactly_what_will_happen(self, people, calls_ready):
        with acting.acting_as(people.a.id):
            payload = await actions.resolve(
                organization_id=people.org,
                workflow_id=None,
                arguments={**ASK, "action": actions.PLACE_CALL},
            )
        assert (
            payload["action"] == "place_call" and payload["state"] == actions.PROPOSED
        )
        assert payload["args"] == {
            "to": "+919876543210",
            "callee": "Green Trends salon",
            "purpose": "Book a haircut for Saturday afternoon",
            "details": "First name Asha",
            "principal_user_id": people.a.id,
        }
        assert payload["reversible"] is False and payload["reaches_people"] is True
        assert payload["label"] == (
            "Call Green Trends salon for you: Book a haircut for Saturday afternoon"
        )
        assert (
            "Hello, this is Decibyl, an AI assistant calling on behalf of Asha Rao."
            in payload["effect"]
        )
        assert "+91 98••••3210" in payload["effect"]
        assert "cannot be undone" in payload["effect"]
        assert actions._is_outbound(payload)

    async def test_a_new_version_needs_a_new_approval(self, people, calls_ready):
        with acting.acting_as(people.a.id):
            first = await call_for_me.resolve(
                organization_id=people.org, arguments=ASK, user_id=people.a.id
            )
            second = await call_for_me.resolve(
                organization_id=people.org,
                arguments={**ASK, "phone_number": "9876500000"},
                user_id=people.a.id,
            )
        one = actions.payload_version({"action": "place_call", "args": first["args"]})
        two = actions.payload_version({"action": "place_call", "args": second["args"]})
        assert one != two

    @pytest.mark.parametrize(
        "change,said",
        [
            ({"phone_number": "12"}, "does not look like a phone number"),
            ({"purpose": ""}, "what the call should achieve"),
            ({"details": "x" * 600}, "too long"),
        ],
    )
    async def test_refused_in_words_the_model_can_repeat(
        self, people, calls_ready, change, said
    ):
        with pytest.raises(call_for_me.CallNotPossible) as refused:
            await call_for_me.resolve(
                organization_id=people.org,
                arguments={**ASK, **change},
                user_id=people.a.id,
            )
        assert said in str(refused.value)

    async def test_only_a_signed_in_person(self, people, calls_ready):
        with pytest.raises(call_for_me.CallNotPossible):
            await call_for_me.resolve(
                organization_id=people.org, arguments=ASK, user_id=None
            )


@pytest.fixture
async def dial_rig(people, calls_ready, monkeypatch):
    workflow = await db_client.create_workflow(
        name="Call and Appointment",
        workflow_definition={"nodes": [], "edges": []},
        user_id=people.a.id,
        organization_id=people.org,
    )

    async def policy(_org):
        return {"call_workflow_id": workflow.id}

    async def may_call(_org, number, **_):
        return number

    from api.services.organization_preferences import (
        get_organization_preferences,  # noqa: F401
    )
    from api.services.telephony import factory, outbound
    from api.services.voice import appointments

    dial = AsyncMock(return_value=4242)
    monkeypatch.setattr(appointments, "get_policy", policy)
    monkeypatch.setattr(dnd, "assert_may_call", may_call)
    monkeypatch.setattr(
        db_client,
        "get_default_telephony_configuration",
        AsyncMock(return_value=SimpleNamespace(id=7)),
    )
    monkeypatch.setattr(
        factory, "get_default_telephony_provider", AsyncMock(return_value=MagicMock())
    )
    monkeypatch.setattr(outbound, "dial_workflow", dial)
    return SimpleNamespace(dial=dial, workflow=workflow)


def _approved(people) -> dict:
    return {
        "action": "place_call",
        "args": {
            "to": "+919876543210",
            "callee": "Green Trends salon",
            "purpose": "Book a haircut",
            "details": "",
            "principal_user_id": people.a.id,
        },
        "label": "Call Green Trends salon for you: Book a haircut",
        "version": "v1",
    }


@pytest.mark.asyncio
class TestTheCall:
    async def test_dials_once_with_the_announcement(self, people, dial_rig):
        payload = _approved(people)
        note = await call_for_me.execute(
            organization_id=people.org, payload=payload, event_id=55
        )
        assert note.startswith("Calling Green Trends salon now")
        dial_rig.dial.assert_awaited_once()
        kwargs = dial_rig.dial.await_args.kwargs
        assert kwargs["source"] == "call_for_me"
        assert kwargs["to_number"] == "+919876543210"
        assert kwargs["telephony_configuration_id"] == 7
        assert kwargs["workflow"].id == dial_rig.workflow.id
        context = kwargs["extra_context"]
        assert context["on_behalf_announcement"] == (
            "Hello, this is Decibyl, an AI assistant calling on behalf of Asha Rao."
        )
        assert context["idempotency_key"] == "card:55:v1"
        assert context["call_purpose"] == "Book a haircut"
        assert payload["result"] == {"workflow_run_id": 4242}

    async def test_do_not_disturb_dials_nothing(self, people, dial_rig, monkeypatch):
        async def listed(*_a, **_k):
            raise dnd.DoNotDisturbListed("This number is on your do-not-disturb list.")

        monkeypatch.setattr(dnd, "assert_may_call", listed)
        with pytest.raises(call_for_me.CallNotPossible) as refused:
            await call_for_me.execute(
                organization_id=people.org, payload=_approved(people), event_id=1
            )
        assert "do-not-disturb" in str(refused.value)
        dial_rig.dial.assert_not_awaited()

    async def test_switched_off_before_it_ran(self, people, dial_rig, monkeypatch):
        monkeypatch.setattr(constants, "CALL_FOR_ME_ENABLED", False)
        with pytest.raises(call_for_me.CallNotPossible):
            await call_for_me.execute(
                organization_id=people.org, payload=_approved(people), event_id=1
            )
        dial_rig.dial.assert_not_awaited()


async def _armed_card(people) -> int:
    payload = {**_approved(people), "state": actions.ARMED}
    payload.pop("version")
    return int(
        await db_client.record_agent_event(
            organization_id=people.org,
            kind=AgentEventKind.ACTION_PROPOSED.value,
            actor=AgentEventActor.AGENT.value,
            summary=payload["label"],
            payload=payload,
        )
    )


@pytest.mark.asyncio
class TestRunsOnceThroughTheCard:
    async def test_a_failure_mid_dial_is_outcome_unknown_even_without_the_ledger(
        self, people
    ):
        event_id = await _armed_card(people)
        with (
            patch.object(
                actions, "_execute", new=AsyncMock(side_effect=TimeoutError())
            ),
            patch.object(actions, "_say", new=AsyncMock()),
        ):
            await actions.run(event_id, people.org)
            await actions.run(event_id, people.org)  # a retry does nothing
        event = await db_client.get_agent_event(event_id, organization_id=people.org)
        assert event.payload["state"] == actions.OUTCOME_UNKNOWN
        assert "Please do not send it again" in event.payload["error"]

    async def test_a_refusal_fails_the_card_with_its_reason(self, people):
        event_id = await _armed_card(people)
        refusal = actions.ActionError("No phone line is connected.")
        with (
            patch.object(actions, "_execute", new=AsyncMock(side_effect=refusal)),
            patch.object(actions, "_say", new=AsyncMock()),
        ):
            await actions.run(event_id, people.org)
        event = await db_client.get_agent_event(event_id, organization_id=people.org)
        assert event.payload["state"] == actions.FAILED
        assert event.payload["error"] == "No phone line is connected."

    async def test_two_jobs_dial_once(self, people, dial_rig):
        event_id = await _armed_card(people)
        with patch.object(actions, "_say", new=AsyncMock()):
            await asyncio.gather(
                actions.run(event_id, people.org), actions.run(event_id, people.org)
            )
        assert dial_rig.dial.await_count == 1
        event = await db_client.get_agent_event(event_id, organization_id=people.org)
        assert event.payload["state"] == actions.DONE


def _engine(context: dict, *, node_enabled=False):
    node = MagicMock()
    node.ai_disclosure_enabled = node_enabled
    node.ai_disclosure = None
    workflow = MagicMock()
    workflow.nodes = {"start": node}
    return PipecatEngine(
        workflow=workflow,
        call_context_vars=context,
        task=MagicMock(queue_frame=AsyncMock()),
        is_voice=True,
    )


class TestTheAnnouncement:
    def test_spoken_first_even_where_the_agent_switched_its_ai_line_off(
        self, monkeypatch
    ):
        monkeypatch.setattr(engine_module, "AI_DISCLOSURE_ENABLED", True)
        line = call_for_me.announcement("Asha Rao")
        engine = _engine({"on_behalf_announcement": line}, node_enabled=False)
        assert engine.resolve_ai_disclosure("start") == line

    def test_absent_leaves_every_other_call_as_it_was(self, monkeypatch):
        monkeypatch.setattr(engine_module, "AI_DISCLOSURE_ENABLED", True)
        assert _engine({}, node_enabled=False).resolve_ai_disclosure("start") is None

    def test_no_name_still_says_who_is_calling(self):
        assert call_for_me.announcement("") == (
            "Hello, this is Decibyl, an AI assistant calling on behalf of a Decibyl user."
        )


@pytest.mark.asyncio
class TestDecibylOffersIt:
    async def test_only_while_on(self, people, monkeypatch):
        from api.services.workflow import decibyl

        names = {t["name"] for t in decibyl.office_tools(people.org)}
        assert "call_for_me" not in names
        assert "call_for_me" not in decibyl.system_prompt(people.org)
        monkeypatch.setattr(constants, "CALL_FOR_ME_ENABLED", True)
        names = {t["name"] for t in decibyl.office_tools(people.org)}
        assert "call_for_me" in names
        assert call_for_me.RULES in decibyl.system_prompt(people.org)

    async def test_the_tool_proposes_a_card_and_never_dials(self, people, monkeypatch):
        from api.services.workflow import decibyl

        monkeypatch.setattr(constants, "CALL_FOR_ME_ENABLED", True)
        propose = AsyncMock(return_value={"status": "proposed"})
        monkeypatch.setattr(actions, "propose", propose)
        call = SimpleNamespace(name="call_for_me", arguments=ASK, id="t1")
        result = await decibyl._tool(people.org, call, people.a.id)
        assert result == {"status": "proposed"}
        assert propose.await_args.kwargs["arguments"]["action"] == "place_call"
        assert propose.await_args.kwargs["in_channel"] is False


def test_orgs_are_real_models():
    # Guard against the fixture drifting from the schema it scopes by.
    assert hasattr(OrganizationModel, "id")
