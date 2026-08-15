"""What resets the idle escalation, and why the answer cannot be one event.

The second idle timeout **disconnects the call**. That makes the reset the
load-bearing half of this feature: if the retry count does not return to zero
when the caller speaks, a caller who is mid-sentence is hung up on roughly
twenty seconds into every call.

The pipeline used to reset on ``on_user_turn_started`` alone. pipecat does not
emit user-turn frames at all for a realtime speech-to-speech service — its own
source says so, in the comment in ``llm_response_universal.py`` that seeds the
turn timestamp from the transcript "because ``_on_user_turn_started`` never
fires in that mode". On those pipelines the counter could only ever go up.

So the reset is also wired to ``on_user_turn_message_added``, which fires
whenever a user message reaches the context. That is the honest definition of
"this caller is not idle", and it is true in both cascade and realtime modes.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from pipecat.utils.enums import EndTaskReason

from api.services.workflow.pipecat_engine_callbacks import UserIdleHandler


@pytest.fixture
def engine():
    engine = MagicMock()
    engine.end_call_with_reason = AsyncMock()
    return engine


@pytest.fixture
def aggregator():
    aggregator = MagicMock()
    aggregator.push_frame = AsyncMock()
    return aggregator


class TestTheEscalation:
    async def test_the_first_timeout_only_asks_whether_they_are_there(
        self, engine, aggregator
    ):
        handler = UserIdleHandler(engine)

        await handler.handle_idle(aggregator)

        aggregator.push_frame.assert_awaited_once()
        engine.end_call_with_reason.assert_not_awaited()

    async def test_the_second_timeout_disconnects(self, engine, aggregator):
        """The reason the reset matters as much as the timeout does."""
        handler = UserIdleHandler(engine)

        await handler.handle_idle(aggregator)
        await handler.handle_idle(aggregator)

        engine.end_call_with_reason.assert_awaited_once_with(
            EndTaskReason.USER_IDLE_MAX_DURATION_EXCEEDED.value
        )


class TestTheFirstNudgeCannotHangUp:
    """The contract is two strikes, and the code has to enforce both halves.

    The first nudge runs a full LLM generation with the node's tools still
    advertised — `end_call` among them on any node with an end edge. A model
    told "the user has been quiet" and handed a hang-up tool will sometimes
    use it, which collapses the escalation into one strike and disconnects a
    caller roughly ten seconds after the greeting. Observed in the greeting
    integration trace: idle attempt 1, then end_call, 10.0s after the bot
    stopped speaking.
    """

    async def test_the_first_prompt_forbids_ending_the_call(self, engine, aggregator):
        handler = UserIdleHandler(engine)

        await handler.handle_idle(aggregator)

        frame = aggregator.push_frame.await_args.args[0]
        content = frame.messages[0]["content"].lower()
        assert "do not end the call" in content
        assert "do not call any tool" in content

    async def test_the_second_prompt_is_the_one_that_announces_it(
        self, engine, aggregator
    ):
        handler = UserIdleHandler(engine)

        await handler.handle_idle(aggregator)
        await handler.handle_idle(aggregator)

        frame = aggregator.push_frame.await_args.args[0]
        content = frame.messages[0]["content"].lower()
        assert "disconnecting" in content


class TestTheReset:
    async def test_a_reset_between_timeouts_saves_the_call(self, engine, aggregator):
        """A caller who speaks after the "are you still there" prompt must get
        the full allowance again, not one timeout from a disconnect."""
        handler = UserIdleHandler(engine)

        await handler.handle_idle(aggregator)
        handler.reset()
        await handler.handle_idle(aggregator)

        engine.end_call_with_reason.assert_not_awaited()

    async def test_without_a_reset_the_count_only_climbs(self, engine, aggregator):
        """The realtime failure, stated plainly: no reset means the third
        timeout is a disconnect no matter how much the caller said."""
        handler = UserIdleHandler(engine)

        await handler.handle_idle(aggregator)
        await handler.handle_idle(aggregator)

        engine.end_call_with_reason.assert_awaited_once()


class TestTheWiring:
    """Both events have to be subscribed, or the reset is mode-dependent.

    Asserted against the pipeline builder's source rather than by running a
    pipeline: the point is which events are subscribed at all, and a test that
    needs a live pipeline to answer that is a test nobody runs when it breaks.
    """

    @staticmethod
    def _source() -> str:
        from pathlib import Path

        import api.services.pipecat.run_pipeline as run_pipeline

        return Path(run_pipeline.__file__).read_text()

    def test_the_idle_event_is_subscribed(self):
        assert 'event_handler("on_user_turn_idle")' in self._source()

    def test_the_turn_event_resets(self):
        assert 'event_handler("on_user_turn_started")' in self._source()

    def test_the_message_event_also_resets(self):
        """The one that works on a realtime pipeline, where no user-turn frame
        is ever emitted."""
        assert 'event_handler("on_user_turn_message_added")' in self._source()

    def test_every_reset_subscription_actually_calls_reset(self):
        source = self._source()
        for event in ("on_user_turn_started", "on_user_turn_message_added"):
            start = source.index(f'event_handler("{event}")')
            body = source[start : start + 400]
            assert "user_idle_handler.reset()" in body, (
                f"{event} is subscribed but does not reset the escalation"
            )


class TestPipecatStillEmitsWhatWeSubscribeTo:
    """A rename upstream would silently disable the reset.

    pipecat registers its event names at runtime and raises on an unknown one
    only when the handler is attached, which happens deep inside pipeline
    construction — far from anything that would point at this. Checking the
    registration list directly turns that into a failure that names itself.
    """

    @staticmethod
    def _aggregator_source() -> str:
        from pathlib import Path

        from pipecat.processors.aggregators import llm_response_universal

        return Path(llm_response_universal.__file__).read_text()

    @pytest.mark.parametrize(
        "event",
        [
            "on_user_turn_idle",
            "on_user_turn_started",
            "on_user_turn_message_added",
        ],
    )
    def test_the_event_is_still_registered_upstream(self, event):
        assert f'_register_event_handler("{event}")' in self._aggregator_source(), (
            f"pipecat no longer registers {event}; the idle reset depends on it"
        )
