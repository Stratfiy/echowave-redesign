"""A text turn says what stopped it, and a busy turn is not cut off.

Seen live on 22 Sept 2026, twice on one bot. A chat message answered on a
model the pipeline could not run produced a turn with no words, no tokens
and no error: the card said "something stopped it" and nothing could say
what. And a prospecting bot asked "hi" made thirteen tool calls in a minute
and was killed by an absolute sixty-second deadline while still working,
its drafts made and its events lost.

Now the pipeline's error frames are recorded on the turn, a turn with an
error and no words fails with the error's own text, the card repeats it,
and the clock runs on silence with a ceiling, not on the wall.
"""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

import pytest

from api.services.workflow import blocked, text_chat_runner


class _Window:
    def __init__(self):
        self.outputs: list[str] = []
        self.pending_context_requests = 0
        self.active_llm_completions = 0
        self.active_assistant_segments = 0
        self.blocking_tool_call_ids: set[str] = set()
        self.frontier_is_idle = True


def _capture():
    return SimpleNamespace(
        activity_count=0, last_activity_at=time.monotonic(), errors=[]
    )


async def _never():
    await asyncio.sleep(3600)


@pytest.mark.asyncio
class TestTheClockRunsOnSilence:
    async def test_a_turn_that_keeps_working_past_the_timeout_is_not_cut_off(self):
        window = _Window()
        capture = _capture()
        runner = asyncio.create_task(_never())

        async def work():
            # Busy for well past the timeout, then quiet.
            window.frontier_is_idle = False
            for _ in range(10):
                await asyncio.sleep(0.05)
                capture.activity_count += 1
                capture.last_activity_at = time.monotonic()
            window.frontier_is_idle = True

        worker = asyncio.create_task(work())
        try:
            await text_chat_runner._wait_for_quiescence(
                capture_processor=capture,
                response_window=window,
                runner_task=runner,
                activity_marker=0,
                # Busy for half a second against a 0.3s silence clock: the old
                # absolute deadline would have cut it off.
                timeout_seconds=0.3,
                hard_cap_seconds=5.0,
            )
        finally:
            worker.cancel()
            runner.cancel()

    async def test_silence_past_the_timeout_is_a_timeout_that_says_so(self):
        window = _Window()
        capture = _capture()
        capture.activity_count = 1
        window.frontier_is_idle = False  # started, then nothing
        runner = asyncio.create_task(_never())
        try:
            with pytest.raises(TimeoutError) as exc:
                await text_chat_runner._wait_for_quiescence(
                    capture_processor=capture,
                    response_window=window,
                    runner_task=runner,
                    activity_marker=0,
                    timeout_seconds=0.1,
                    hard_cap_seconds=5.0,
                )
            assert "nothing happened for 0s" in str(exc.value)
        finally:
            runner.cancel()

    async def test_the_ceiling_ends_a_turn_that_never_settles(self):
        window = _Window()
        capture = _capture()
        runner = asyncio.create_task(_never())

        async def forever_busy():
            window.frontier_is_idle = False
            while True:
                await asyncio.sleep(0.02)
                capture.activity_count += 1
                capture.last_activity_at = time.monotonic()

        worker = asyncio.create_task(forever_busy())
        try:
            with pytest.raises(TimeoutError) as exc:
                await text_chat_runner._wait_for_quiescence(
                    capture_processor=capture,
                    response_window=window,
                    runner_task=runner,
                    activity_marker=0,
                    timeout_seconds=1.0,
                    hard_cap_seconds=0.2,
                )
            assert "ceiling" in str(exc.value)
        finally:
            worker.cancel()
            runner.cancel()


class TestAnErrorIsSaid:
    def test_the_pipelines_complaint_lands_on_the_turn(self):
        window = _Window()
        capture = text_chat_runner._TextChatCaptureProcessor(
            window, context=SimpleNamespace()
        )
        before = capture.activity_count
        capture.note_error("  not_found_error:  model claude-opus-5 \n not found ")
        assert capture.errors == ["not_found_error: model claude-opus-5 not found"]
        assert capture.events[-1]["type"] == "pipeline_error"
        assert capture.activity_count == before + 1

    def test_no_words_and_an_error_is_the_error(self):
        window = _Window()
        capture = _capture()
        capture.errors = ["model not found"]
        with pytest.raises(text_chat_runner.TextChatModelError) as exc:
            text_chat_runner.raise_if_silent_after_error(window, capture)
        assert "model not found" in str(exc.value)

    def test_words_win_over_a_complaint(self):
        window = _Window()
        window.outputs = ["Hello."]
        capture = _capture()
        capture.errors = ["TTS grumbled"]
        text_chat_runner.raise_if_silent_after_error(window, capture)

    def test_the_card_repeats_the_reason(self):
        wall = blocked.classify(
            "could_not",
            {"error": "The model returned an error: not_found_error"},
            workflow_id=43,
        )
        assert wall is not None and wall.reason == "failed"
        assert "not_found_error" in wall.says
        bare = blocked.classify("could_not", {"error": " "}, workflow_id=43)
        assert bare is not None and bare.says.endswith("stopped there.")
