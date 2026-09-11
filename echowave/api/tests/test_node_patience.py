"""Some steps send the caller away, and must not hang up while they are gone.

The idle handler assumes a quiet caller is a caller thinking, and on that
assumption a couple of nudges and a disconnect is right. It is wrong for the
step that says "press 6# on the controller and tell me whether the GPS light
is blinking" — that caller is walking to a vehicle, and the shipped default
gives them ten seconds before the agent starts asking if they are still
there.

So a step can ask for more rope. While the silence it has absorbed is under its
budget the agent says nothing at all: not a softer prompt, nothing. "Are you
still there?" every ten seconds is worse than the silence it interrupts.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from api.services.workflow.pipecat_engine_callbacks import (
    NUDGES_BEFORE_HANGING_UP,
    UserIdleHandler,
)


def _engine(patience=None, tick=10.0):
    engine = MagicMock()
    engine._current_node = MagicMock(patience_seconds=patience)
    engine.user_idle_timeout_seconds = tick
    engine.end_call_with_reason = AsyncMock()
    return engine


def _aggregator():
    aggregator = MagicMock()
    aggregator.push_frame = AsyncMock()
    return aggregator


class TestWithoutPatience:
    """Every workflow that exists today. Nothing about it may change."""

    @pytest.mark.asyncio
    async def test_the_first_silence_asks_if_they_are_there(self):
        handler = UserIdleHandler(_engine(patience=None))
        aggregator = _aggregator()
        await handler.handle_idle(aggregator)
        aggregator.push_frame.assert_awaited_once()
        assert handler._retry_count == 1

    @pytest.mark.asyncio
    async def test_the_second_asks_again_rather_than_hanging_up(self):
        """Deliberately changed: one nudge and goodbye was too few.

        Run 307 ended on a caller who had not spoken yet, on a greeting that
        names five languages. A quiet caller now gets asked twice.
        """
        engine = _engine(patience=None)
        handler = UserIdleHandler(engine)
        aggregator = _aggregator()
        await handler.handle_idle(aggregator)
        await handler.handle_idle(aggregator)
        engine.end_call_with_reason.assert_not_awaited()
        assert aggregator.push_frame.await_count == 2

    @pytest.mark.asyncio
    async def test_the_silence_after_the_last_nudge_ends_the_call(self):
        engine = _engine(patience=None)
        handler = UserIdleHandler(engine)
        aggregator = _aggregator()
        for _ in range(NUDGES_BEFORE_HANGING_UP + 1):
            await handler.handle_idle(aggregator)
        engine.end_call_with_reason.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_a_node_with_zero_patience_behaves_the_same(self):
        handler = UserIdleHandler(_engine(patience=0))
        aggregator = _aggregator()
        await handler.handle_idle(aggregator)
        assert handler._retry_count == 1


class TestWithPatience:
    @pytest.mark.asyncio
    async def test_it_stays_silent_while_within_budget(self):
        """The whole point: no prompt, no strike, no call ended."""
        engine = _engine(patience=60, tick=10.0)
        handler = UserIdleHandler(engine)
        aggregator = _aggregator()

        for _ in range(6):  # 60s of silence, exactly the budget
            await handler.handle_idle(aggregator)

        aggregator.push_frame.assert_not_awaited()
        engine.end_call_with_reason.assert_not_awaited()
        assert handler._retry_count == 0

    @pytest.mark.asyncio
    async def test_it_asks_once_the_budget_is_spent(self):
        engine = _engine(patience=20, tick=10.0)
        handler = UserIdleHandler(engine)
        aggregator = _aggregator()

        await handler.handle_idle(aggregator)
        await handler.handle_idle(aggregator)
        aggregator.push_frame.assert_not_awaited()

        await handler.handle_idle(aggregator)
        aggregator.push_frame.assert_awaited_once()
        assert handler._retry_count == 1

    @pytest.mark.asyncio
    async def test_the_caller_speaking_returns_the_full_budget(self):
        """Someone who came back and spoke is not part-way through waiting."""
        handler = UserIdleHandler(_engine(patience=30, tick=10.0))
        aggregator = _aggregator()
        await handler.handle_idle(aggregator)
        handler.reset()
        assert handler._waited_seconds == 0.0

        for _ in range(3):
            await handler.handle_idle(aggregator)
        aggregator.push_frame.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_patience_is_measured_in_the_configured_tick(self):
        """Raising the agent timeout must not multiply every node's patience."""
        engine = _engine(patience=60, tick=30.0)
        handler = UserIdleHandler(engine)
        aggregator = _aggregator()

        await handler.handle_idle(aggregator)
        await handler.handle_idle(aggregator)
        aggregator.push_frame.assert_not_awaited()

        await handler.handle_idle(aggregator)
        aggregator.push_frame.assert_awaited_once()


class TestItCannotBreakACall:
    @pytest.mark.asyncio
    async def test_a_malformed_patience_falls_back_to_the_default(self):
        """A bad value must not decide how long we hold a call open."""
        handler = UserIdleHandler(_engine(patience="soon"))
        aggregator = _aggregator()
        await handler.handle_idle(aggregator)
        aggregator.push_frame.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_a_negative_patience_is_treated_as_none(self):
        handler = UserIdleHandler(_engine(patience=-5))
        aggregator = _aggregator()
        await handler.handle_idle(aggregator)
        aggregator.push_frame.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_no_current_node_is_not_an_error(self):
        engine = _engine()
        engine._current_node = None
        handler = UserIdleHandler(engine)
        aggregator = _aggregator()
        await handler.handle_idle(aggregator)
        aggregator.push_frame.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_a_missing_tick_falls_back_to_the_shipped_default(self):
        engine = _engine(patience=60)
        engine.user_idle_timeout_seconds = None
        handler = UserIdleHandler(engine)
        aggregator = _aggregator()
        for _ in range(6):
            await handler.handle_idle(aggregator)
        aggregator.push_frame.assert_not_awaited()
