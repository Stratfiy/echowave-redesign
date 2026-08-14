"""Why a call failed, as a field rather than a guess.

"Why did this call fail" was unanswerable across the fleet. ``queued_runs``
records a refusal for the two compliance cases; a run that started and went
wrong recorded nothing, because the state machine has initialized, running and
completed and a call that died is left in whichever it reached.

Two properties matter more than the field existing at all.

**Recording never breaks the caller.** Every call site is already on an error
path. An exception there replaces a diagnosable failure with an undiagnosable
one, so ``record`` swallows its own faults.

**The first reason wins.** A call refused for no credit that then loses its
websocket must read as "no credit". The second failure is a consequence, and
letting it overwrite blames the symptom.

The breakdown groups by *who has to act* rather than by volume, and that is not
cosmetic: ordered by volume, a healthy fleet puts "outside calling hours" at
the top — a refusal that is the system obeying its instructions — and buries
the provider errors somebody has to fix underneath it.
"""

from datetime import UTC, datetime

import pytest

from api.db.billing_dashboard_client import failure_breakdown
from api.enums import RunFailureReason
from api.services.runs import failures


async def _run(async_session, slug, *, organization=None):
    from api.db.models import OrganizationModel, WorkflowModel, WorkflowRunModel

    org = organization
    if org is None:
        org = OrganizationModel(provider_id=f"org-{slug}", quota_decibyl_tokens=0)
        async_session.add(org)
        await async_session.flush()
    workflow = WorkflowModel(name=f"wf-{slug}", organization_id=org.id)
    async_session.add(workflow)
    await async_session.flush()
    run = WorkflowRunModel(
        name=f"run-{slug}",
        workflow_id=workflow.id,
        mode="twilio",
        created_at=datetime.now(UTC),
        initial_context={},
        gathered_context={},
    )
    async_session.add(run)
    await async_session.flush()
    return org, run


def _today():
    from api.services.billing.rollup import IST

    return datetime.now(IST).date()


@pytest.mark.asyncio
class TestRecording:
    async def test_a_reason_is_written_to_the_run(self, db_session, async_session):
        _, run = await _run(async_session, "record")

        await failures.record(
            async_session,
            workflow_run_id=run.id,
            reason=RunFailureReason.INSUFFICIENT_CREDIT,
            detail="Balance is zero",
        )
        await async_session.refresh(run)

        assert run.failure_reason == "insufficient_credit"
        assert run.failure_detail == "Balance is zero"

    async def test_the_first_reason_wins(self, db_session, async_session):
        """A call refused for no credit that then loses its websocket must
        read as "no credit". The second failure is a consequence of the first,
        and letting it overwrite blames the symptom."""
        _, run = await _run(async_session, "first")

        await failures.record(
            async_session,
            workflow_run_id=run.id,
            reason=RunFailureReason.INSUFFICIENT_CREDIT,
        )
        await failures.record(
            async_session,
            workflow_run_id=run.id,
            reason=RunFailureReason.PLATFORM_ERROR,
        )
        await async_session.refresh(run)

        assert run.failure_reason == "insufficient_credit"

    async def test_recording_against_a_missing_run_does_not_raise(
        self, db_session, async_session
    ):
        """Every caller is on an error path. An exception here replaces a
        diagnosable failure with an undiagnosable one."""
        await failures.record(
            workflow_run_id=-1,
            session=async_session,
            reason=RunFailureReason.PLATFORM_ERROR,
        )

    async def test_a_long_detail_is_truncated_rather_than_refused(
        self, db_session, async_session
    ):
        """A provider stack trace is not worth failing a write whose whole
        purpose is to explain a failure."""
        _, run = await _run(async_session, "long")

        await failures.record(
            async_session,
            workflow_run_id=run.id,
            reason=RunFailureReason.PROVIDER_ERROR,
            detail="x" * 5000,
        )
        await async_session.refresh(run)

        assert len(run.failure_detail) == 2000


class TestMappingExistingCodes:
    def test_known_codes_map_onto_the_vocabulary(self):
        assert (
            failures.reason_for_error_code("insufficient_credit")
            is RunFailureReason.INSUFFICIENT_CREDIT
        )
        assert (
            failures.reason_for_error_code("outside_calling_hours")
            is RunFailureReason.OUTSIDE_CALLING_HOURS
        )

    def test_an_unmapped_code_becomes_unknown_not_our_fault(self):
        """Claiming a fault is ours when we have not classified it is how a
        breakdown stops being trusted — and how the fault rate reads high for
        reasons nobody can act on."""
        assert (
            failures.reason_for_error_code("something_new") is RunFailureReason.UNKNOWN
        )
        assert failures.reason_for_error_code(None) is RunFailureReason.UNKNOWN


