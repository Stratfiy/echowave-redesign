"""FD-1: telling the person on the phone that they are speaking with an AI.

The same shape as the recording disclosure, and the same property worth
defending: omission cannot switch it off. A workflow that never touched the
setting discloses; only a deliberate False opts out, opting out needs an
acknowledgement naming the jurisdictions where that is unlawful, and the
switch is a row in Activity with a name on it. The line goes first, before
the recording line and the greeting, as one utterance.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from api.services.compliance import ai_disclosure
from api.services.workflow import pipecat_engine as engine_module
from api.services.workflow.pipecat_engine import PipecatEngine


def _engine(
    *,
    ai_enabled=None,
    ai_text=None,
    recording_enabled=None,
    recording_text=None,
    greeting=None,
    greeting_type=None,
    is_voice=True,
    recorded=True,
):
    node = MagicMock()
    node.ai_disclosure_enabled = ai_enabled
    node.ai_disclosure = ai_text
    node.recording_disclosure_enabled = recording_enabled
    node.recording_disclosure = recording_text
    node.greeting = greeting
    node.greeting_type = greeting_type
    node.greeting_recording_id = None
    workflow = MagicMock()
    workflow.start_node_id = "start"
    workflow.nodes = {"start": node}
    built = PipecatEngine(
        workflow=workflow,
        call_context_vars={},
        task=MagicMock(queue_frame=AsyncMock()),
        is_voice=is_voice,
    )
    built._call_recorded = recorded
    return built


@pytest.fixture(autouse=True)
def platform_default(monkeypatch):
    monkeypatch.setattr(engine_module, "AI_DISCLOSURE_ENABLED", True)
    monkeypatch.setattr(
        engine_module, "AI_DISCLOSURE_TEXT", "You are speaking with an AI."
    )
    monkeypatch.setattr(engine_module, "RECORDING_DISCLOSURE_ENABLED", True)
    monkeypatch.setattr(
        engine_module, "RECORDING_DISCLOSURE_TEXT", "This call is recorded."
    )


class TestOmissionDoesNotDisableIt:
    def test_a_workflow_that_never_set_it_still_discloses(self):
        assert (
            _engine().resolve_ai_disclosure("start") == "You are speaking with an AI."
        )

    def test_only_an_explicit_false_opts_out(self):
        assert _engine(ai_enabled=False).resolve_ai_disclosure("start") is None

    def test_the_platform_default_can_be_switched_off(self, monkeypatch):
        monkeypatch.setattr(engine_module, "AI_DISCLOSURE_ENABLED", False)
        assert _engine().resolve_ai_disclosure("start") is None
        assert _engine(ai_enabled=True).resolve_ai_disclosure("start") is not None

    def test_the_agents_own_wording_wins_and_blank_falls_through(self):
        assert (
            _engine(ai_text="Namaste, this is Asha, an AI.").resolve_ai_disclosure(
                "start"
            )
            == "Namaste, this is Asha, an AI."
        )
        assert _engine(ai_text="   ").resolve_ai_disclosure("start") == (
            "You are speaking with an AI."
        )

    def test_a_text_chat_does_not_say_it(self):
        assert _engine(is_voice=False).resolve_ai_disclosure("start") is None

    def test_it_does_not_depend_on_recording(self):
        """The voice is an AI's whether or not anyone keeps the audio."""
        built = _engine(recorded=False)
        assert built.resolve_ai_disclosure("start") == "You are speaking with an AI."
        assert built.resolve_recording_disclosure("start") is None


class TestItGoesFirst:
    def test_identity_then_recording_as_one_utterance(self):
        assert _engine().resolve_opening_disclosures("start") == (
            "You are speaking with an AI. This call is recorded."
        )

    def test_either_alone(self):
        assert _engine(recording_enabled=False).resolve_opening_disclosures(
            "start"
        ) == ("You are speaking with an AI.")
        assert _engine(ai_enabled=False).resolve_opening_disclosures("start") == (
            "This call is recorded."
        )
        assert (
            _engine(
                ai_enabled=False, recording_enabled=False
            ).resolve_opening_disclosures("start")
            is None
        )

    def test_the_greeting_follows_in_the_same_breath(self):
        built = _engine(greeting="Welcome to Narayani Dental.", greeting_type="text")
        line = built._opening_line(
            built.resolve_opening_disclosures("start"), "Welcome to Narayani Dental."
        )
        assert (
            line
            == "You are speaking with an AI. This call is recorded. Welcome to Narayani Dental."
        )

    @pytest.mark.asyncio
    async def test_spoken_on_its_own_when_there_is_no_text_to_carry_it(self):
        built = _engine()
        assert await built._speak_recording_disclosure("start")
        frame = built.task.queue_frame.await_args.args[0]
        assert frame.text == "You are speaking with an AI. This call is recorded."


