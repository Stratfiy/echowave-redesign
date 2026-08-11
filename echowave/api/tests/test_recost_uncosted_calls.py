"""scripts/recost_uncosted_calls.py: finding and fixing calls whose receipt
recorded usage with no rate on file at the time.

The subtlety worth guarding: `uncosted_usage IS NULL` and
`uncosted_usage = []` mean different things (see the column's own comment on
WorkflowRunModel) -- only the non-empty case is an actual gap to recost.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from api.db.models import (
    CreditLedgerModel,
    OrganizationModel,
    ProviderRateModel,
    UserModel,
    WorkflowModel,
    WorkflowRunModel,
)
from api.enums import CostComponent, RateUnit
from api.services.billing.costing import cost_workflow_run
from scripts.recost_uncosted_calls import _candidates


async def _org_with_workflow(async_session, slug: str):
    user = UserModel(provider_id=f"user-{slug}")
    org = OrganizationModel(provider_id=f"org-{slug}", quota_decibyl_tokens=0)
    async_session.add_all([user, org])
    await async_session.flush()

    workflow = WorkflowModel(
        name=f"wf-{slug}",
        user_id=user.id,
        organization_id=org.id,
        workflow_definition={},
        template_context_variables={},
        call_disposition_codes={},
    )
    async_session.add(workflow)
    await async_session.flush()
    return org, workflow


async def _run(async_session, workflow, *, usage_info, created_at=None):
    run = WorkflowRunModel(
        name="run",
        workflow_id=workflow.id,
        mode="twilio",
        usage_info=usage_info,
        cost_info={},
        initial_context={},
        gathered_context={},
        is_completed=True,
        created_at=created_at or datetime.now(UTC),
        answered_at=created_at or datetime.now(UTC),
    )
    async_session.add(run)
    await async_session.flush()
    return run


@pytest.mark.asyncio
class TestCandidates:
    async def test_a_run_with_no_uncosted_usage_is_not_a_candidate(self, async_session):
        _org, workflow = await _org_with_workflow(async_session, "clean")
        run = await _run(
            async_session, workflow, usage_info={"call_duration_seconds": 60}
        )
        await cost_workflow_run(async_session, run.id)
        await async_session.commit()

        candidates = await _candidates(async_session, workflow_run_id=None)
        assert run.id not in {c.id for c in candidates}

    async def test_a_run_with_uncosted_usage_is_a_candidate(self, async_session):
        _org, workflow = await _org_with_workflow(async_session, "gappy")
        run = await _run(
            async_session,
            workflow,
            usage_info={
                "tts": {"MysteryTTSService|||v1": 4000},
                "call_duration_seconds": 60,
            },
        )
        cost = await cost_workflow_run(async_session, run.id)
        await async_session.commit()
        assert cost.uncosted  # sanity: this run really is a gap

        candidates = await _candidates(async_session, workflow_run_id=None)
        assert run.id in {c.id for c in candidates}

    async def test_an_uncosted_run_is_not_a_candidate(self, async_session):
        """costed_at is None -- there is no receipt to correct yet, and
        cost_workflow_run's own idempotency guard is what should cost it
        first, not this script."""
        _org, workflow = await _org_with_workflow(async_session, "never-costed")
        run = await _run(
            async_session, workflow, usage_info={"call_duration_seconds": 60}
        )
        # Deliberately not costed.

        candidates = await _candidates(async_session, workflow_run_id=None)
        assert run.id not in {c.id for c in candidates}

    async def test_workflow_run_id_scopes_to_one_run(self, async_session):
        _org, workflow = await _org_with_workflow(async_session, "scoped")
        usage = {"tts": {"MysteryTTSService|||v1": 4000}, "call_duration_seconds": 60}
        run_a = await _run(async_session, workflow, usage_info=usage)
        run_b = await _run(async_session, workflow, usage_info=usage)
        await cost_workflow_run(async_session, run_a.id)
        await cost_workflow_run(async_session, run_b.id)
        await async_session.commit()

        candidates = await _candidates(async_session, workflow_run_id=run_a.id)
        assert {c.id for c in candidates} == {run_a.id}


@pytest.mark.asyncio
class TestRecostingFixesTheGap:
    async def test_adding_the_missing_rate_then_recosting_charges_the_gap(
        self, async_session
    ):
        """The actual point of the script: a rate seeded after the call was
        placed is picked up on recost, and the ledger reflects the corrected
        total rather than the original understated one."""
        org, workflow = await _org_with_workflow(async_session, "fixable")
        run = await _run(
            async_session,
            workflow,
            usage_info={
                "tts": {"SarvamTTSService|||bulbul:v3": 4000},
                "call_duration_seconds": 60,
            },
        )
        first = await cost_workflow_run(async_session, run.id)
        await async_session.commit()
        assert first.uncosted
        first_charged = run.total_charged_paise

        # The gap gets fixed the way it would in production: a rate is added
        # after the fact.
        async_session.add(
            ProviderRateModel(
                provider="sarvam",
                component=CostComponent.TTS.value,
                unit=RateUnit.THOUSAND_CHARS.value,
                rate_mpaise=500_000,
                effective_from=datetime(2020, 1, 1, tzinfo=UTC),
            )
        )
        await async_session.flush()

        candidates = await _candidates(async_session, workflow_run_id=None)
        assert run.id in {c.id for c in candidates}

        second = await cost_workflow_run(async_session, run.id, recost=True)
        await async_session.commit()

        assert second.uncosted == ()
        assert second.total_charged_paise > first_charged

        ledger_rows = (
            await async_session.scalars(
                select(CreditLedgerModel).where(
                    CreditLedgerModel.organization_id == org.id,
                    CreditLedgerModel.ref_type == "workflow_run",
                    CreditLedgerModel.ref_id == str(run.id),
                )
            )
        ).all()
        assert len(ledger_rows) == 1  # recost replaced, did not duplicate
        assert ledger_rows[0].delta_paise == -second.total_charged_paise
