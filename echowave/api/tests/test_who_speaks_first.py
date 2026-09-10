"""Who opens the call, and whether the call keeps its audio.

Two per-agent switches every competitor ships and we did not:

* **Who speaks first.** An inbound line where people call in already
  talking, or an outbound call answered with "hello?", wants the agent to
  wait. But a caller who says nothing must still get a greeting, or two
  parties sit waiting for each other and the caller hangs up.
* **Recording.** Off means no audio kept — and, because the disclosure is
  only true when there is a recording, no "this call is recorded" either.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from pipecat.frames.frames import UserStartedSpeakingFrame
from pipecat.processors.frame_processor import FrameDirection

from api.services.pipecat.call_recording import recording_enabled
from api.services.pipecat.pipeline_engine_callbacks_processor import (
    PipelineEngineCallbacksProcessor,
)
from api.services.workflow import pipecat_engine as engine_module
from api.services.workflow.pipecat_engine import PipecatEngine


def _engine(*, speaks_first="agent", wait=None, is_voice=True, call_recorded=True):
    node = MagicMock()
    node.id = "start"
    node.speaks_first = speaks_first
    node.speaks_first_wait_secs = wait
    node.recording_disclosure_enabled = None
    node.recording_disclosure = None
    node.greeting = "Hello, you have reached the clinic."
    node.greeting_type = "text"
    node.greeting_recording_id = None

    workflow = MagicMock()
    workflow.start_node_id = "start"
    workflow.nodes = {"start": node}

    engine = PipecatEngine(
        workflow=workflow,
        call_context_vars={},
        task=MagicMock(queue_frame=AsyncMock()),
        is_voice=is_voice,
        call_recorded=call_recorded,
    )
    engine.queue_node_opening = AsyncMock(return_value="greeting")
    return engine, node


@pytest.fixture(autouse=True)
def platform_default(monkeypatch):
    monkeypatch.setattr(engine_module, "RECORDING_DISCLOSURE_ENABLED", True)
    monkeypatch.setattr(
        engine_module, "RECORDING_DISCLOSURE_TEXT", "This call is recorded."
    )


class TestAgentFirst:
    @pytest.mark.asyncio
    async def test_the_default_opens_immediately(self):
        engine, _ = _engine()
        await engine.open_call()
        engine.queue_node_opening.assert_awaited_once_with(
            node_id="start", previous_node_id=None, generate_if_no_greeting=True
        )

    def test_a_node_that_never_heard_of_the_setting_is_agent_first(self):
        engine, node = _engine()
        del node.speaks_first
        assert engine.caller_speaks_first() is False

    def test_no_opening_note_in_the_prompt(self):
        engine, node = _engine()
        assert engine._opening_notes_for(node) is None


class TestCallerFirst:
    @pytest.mark.asyncio
    async def test_nothing_is_said_when_the_line_comes_up(self):
        engine, _ = _engine(speaks_first="caller", wait=0.2)
        await engine.open_call()
        engine.queue_node_opening.assert_not_awaited()
        engine._caller_first_wait_task.cancel()

    @pytest.mark.asyncio
    async def test_a_silent_caller_gets_the_greeting_after_the_wait(self):
        engine, _ = _engine(speaks_first="caller", wait=0.05)
        await engine.open_call()
        await asyncio.sleep(0.15)
        engine.queue_node_opening.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_a_caller_who_speaks_cancels_the_greeting(self):
        engine, _ = _engine(speaks_first="caller", wait=0.1)
        await engine.open_call()
        await engine.handle_user_started_speaking()
        await asyncio.sleep(0.2)
        engine.queue_node_opening.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_caller_who_spoke_before_the_line_settled_is_not_greeted(self):
        engine, _ = _engine(speaks_first="caller", wait=0.05)
        await engine.handle_user_started_speaking()
        await engine.open_call()
        await asyncio.sleep(0.1)
        engine.queue_node_opening.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_speaking_later_does_not_disturb_a_greeting_already_playing(self):
        engine, _ = _engine(speaks_first="caller", wait=0.02)
        await engine.open_call()
        await asyncio.sleep(0.08)
        await engine.handle_user_started_speaking()
        engine.queue_node_opening.assert_awaited_once()

    def test_text_chat_always_greets(self):
        engine, _ = _engine(speaks_first="caller", is_voice=False)
        assert engine.caller_speaks_first() is False

    def test_wait_defaults_and_is_capped(self):
        assert _engine(speaks_first="caller")[0].caller_first_wait_secs() == 3.0
        assert _engine(speaks_first="caller", wait=0)[0].caller_first_wait_secs() == 3.0
        assert (
            _engine(speaks_first="caller", wait="x")[0].caller_first_wait_secs() == 3.0
        )
        assert _engine(speaks_first="caller", wait=5)[0].caller_first_wait_secs() == 5.0
        assert (
            _engine(speaks_first="caller", wait=60)[0].caller_first_wait_secs() == 15.0
        )

    def test_the_prompt_tells_the_model_to_answer_and_disclose(self):
        engine, node = _engine(speaks_first="caller")
        note = engine._opening_notes_for(node)
        assert "lets the caller speak first" in note
        assert "This call is recorded." in note

    def test_the_prompt_skips_the_disclosure_when_nothing_is_recorded(self):
        engine, node = _engine(speaks_first="caller", call_recorded=False)
        note = engine._opening_notes_for(node)
        assert "lets the caller speak first" in note
        assert "recorded" not in note


class TestRecordingSwitch:
    def test_on_unless_switched_off(self):
        assert recording_enabled(None) is True
        assert recording_enabled({}) is True
        assert recording_enabled({"recording_configuration": {}}) is True
        assert recording_enabled({"recording_configuration": {"enabled": True}}) is True
        assert recording_enabled({"recording_configuration": "junk"}) is True
        assert (
            recording_enabled({"recording_configuration": {"enabled": False}}) is False
        )

    def test_no_recording_means_no_disclosure(self):
        engine, _ = _engine(call_recorded=False)
        assert engine.resolve_recording_disclosure("start") is None

    def test_a_recorded_call_still_discloses(self):
        engine, _ = _engine(call_recorded=True)
        assert engine.resolve_recording_disclosure("start") == "This call is recorded."


@pytest.mark.asyncio
async def test_the_callbacks_processor_reports_user_speech():
    heard = AsyncMock()
    processor = PipelineEngineCallbacksProcessor(user_started_speaking_callback=heard)
    processor.push_frame = AsyncMock()
    await processor.process_frame(UserStartedSpeakingFrame(), FrameDirection.DOWNSTREAM)
    heard.assert_awaited_once()
