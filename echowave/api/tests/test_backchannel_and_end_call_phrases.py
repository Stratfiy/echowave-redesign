"""The two smallest things that make an agent sound like a person.

A filler while a slow reply starts, so the caller does not hear a dropped
line; and hanging up when the caller says goodbye, without a model turn in
between.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from pipecat.frames.frames import (
    LLMTextFrame,
    TranscriptionFrame,
    TTSSpeakFrame,
    UserStartedSpeakingFrame,
    UserStoppedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameDirection
from pipecat.utils.enums import EndTaskReason

from api.services.pipecat.backchannel import (
    DEFAULT_PHRASES,
    Backchannel,
    backchannel_settings,
)
from api.services.pipecat.end_call_phrases import (
    EndCallPhraseWatcher,
    end_call_farewell,
    end_call_phrases,
    matches,
)
from api.services.workflow.pipecat_engine import PipecatEngine


def _spoken(processor) -> list[str]:
    return [
        call.args[0].text
        for call in processor.push_frame.await_args_list
        if isinstance(call.args[0], TTSSpeakFrame)
    ]


class TestBackchannelSettings:
    def test_off_unless_switched_on(self):
        assert backchannel_settings(None) is None
        assert backchannel_settings({}) is None
        assert backchannel_settings({"backchannel_configuration": {}}) is None
        assert (
            backchannel_settings({"backchannel_configuration": {"enabled": False}})
            is None
        )

    def test_defaults_and_clamping(self):
        assert backchannel_settings(
            {"backchannel_configuration": {"enabled": True}}
        ) == (1.2, list(DEFAULT_PHRASES))
        delay, phrases = backchannel_settings(
            {
                "backchannel_configuration": {
                    "enabled": True,
                    "delay_secs": 0.1,
                    "phrases": [" சரி. ", "", 3],
                }
            }
        )
        assert delay == 0.5
        assert phrases == ["சரி."]
        delay, _ = backchannel_settings(
            {"backchannel_configuration": {"enabled": True, "delay_secs": 99}}
        )
        assert delay == 5.0


class TestBackchannel:
    @pytest.mark.asyncio
    async def test_a_slow_reply_gets_a_filler_once(self):
        b = Backchannel(delay_secs=0.05, phrases=["Hmm.", "Okay."])
        b.push_frame = AsyncMock()
        await b.process_frame(UserStoppedSpeakingFrame(), FrameDirection.DOWNSTREAM)
        await asyncio.sleep(0.15)
        assert _spoken(b) == ["Hmm."]
        filler = [
            c.args[0]
            for c in b.push_frame.await_args_list
            if isinstance(c.args[0], TTSSpeakFrame)
        ][0]
        assert filler.append_to_context is False

    @pytest.mark.asyncio
    async def test_a_prompt_reply_gets_no_filler(self):
        b = Backchannel(delay_secs=0.05)
        b.push_frame = AsyncMock()
        await b.process_frame(UserStoppedSpeakingFrame(), FrameDirection.DOWNSTREAM)
        await b.process_frame(LLMTextFrame("Sure"), FrameDirection.DOWNSTREAM)
        await asyncio.sleep(0.1)
        assert _spoken(b) == []

    @pytest.mark.asyncio
    async def test_a_caller_who_keeps_talking_gets_no_filler(self):
        b = Backchannel(delay_secs=0.05)
        b.push_frame = AsyncMock()
        await b.process_frame(UserStoppedSpeakingFrame(), FrameDirection.DOWNSTREAM)
        await b.process_frame(UserStartedSpeakingFrame(), FrameDirection.DOWNSTREAM)
        await asyncio.sleep(0.1)
        assert _spoken(b) == []

    @pytest.mark.asyncio
    async def test_phrases_rotate_across_turns(self):
        b = Backchannel(delay_secs=0.02, phrases=["A", "B"])
        b.push_frame = AsyncMock()
        for _ in range(3):
            await b.process_frame(UserStoppedSpeakingFrame(), FrameDirection.DOWNSTREAM)
            await asyncio.sleep(0.05)
        assert _spoken(b) == ["A", "B", "A"]
        assert b.spoken == 3


class TestMatching:
    def test_exact_and_short_contains(self):
        phrases = ["bye", "that's all", "சரி வைக்கிறேன்"]
        assert matches("Bye!", phrases) == "bye"
        assert matches("okay thanks, bye.", phrases) == "bye"
        assert matches("That's all", phrases) == "that's all"
        assert matches("சரி வைக்கிறேன்.", phrases) == "சரி வைக்கிறேன்"

    def test_a_long_sentence_has_to_be_the_phrase(self):
        phrases = ["bye"]
        assert matches("bye the way I have one more question for you", phrases) is None
        assert matches("I will say bye after you confirm my booking", phrases) is None

    def test_partial_words_do_not_match(self):
        assert matches("goodbye", ["bye"]) is None
        assert matches("byebye", ["bye"]) is None

    def test_config_readers(self):
        assert end_call_phrases(None) == []
        assert end_call_phrases({"end_call_phrases": "bye"}) == []
        assert end_call_phrases({"end_call_phrases": [" bye ", "", 1]}) == ["bye"]
        assert end_call_farewell({"end_call_farewell": "  "}) is None
        assert end_call_farewell({"end_call_farewell": "Take care."}) == "Take care."


def _transcription(text: str) -> TranscriptionFrame:
    return TranscriptionFrame(text=text, user_id="caller", timestamp="now")


class TestWatcher:
    @pytest.mark.asyncio
    async def test_a_goodbye_ends_the_call_and_is_still_forwarded(self):
        on_match = AsyncMock()
        w = EndCallPhraseWatcher(phrases=["bye"], on_match=on_match)
        w.push_frame = AsyncMock()
        frame = _transcription("okay bye")
        await w.process_frame(frame, FrameDirection.DOWNSTREAM)
        on_match.assert_awaited_once_with("bye")
        w.push_frame.assert_awaited_once()
        assert w.push_frame.await_args.args[0] is frame

    @pytest.mark.asyncio
    async def test_only_the_first_match_fires(self):
        on_match = AsyncMock()
        w = EndCallPhraseWatcher(phrases=["bye"], on_match=on_match)
        w.push_frame = AsyncMock()
        await w.process_frame(_transcription("bye"), FrameDirection.DOWNSTREAM)
        await w.process_frame(_transcription("bye bye"), FrameDirection.DOWNSTREAM)
        assert on_match.await_count == 1

    @pytest.mark.asyncio
    async def test_other_speech_passes_untouched(self):
        on_match = AsyncMock()
        w = EndCallPhraseWatcher(phrases=["bye"], on_match=on_match)
        w.push_frame = AsyncMock()
        await w.process_frame(
            _transcription("is the clinic open on Sunday"), FrameDirection.DOWNSTREAM
        )
        on_match.assert_not_awaited()
        assert w.matched is None


def _engine():
    workflow = MagicMock()
    workflow.start_node_id = "start"
    workflow.nodes = {}
    engine = PipecatEngine(
        workflow=workflow,
        call_context_vars={},
        task=MagicMock(queue_frame=AsyncMock()),
    )
    engine.end_call_with_reason = AsyncMock()
    return engine


class TestEngineHangUp:
    @pytest.mark.asyncio
    async def test_farewell_plays_then_the_call_ends(self):
        engine = _engine()
        await engine.end_call_on_phrase("bye", "Thanks for calling. Bye!")
        queued = engine.task.queue_frame.await_args.args[0]
        assert isinstance(queued, TTSSpeakFrame)
        assert queued.text == "Thanks for calling. Bye!"
        engine.end_call_with_reason.assert_awaited_once_with(
            EndTaskReason.USER_HANGUP.value, abort_immediately=False
        )
        assert engine._gathered_context["call_disposition"] == "caller_said_goodbye"
        assert "end_call_phrase" in engine._gathered_context["call_tags"]

    @pytest.mark.asyncio
    async def test_no_farewell_ends_now(self):
        engine = _engine()
        await engine.end_call_on_phrase("bye", None)
        engine.task.queue_frame.assert_not_awaited()
        engine.end_call_with_reason.assert_awaited_once_with(
            EndTaskReason.USER_HANGUP.value, abort_immediately=True
        )
