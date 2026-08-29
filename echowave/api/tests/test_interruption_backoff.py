"""The pause after a caller cuts in, and the things it must not do.

Holding frames on the path the agent speaks through is the risky half of this
feature, so the tests are mostly about what still gets through: interruptions,
ordering, and an agent nobody configured it on.
"""

import asyncio

import pytest
from pipecat.frames.frames import (
    Frame,
    InterruptionFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
)
from pipecat.processors.frame_processor import FrameDirection

from api.services.pipecat.interruption_backoff import InterruptionBackoff


async def _backoff(seconds: float = 0.2) -> tuple[InterruptionBackoff, list[Frame]]:
    """A processor wired up enough to run, with its output captured.

    Only a task manager is needed: the release timer is created through it, and
    without one the processor raises rather than holding anything.
    """
    from pipecat.utils.asyncio.task_manager import TaskManager
    from pipecat.utils.base_object import BaseObject

    processor = InterruptionBackoff(seconds=seconds)
    # BaseObject.setup takes the task manager directly. FrameProcessor.setup
    # wants a whole pipeline setup object, and nothing here needs one.
    await BaseObject.setup(processor, TaskManager())

    pushed: list[Frame] = []

    async def capture(frame, direction=FrameDirection.DOWNSTREAM):
        pushed.append(frame)

    processor.push_frame = capture
    # The base class refuses frames before StartFrame; this suite is about what
    # this processor decides, not about pipeline startup.
    processor._FrameProcessor__started = True
    return processor, pushed


async def _send(processor, frame):
    await processor.process_frame(frame, FrameDirection.DOWNSTREAM)


def _text(index: int) -> LLMTextFrame:
    return LLMTextFrame(text=f"part {index}")


class TestWithNoInterruptionNothingChanges:
    @pytest.mark.asyncio
    async def test_frames_pass_straight_through(self):
        processor, pushed = await _backoff()

        await _send(processor, _text(1))
        await _send(processor, _text(2))

        assert [f.text for f in pushed] == ["part 1", "part 2"]


class TestAfterAnInterruption:
    @pytest.mark.asyncio
    async def test_the_reply_is_held(self):
        processor, pushed = await _backoff(seconds=5)

        await _send(processor, InterruptionFrame())
        pushed.clear()
        await _send(processor, _text(1))

        assert pushed == [], "the reply should be waiting, not spoken"

    @pytest.mark.asyncio
    async def test_it_is_released_once_the_pause_is_over(self):
        processor, pushed = await _backoff(seconds=0.05)

        await _send(processor, InterruptionFrame())
        pushed.clear()
        await _send(processor, _text(1))
        await asyncio.sleep(0.25)

        assert [f.text for f in pushed] == ["part 1"]

    @pytest.mark.asyncio
    async def test_a_response_is_released_in_the_order_it_arrived(self):
        """Speech synthesis reads a reply as a sequence bounded by its start and
        end frames. Released out of order it is assembled wrong, not late."""
        processor, pushed = await _backoff(seconds=0.05)

        await _send(processor, InterruptionFrame())
        pushed.clear()
        await _send(processor, LLMFullResponseStartFrame())
        await _send(processor, _text(1))
        await _send(processor, _text(2))
        await _send(processor, LLMFullResponseEndFrame())
        await asyncio.sleep(0.25)

        assert [type(f).__name__ for f in pushed] == [
            "LLMFullResponseStartFrame",
            "LLMTextFrame",
            "LLMTextFrame",
            "LLMFullResponseEndFrame",
        ]

    @pytest.mark.asyncio
    async def test_the_pause_ends_and_later_frames_are_not_held(self):
        processor, pushed = await _backoff(seconds=0.05)

        await _send(processor, InterruptionFrame())
        await asyncio.sleep(0.15)
        pushed.clear()
        await _send(processor, _text(9))

        assert [f.text for f in pushed] == ["part 9"]


class TestWhatMustNeverBeHeld:
    @pytest.mark.asyncio
    async def test_a_second_interruption_gets_through_during_the_pause(self):
        """The reason this buffers rather than sleeping. A sleep here stalls the
        processor's queue, so the second interruption would wait out the pause
        caused by the first."""
        processor, pushed = await _backoff(seconds=5)

        await _send(processor, InterruptionFrame())
        pushed.clear()
        await _send(processor, _text(1))
        await _send(processor, InterruptionFrame())

        assert [type(f).__name__ for f in pushed] == ["InterruptionFrame"]

    @pytest.mark.asyncio
    async def test_the_interruption_itself_is_passed_on(self):
        processor, pushed = await _backoff(seconds=5)

        await _send(processor, InterruptionFrame())

        assert [type(f).__name__ for f in pushed] == ["InterruptionFrame"]

    @pytest.mark.asyncio
    async def test_held_speech_is_dropped_when_the_caller_cuts_in_again(self):
        """It answers something they interrupted to change."""
        processor, pushed = await _backoff(seconds=0.05)

        await _send(processor, InterruptionFrame())
        await _send(processor, _text(1))
        await _send(processor, InterruptionFrame())
        pushed.clear()
        await asyncio.sleep(0.25)

        assert pushed == []


class TestItIsAbsentUnlessAskedFor:
    def test_zero_seconds_builds_no_processor(self):
        from api.services.pipecat.run_pipeline import _create_interruption_backoff

        assert _create_interruption_backoff({}) is None
        assert _create_interruption_backoff({"interruption_backoff_secs": 0}) is None
        assert _create_interruption_backoff({"interruption_backoff_secs": None}) is None

    def test_a_configured_value_builds_one(self):
        from api.services.pipecat.run_pipeline import _create_interruption_backoff

        processor = _create_interruption_backoff({"interruption_backoff_secs": 0.8})

        assert isinstance(processor, InterruptionBackoff)
        assert processor._seconds == 0.8

    def test_the_pipeline_omits_it_when_there_is_none(self):
        import inspect

        from api.services.pipecat import pipeline_builder

        source = inspect.getsource(pipeline_builder.build_pipeline)

        assert "*([interruption_backoff] if interruption_backoff else [])" in source, (
            "An agent that never asked for this must run the frames it ran before."
        )
