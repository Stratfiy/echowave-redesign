"""A routine teaches the business what a call does.

`learn_from_run` — gaps, confirmations, everything the organisation finds out
about itself — hangs off `PROCESS_WORKFLOW_COMPLETION`. That was enqueued from
exactly two places, both voice: the pipecat teardown and the telephony status
processor. Routines enqueued `RUN_AGENT_ROUTINE` and stopped there; text chat
enqueued nothing.

So of the four `CallDirection` values, the two that ring a phone learned and
the two that do not learned nothing. A routine could fail to reach the same
system every morning for a month and it would never become a gap anybody saw.

Nothing about the machinery was voice-specific. `learn_from_run` takes a run
id, an intent and a set of interactions, none of which imply a phone. Only the
wiring was — the shape AGENTS.md warns about in its opening paragraphs.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from api.tasks.function_names import FunctionNames
from api.tasks.routines import run_agent_routine


def _ctx():
    return {"redis": SimpleNamespace(enqueue_job=AsyncMock())}


class TestARoutineRunIsProcessedLikeAnyOtherRun:
    @pytest.mark.asyncio
    async def test_a_finished_routine_reaches_the_completion_job(self):
        ctx = _ctx()
        with patch(
            "api.services.workflow.routine_runner.run_routine",
            AsyncMock(return_value=336),
        ):
            await run_agent_routine(ctx, 7)

        ctx["redis"].enqueue_job.assert_awaited_once_with(
            FunctionNames.PROCESS_WORKFLOW_COMPLETION, 336
        )

    @pytest.mark.asyncio
    async def test_a_routine_that_never_ran_is_not_processed(self):
        """No credit, or the routine vanished. There is nothing to cost and
        nothing to learn from, and enqueuing anyway would put a completion job
        on a run that does not exist."""
        ctx = _ctx()
        with patch(
            "api.services.workflow.routine_runner.run_routine",
            AsyncMock(return_value=None),
        ):
            await run_agent_routine(ctx, 7)

        ctx["redis"].enqueue_job.assert_not_awaited()


class TestTheEvidenceNumberCannotInflate:
    """`times_seen` is the sentence a suggestion shows a customer — "seven
    callers asked this and no agent could answer". It increments through an
    ON CONFLICT DO UPDATE with no per-run guard, and arq retries, so the same
    run could count twice. A number the product states and that is not true is
    worse than making no suggestion at all.
    """

    @pytest.mark.asyncio
    async def test_a_run_that_already_taught_us_teaches_us_nothing_again(self):
        from api.services.workflow import organisation_learning

        remember = AsyncMock()
        with (
            patch(
                "api.services.workflow.organisation_learning.db_client.claim_run_for_learning",
                AsyncMock(return_value=False),
            ),
            patch(
                "api.services.workflow.organisation_learning.db_client.remember_organisation_observations",
                remember,
            ),
        ):
            written = await organisation_learning.learn_from_run(
                organization_id=42,
                workflow_run_id=336,
                intent="not_understood",
                escalated=True,
            )

        assert written == 0
        remember.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_the_first_caller_to_claim_a_run_does_the_learning(self):
        from api.services.workflow import organisation_learning

        remember = AsyncMock()
        with (
            patch(
                "api.services.workflow.organisation_learning.db_client.claim_run_for_learning",
                AsyncMock(return_value=True),
            ),
            patch(
                "api.services.workflow.organisation_learning.db_client.remember_organisation_observations",
                remember,
            ),
        ):
            await organisation_learning.learn_from_run(
                organization_id=42,
                workflow_run_id=336,
                intent="not_understood",
                escalated=True,
            )

        remember.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_a_run_that_cannot_be_claimed_records_nothing(self):
        """An unclaimable run is one we cannot prove is unlearned. Recording
        nothing is the safe direction: a missing gap comes back the next time
        somebody asks, an inflated count never corrects itself."""
        from api.services.workflow import organisation_learning

        remember = AsyncMock()
        with (
            patch(
                "api.services.workflow.organisation_learning.db_client.claim_run_for_learning",
                AsyncMock(side_effect=RuntimeError("database is away")),
            ),
            patch(
                "api.services.workflow.organisation_learning.db_client.remember_organisation_observations",
                remember,
            ),
        ):
            written = await organisation_learning.learn_from_run(
                organization_id=42,
                workflow_run_id=336,
                intent="not_understood",
                escalated=True,
            )

        assert written == 0
        remember.assert_not_awaited()


class TestABotAnsweringInAChannel:
    """The other half of @mentions, and the reason silence is the one outcome
    that must not happen: the person who typed the message is watching for a
    reply, and nothing at all is indistinguishable from being ignored."""

    @pytest.mark.asyncio
    async def test_an_answer_is_processed_like_any_other_run(self):
        """Not decoration. Until routines were wired, completion was enqueued
        only from the two voice paths -- so a bot that could not answer a
        question in a channel left no gap for the business to see, which is
        the whole point of having asked it there."""
        from api.tasks.routines import answer_channel_message

        ctx = _ctx()
        with patch(
            "api.services.workflow.channel_reply.answer_in_channel",
            AsyncMock(return_value=336),
        ):
            await answer_channel_message(ctx, 7, 3, "chase the suppliers")

        ctx["redis"].enqueue_job.assert_awaited_once_with(
            FunctionNames.PROCESS_WORKFLOW_COMPLETION, 336
        )

    @pytest.mark.asyncio
    async def test_a_bot_that_never_ran_is_not_processed(self):
        from api.tasks.routines import answer_channel_message

        ctx = _ctx()
        with patch(
            "api.services.workflow.channel_reply.answer_in_channel",
            AsyncMock(return_value=None),
        ):
            await answer_channel_message(ctx, 7, 3, "chase the suppliers")

        ctx["redis"].enqueue_job.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_vanished_bot_says_so_rather_than_raising(self):
        """This runs on a worker serving every tenant. One bot throwing would
        take the worker with it."""
        from api.services.workflow.channel_reply import answer_in_channel

        with patch(
            "api.services.workflow.channel_reply.db_client.get_workflow_by_id",
            AsyncMock(return_value=None),
        ):
            assert await answer_in_channel(7, 3, "hello") is None

    @pytest.mark.asyncio
    async def test_no_credit_is_reported_in_the_channel_not_swallowed(self):
        """A bot that stops because the balance ran out looks exactly like one
        that is broken, and the difference is the one thing an operator can
        fix."""
        from api.services.workflow import channel_reply

        recorded = []

        async def _record(**kwargs):
            recorded.append(kwargs)

        with (
            patch(
                "api.services.workflow.channel_reply.db_client.get_workflow_by_id",
                AsyncMock(
                    return_value=SimpleNamespace(
                        id=7, organization_id=42, name="Ops bot"
                    )
                ),
            ),
            patch(
                "api.services.workflow.channel_reply.db_client.create_workflow_run",
                AsyncMock(return_value=SimpleNamespace(id=336)),
            ),
            patch(
                "api.services.workflow.channel_reply.authorize_workflow_run_start",
                AsyncMock(
                    return_value=SimpleNamespace(
                        has_quota=False, error_message="no credit for this run"
                    )
                ),
            ),
            patch.object(channel_reply.agent_timeline, "record", _record),
        ):
            assert await channel_reply.answer_in_channel(7, 3, "hello") is None

        assert recorded, "a bot that could not answer said nothing at all"
        assert recorded[0]["folder_id"] == 3
        assert "no credit" in recorded[0]["summary"]