@pytest.mark.asyncio
class TestTheBreakdown:
    async def test_it_groups_by_who_has_to_act(self, db_session, async_session):
        org, run = await _run(async_session, "owner")
        await failures.record(
            async_session,
            workflow_run_id=run.id,
            reason=RunFailureReason.PROVIDER_ERROR,
        )
        _, second = await _run(async_session, "owner2", organization=org)
        await failures.record(
            async_session,
            workflow_run_id=second.id,
            reason=RunFailureReason.INSUFFICIENT_CREDIT,
        )

        result = await failure_breakdown(
            async_session, start=_today(), end=_today(), organization_id=org.id
        )
        owners = {r["reason"]: r["owner"] for r in result["reasons"]}

        assert owners["provider_error"] == "us"
        assert owners["insufficient_credit"] == "customer"

    async def test_the_fault_rate_counts_only_our_faults(
        self, db_session, async_session
    ):
        """The number that should be near zero. Counting a compliance refusal
        against it would have a fleet obeying the law look broken."""
        org, run = await _run(async_session, "rate")
        await failures.record(
            async_session,
            workflow_run_id=run.id,
            reason=RunFailureReason.PROVIDER_ERROR,
        )
        for index in range(3):
            _, other = await _run(async_session, f"rate-dnd{index}", organization=org)
            await failures.record(
                async_session,
                workflow_run_id=other.id,
                reason=RunFailureReason.DND_LISTED,
            )

        result = await failure_breakdown(
            async_session, start=_today(), end=_today(), organization_id=org.id
        )

        assert result["total_runs"] == 4
        assert result["failed_runs"] == 4
        assert result["fault_runs"] == 1
        assert result["fault_rate"] == 0.25

    async def test_deliberate_refusals_are_marked_as_such(
        self, db_session, async_session
    ):
        """They are the system working. A screen that cannot tell them from
        faults is one where every real problem is buried under them."""
        org, run = await _run(async_session, "deliberate")
        await failures.record(
            async_session,
            workflow_run_id=run.id,
            reason=RunFailureReason.OUTSIDE_CALLING_HOURS,
        )

        result = await failure_breakdown(
            async_session, start=_today(), end=_today(), organization_id=org.id
        )
        assert result["reasons"][0]["deliberate"] is True
        assert result["fault_runs"] == 0

    async def test_a_reason_this_deploy_does_not_know_is_reported_not_dropped(
        self, db_session, async_session
    ):
        """Written by a newer deploy during a rollout. Dropping it would make
        the fleet look healthier during exactly the window somebody is
        investigating."""
        org, run = await _run(async_session, "future")
        await failures.record(
            async_session, workflow_run_id=run.id, reason="a_reason_from_the_future"
        )

        result = await failure_breakdown(
            async_session, start=_today(), end=_today(), organization_id=org.id
        )
        assert result["reasons"][0]["reason"] == "a_reason_from_the_future"
        assert result["fault_runs"] == 1

    async def test_calls_that_went_fine_are_counted_but_not_listed(
        self, db_session, async_session
    ):
        """The denominator is every call, not every failure. A fault rate over
        failures alone is always 100% of something."""
        org, _ = await _run(async_session, "fine")

        result = await failure_breakdown(
            async_session, start=_today(), end=_today(), organization_id=org.id
        )

        assert result["total_runs"] == 1
        assert result["failed_runs"] == 0
        assert result["reasons"] == []
        assert result["fault_rate"] == 0.0

    async def test_one_organization_cannot_see_anothers_failures(
        self, db_session, async_session
    ):
        mine, my_run = await _run(async_session, "mine-f")
        await failures.record(
            async_session,
            workflow_run_id=my_run.id,
            reason=RunFailureReason.PROVIDER_ERROR,
        )
        _, their_run = await _run(async_session, "theirs-f")
        await failures.record(
            async_session,
            workflow_run_id=their_run.id,
            reason=RunFailureReason.TELEPHONY_ERROR,
        )

        result = await failure_breakdown(
            async_session, start=_today(), end=_today(), organization_id=mine.id
        )
        assert [r["reason"] for r in result["reasons"]] == ["provider_error"]

    async def test_an_empty_window_does_not_divide_by_zero(
        self, db_session, async_session
    ):
        result = await failure_breakdown(
            async_session, start=_today(), end=_today(), organization_id=-1
        )
        assert result["fault_rate"] is None
