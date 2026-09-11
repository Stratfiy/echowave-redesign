"""How many times a quiet caller is asked before the call is ended.

Run 307 on the Narayani agent ended with `user_idle_max_duration_exceeded`
on a caller who had not said a word yet, on a greeting that names five
languages and takes a while to take in. One nudge and goodbye is not how a
person behaves at a desk, and it is not enough time.

These drive the handler directly rather than through a pipeline: the
sequence is the whole behaviour, and a pipeline test of it is slow and
tells you less about which turn did what.
"""

from unittest.mock import AsyncMock

import pytest

from api.services.workflow.pipecat_engine_callbacks import (
    NUDGES_BEFORE_HANGING_UP,
    UserIdleHandler,
)


class _Engine:
    """Only what the handler reads."""

    def __init__(self, patience=None):
        self._current_node = type("N", (), {"patience_seconds": patience})()
        self.user_idle_timeout_seconds = 10.0
        self.end_call_with_reason = AsyncMock()


def _spoken(aggregator) -> list[str]:
    """The instructions pushed to the model, in order."""
    said = []
    for call in aggregator.push_frame.await_args_list:
        frame = call.args[0]
        for message in getattr(frame, "messages", []):
            said.append(message["content"])
    return said


@pytest.mark.asyncio
async def test_the_first_silence_is_a_question_not_a_goodbye():
    handler = UserIdleHandler(_Engine())
    aggregator = AsyncMock()

    await handler.handle_idle(aggregator)

    assert "still there" in _spoken(aggregator)[0]
    handler._engine.end_call_with_reason.assert_not_awaited()


@pytest.mark.asyncio
async def test_the_second_silence_asks_again_rather_than_hanging_up():
    """The change. Before this, the second tick ended the call."""
    handler = UserIdleHandler(_Engine())
    aggregator = AsyncMock()

    await handler.handle_idle(aggregator)
    await handler.handle_idle(aggregator)

    handler._engine.end_call_with_reason.assert_not_awaited()
    assert len(_spoken(aggregator)) == 2


@pytest.mark.asyncio
async def test_the_second_ask_is_not_the_first_one_repeated():
    """Saying the same sentence twice is what a machine does."""
    handler = UserIdleHandler(_Engine())
    aggregator = AsyncMock()

    await handler.handle_idle(aggregator)
    await handler.handle_idle(aggregator)

    first, second = _spoken(aggregator)
    assert first != second
    assert "different" in second


@pytest.mark.asyncio
async def test_the_third_silence_ends_the_call():
    """Still bounded. A caller who has gone is not held on the line."""
    handler = UserIdleHandler(_Engine())
    aggregator = AsyncMock()

    for _ in range(NUDGES_BEFORE_HANGING_UP + 1):
        await handler.handle_idle(aggregator)

    handler._engine.end_call_with_reason.assert_awaited_once()


@pytest.mark.asyncio
async def test_speaking_resets_the_count():
    """Two quiet moments in one call, with a sentence between them, is not
    a caller who has gone."""
    handler = UserIdleHandler(_Engine())
    aggregator = AsyncMock()

    await handler.handle_idle(aggregator)
    await handler.handle_idle(aggregator)
    handler.reset()
    await handler.handle_idle(aggregator)

    handler._engine.end_call_with_reason.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_patient_step_still_says_nothing_at_all():
    """The patience rule is untouched: a caller sent to walk to a vehicle is
    not prompted, and the silence does not count as a nudge."""
    handler = UserIdleHandler(_Engine(patience=60))
    aggregator = AsyncMock()

    await handler.handle_idle(aggregator)

    assert _spoken(aggregator) == []
    assert handler._retry_count == 0