def _definition(**data):
    return {
        "nodes": [{"id": "start-1", "type": "startCall", "data": data}],
        "edges": [],
    }


class TestSwitchingItOffNamesTheLaw:
    def test_off_without_the_acknowledgement_is_a_problem_naming_the_jurisdictions(
        self,
    ):
        problems = ai_disclosure.problems(_definition(ai_disclosure_enabled=False))
        assert len(problems) == 1
        assert problems[0]["id"] == "start-1"
        assert problems[0]["field"] == "data.ai_disclosure_opt_out_acknowledged"
        assert "Article 50" in problems[0]["message"]
        assert "TCPA" in problems[0]["message"]

    def test_off_with_the_acknowledgement_is_allowed(self):
        assert (
            ai_disclosure.problems(
                _definition(
                    ai_disclosure_enabled=False, ai_disclosure_opt_out_acknowledged=True
                )
            )
            == []
        )

    def test_on_or_unset_needs_nothing(self):
        assert ai_disclosure.problems(_definition()) == []
        assert ai_disclosure.problems(_definition(ai_disclosure_enabled=True)) == []
        assert ai_disclosure.problems(None) == []

    def test_the_acknowledgement_alone_does_not_switch_it_off(self):
        assert (
            ai_disclosure.problems(_definition(ai_disclosure_opt_out_acknowledged=True))
            == []
        )


@pytest.mark.asyncio
class TestTheSwitchLandsInActivity:
    async def test_off_is_a_row_with_a_name_and_the_jurisdictions(self):
        with patch(
            "api.services.workflow.agent_timeline.record", new=AsyncMock()
        ) as record:
            lines = await ai_disclosure.note_change(
                organization_id=7,
                workflow_id=3,
                workflow_name="Front Desk",
                user_id=11,
                before=_definition(),
                after=_definition(
                    ai_disclosure_enabled=False, ai_disclosure_opt_out_acknowledged=True
                ),
            )
        assert lines and lines[0].startswith(
            "AI-identity disclosure switched off on Front Desk"
        )
        record.assert_awaited_once()
        kwargs = record.await_args.kwargs
        assert kwargs["workflow_id"] == 3
        assert kwargs["actor"] == "human"
        assert kwargs["payload"]["by"] == 11
        assert [j["name"] for j in kwargs["payload"]["jurisdictions"]] == [
            "European Union",
            "United States",
        ]

    async def test_back_on_is_a_row_too_and_no_change_is_none(self):
        with patch(
            "api.services.workflow.agent_timeline.record", new=AsyncMock()
        ) as record:
            on = await ai_disclosure.note_change(
                organization_id=7,
                workflow_id=3,
                workflow_name="Front Desk",
                user_id=11,
                before=_definition(ai_disclosure_enabled=False),
                after=_definition(),
            )
            same = await ai_disclosure.note_change(
                organization_id=7,
                workflow_id=3,
                workflow_name="Front Desk",
                user_id=11,
                before=_definition(ai_disclosure_enabled=False),
                after=_definition(ai_disclosure_enabled=False),
            )
        assert on == ["AI-identity disclosure switched back on for Front Desk"]
        assert same == []
        assert record.await_count == 1

    async def test_a_new_bot_switched_off_from_the_start_is_recorded(self):
        with patch(
            "api.services.workflow.agent_timeline.record", new=AsyncMock()
        ) as record:
            lines = await ai_disclosure.note_change(
                organization_id=7,
                workflow_id=3,
                workflow_name="Front Desk",
                user_id=11,
                before=None,
                after=_definition(
                    ai_disclosure_enabled=False, ai_disclosure_opt_out_acknowledged=True
                ),
            )
        assert len(lines) == 1
        record.assert_awaited_once()


class TestABriefDefaultsItOn:
    def test_the_start_node_of_a_built_bot_says_it(self):
        from api.services.workflow.agent_brief import AgentBrief, apply_brief

        definition = {
            "nodes": [{"id": "s", "type": "startCall", "data": {}}],
            "edges": [],
        }
        brief = AgentBrief(
            call_type="inbound", use_case="", objective="", conversation_flow="x"
        )
        out = apply_brief(definition, brief)
        start = next(n for n in out["nodes"] if n["type"] == "startCall")
        assert start["data"]["ai_disclosure_enabled"] is True
