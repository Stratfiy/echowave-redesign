"""Telling the person on the phone that the call is recorded.

Twelve US states require two-party consent to record, Indian telecom rules
expect disclosure, and DPDP treats the recording as personal data collected from
the person who answered. The industry answer is the same everywhere: say it in
the agent's first turn.

The property worth defending is that **omission cannot switch it off**. A
workflow built before the setting existed, or by someone who never scrolled to
it, still discloses. Only a deliberate `False` opts out.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from api.services.workflow import pipecat_engine as engine_module
from api.services.workflow.pipecat_engine import PipecatEngine


def _engine(
    *,
    disclosure_enabled=None,
    disclosure_text=None,
    greeting=None,
    greeting_type=None,
    is_voice=True,
):
    """An engine with one start node, and everything else stubbed."""
    node = MagicMock()
    node.recording_disclosure_enabled = disclosure_enabled
    node.recording_disclosure = disclosure_text
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
    return built, node


@pytest.fixture(autouse=True)
def platform_default(monkeypatch):
    monkeypatch.setattr(engine_module, "RECORDING_DISCLOSURE_ENABLED", True)
    monkeypatch.setattr(
        engine_module,
        "RECORDING_DISCLOSURE_TEXT",
        "This call is recorded for quality and training purposes.",
    )


class TestOmissionDoesNotDisableIt:
    def test_a_workflow_that_never_set_it_still_discloses(self):
        """Every workflow built before this existed. If silence meant off, none
        of them would ever disclose and nobody would notice."""
        built, _ = _engine(disclosure_enabled=None)

        assert built.resolve_recording_disclosure("start") == (
            "This call is recorded for quality and training purposes."
        )

    def test_only_an_explicit_false_opts_out(self):
        built, _ = _engine(disclosure_enabled=False)

        assert built.resolve_recording_disclosure("start") is None

    def test_the_platform_default_can_be_switched_off_globally(self, monkeypatch):
        monkeypatch.setattr(engine_module, "RECORDING_DISCLOSURE_ENABLED", False)
        built, _ = _engine(disclosure_enabled=None)

        assert built.resolve_recording_disclosure("start") is None

    def test_an_explicit_true_survives_the_global_default_being_off(self, monkeypatch):
        """A workflow that asked for it gets it, whatever the platform does."""
        monkeypatch.setattr(engine_module, "RECORDING_DISCLOSURE_ENABLED", False)
        built, _ = _engine(disclosure_enabled=True)

        assert built.resolve_recording_disclosure("start") is not None


class TestOnlyCallsDisclose:
    """A text chat is not a call. "This call is recorded" is untrue there, and
    boilerplate that is visibly wrong is how people learn to ignore
    disclosures — a written interface has a page to say it on properly."""

    def test_a_text_chat_says_nothing(self):
        built, _ = _engine(disclosure_enabled=True, is_voice=False)

        assert built.resolve_recording_disclosure("start") is None

    def test_even_an_explicit_opt_in_does_not_make_a_chat_announce_a_call(self):
        built, _ = _engine(
            disclosure_enabled=True,
            disclosure_text="This call is recorded.",
            is_voice=False,
        )

        assert built.resolve_recording_disclosure("start") is None

    def test_voice_is_the_default_so_forgetting_still_discloses(self):
        """The flag is set by the text-chat runner. Everything else — phone,
        WebRTC, anything added later — gets the disclosure without having to
        remember to ask for it."""
        built, _ = _engine(disclosure_enabled=None)

        assert built.resolve_recording_disclosure("start") is not None


class TestWording:
    def test_a_workflow_can_supply_its_own(self):
        built, _ = _engine(
            disclosure_enabled=True,
            disclosure_text="Heads up, we record calls.",
        )

        assert (
            built.resolve_recording_disclosure("start") == "Heads up, we record calls."
        )

    def test_blank_wording_falls_back_to_the_default(self):
        """An empty string must not silently mean "say nothing" — that is an
        opt-out through a text box rather than the switch."""
        built, _ = _engine(disclosure_enabled=True, disclosure_text="   ")

        assert built.resolve_recording_disclosure("start") == (
            "This call is recorded for quality and training purposes."
        )

    def test_template_variables_are_rendered(self):
        """So a customer can name the company doing the calling."""
        built, _ = _engine(
            disclosure_enabled=True,
            disclosure_text="{{company}} records calls for quality.",
        )
        built._call_context_vars = {"company": "Acme"}

        assert built.resolve_recording_disclosure("start") == (
            "Acme records calls for quality."
        )


@pytest.mark.asyncio
class TestItIsSpoken:
    async def test_the_disclosure_is_queued_as_speech(self):
        built, _ = _engine(disclosure_enabled=True)

        said = await built._speak_recording_disclosure("start")

        assert said
        built.task.queue_frame.assert_awaited_once()
        frame = built.task.queue_frame.call_args[0][0]
        assert "recorded" in frame.text

    async def test_it_is_committed_to_the_llm_context(self):
        """Without this the model does not know the disclosure was made, and
        can contradict it or repeat it."""
        built, _ = _engine(disclosure_enabled=True)

        await built._speak_recording_disclosure("start")

        frame = built.task.queue_frame.call_args[0][0]
        assert frame.append_to_context is True

    async def test_nothing_is_queued_when_disabled(self):
        built, _ = _engine(disclosure_enabled=False)

        said = await built._speak_recording_disclosure("start")

        assert not said
        built.task.queue_frame.assert_not_awaited()

    async def test_it_precedes_the_greeting(self):
        """It has to be the first thing heard, not an afterthought once the
        agent has already started talking."""
        built, _ = _engine(
            disclosure_enabled=True, greeting="Hi there!", greeting_type="text"
        )

        await built.queue_node_opening(node_id="start", previous_node_id=None)

        spoken = [call.args[0].text for call in built.task.queue_frame.await_args_list]
        assert len(spoken) == 2
        assert "recorded" in spoken[0]
        assert spoken[1] == "Hi there!"

    async def test_it_is_said_even_with_no_greeting_configured(self):
        """The case a text-only implementation would miss: an agent that opens
        with an LLM generation still has to disclose."""
        built, _ = _engine(disclosure_enabled=True, greeting=None)

        await built.queue_node_opening(node_id="start", previous_node_id=None)

        spoken = [call.args[0].text for call in built.task.queue_frame.await_args_list]
        assert any("recorded" in text for text in spoken)

    async def test_it_is_not_repeated_on_a_later_node(self):
        """Once per call, not once per node transition."""
        built, _ = _engine(
            disclosure_enabled=True, greeting="Hi!", greeting_type="text"
        )

        await built.queue_node_opening(node_id="start", previous_node_id="other")

        spoken = [call.args[0].text for call in built.task.queue_frame.await_args_list]
        assert not any("recorded" in text for text in spoken)
