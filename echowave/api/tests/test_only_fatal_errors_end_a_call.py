"""A provider complaining is not a reason to hang up on a customer.

Pipecat fires ``on_pipeline_error`` for *every* ErrorFrame and then decides for
itself what to do about it: ``fatal`` cancels the pipeline, anything else is a
warning and the bot keeps talking. ``FrameProcessor.push_error`` defaults to
``fatal=False``, so most of what reaches the handler is a service reporting a
problem it expects to survive.

Our handler ended the call on all of them. Sarvam answered one websocket
message with a complaint, pipecat would have shrugged, and the customer's call
was cut at 0s with the disposition ``pipeline_error`` — the run recording one
bot turn, because the greeting had already been produced.

The test drives the registered handler rather than a helper, because the
regression was never in a helper: the branch that decides is inside the
handler, and a test of anything else would have passed throughout.
"""

from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import AsyncMock, patch

import pytest


@dataclass
class _Frame:
    """Shaped like pipecat's ErrorFrame for the two fields that matter."""

    error: str
    fatal: bool = False


class _CapturingTask:
    """Collects the handlers ``register_event_handlers`` decorates.

    A stand-in for PipelineWorker: registration only stores functions, so
    nothing here needs a running pipeline.
    """

    def __init__(self):
        self.handlers: dict[str, object] = {}
        self.user_bot_latency_observer = None

    def event_handler(self, name: str):
        def decorator(fn):
            self.handlers[name] = fn
            return fn

        return decorator


async def _fire(frame: _Frame):
    """Run the real ``on_pipeline_error`` against a frame, and report what it did.

    Returns ``(ended_the_call, recorded_a_failure)``.
    """
    from api.services.pipecat import event_handlers

    task = _CapturingTask()

    engine = AsyncMock()
    transport = _CapturingTask()

    with (
        patch.object(event_handlers, "_record_pipeline_error", AsyncMock()),
        patch.object(event_handlers, "db_client") as db_client,
        patch.object(event_handlers, "circuit_breaker") as breaker,
        patch.object(event_handlers, "_capture_call_event", AsyncMock()),
    ):
        db_client.get_workflow_run_by_id = AsyncMock(return_value=None)
        breaker.record_and_evaluate = AsyncMock()

        event_handlers.register_event_handlers(
            task=task,
            transport=transport,
            workflow_run_id=1,
            engine=engine,
            audio_buffer=AsyncMock(),
            in_memory_logs_buffer=AsyncMock(),
            transcript_log_coordinator=AsyncMock(),
            pipeline_metrics_aggregator=AsyncMock(),
        )

        handler = task.handlers["on_pipeline_error"]
        await handler(task, frame)

        return engine.end_call_with_reason.called, breaker.record_and_evaluate.called


@pytest.mark.asyncio
class TestFatalityDecidesWhetherTheCallEnds:
    async def test_a_non_fatal_error_does_not_end_the_call(self):
        """The regression, in one line.

        Sarvam's message came through ``push_error`` with the default
        ``fatal=False``. Pipecat logs those and carries on.
        """
        ended, _ = await _fire(
            _Frame(error="TTS Error: Input parameters has to be a valid dictionary")
        )

        assert not ended

    async def test_a_non_fatal_error_is_not_a_campaign_failure(self):
        """A campaign's circuit breaker exists to stop a run that is genuinely
        broken. Counting calls that completed — and were billed — would trip it
        on a working campaign."""
        _, recorded = await _fire(_Frame(error="TTS Error: something recoverable"))

        assert not recorded

    async def test_a_fatal_error_still_ends_the_call(self):
        """The other half. A service saying it cannot continue is exactly when
        hanging up is right, and that path is unchanged."""
        ended, _ = await _fire(_Frame(error="Fatal: transport gone", fatal=True))

        assert ended
