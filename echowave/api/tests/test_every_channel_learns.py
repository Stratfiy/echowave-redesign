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
