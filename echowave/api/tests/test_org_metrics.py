"""Org-wide answer rate and cost-by-outcome — the numbers on the usage screen
that tell a customer whether the product is working and whether it pays.

Same denominator discipline as `campaign_summary.py`: a rate with nothing
measured is `None`, not `0.0`, and every query is scoped by organization_id
so one account's numbers never inflate with a stranger's calls. Cost-by-outcome
has no fixed cross-tenant taxonomy, so the tests care most about the fallback
label for calls with no disposition recorded and about the date window.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from api.db.models import OrganizationModel, UserModel, WorkflowModel, WorkflowRunModel
from api.services.reports.org_metrics import answer_seizure_ratio, cost_by_outcome


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


async def _call(
    async_session,
    workflow,
    *,
    answered: bool = True,
    disposition: str | None = "booked",
    charged_paise: int | None = 1000,
    created_at: datetime | None = None,
):
    created = created_at or datetime.now(UTC)
    gathered_context = {}
    if disposition is not None:
        gathered_context["mapped_call_disposition"] = disposition
    run = WorkflowRunModel(
        name="run",
        workflow_id=workflow.id,
        mode="twilio",
        usage_info={},
        cost_info={},
        initial_context={},
        gathered_context=gathered_context,
        is_completed=True,
        created_at=created,
        answered_at=created if answered else None,
        total_charged_paise=charged_paise,
    )
    async_session.add(run)
    await async_session.flush()
    return run


@pytest.mark.asyncio
class TestAnswerSeizureRatio:
    async def test_asr_is_connected_over_attempted(self, async_session):
        org, workflow = await _org_with_workflow(async_session, "asr")
        await _call(async_session, workflow, answered=True)
        await _call(async_session, workflow, answered=True)
        await _call(async_session, workflow, answered=False)
        await _call(async_session, workflow, answered=False)

        result = await answer_seizure_ratio(
            async_session, organization_id=org.id, days=7
        )
        assert result == {"attempted": 4, "connected": 2, "asr": 0.5}

    async def test_no_calls_reports_none_rather_than_zero(self, async_session):
        org, _workflow = await _org_with_workflow(async_session, "asr-empty")

        result = await answer_seizure_ratio(
            async_session, organization_id=org.id, days=7
        )
        assert result == {"attempted": 0, "connected": 0, "asr": None}

    async def test_another_orgs_calls_are_not_counted(self, async_session):
        mine, my_workflow = await _org_with_workflow(async_session, "asr-mine")
        theirs, their_workflow = await _org_with_workflow(async_session, "asr-theirs")
        await _call(async_session, my_workflow, answered=True)
        for _ in range(5):
            await _call(async_session, their_workflow, answered=True)

        result = await answer_seizure_ratio(
            async_session, organization_id=mine.id, days=7
        )
        assert result["attempted"] == 1

    async def test_calls_outside_the_day_window_are_excluded(self, async_session):
        org, workflow = await _org_with_workflow(async_session, "asr-window")
        await _call(async_session, workflow, answered=True)
        await _call(
            async_session,
            workflow,
            answered=True,
            created_at=datetime.now(UTC) - timedelta(days=30),
        )

        result = await answer_seizure_ratio(
            async_session, organization_id=org.id, days=7
        )
        assert result["attempted"] == 1


@pytest.mark.asyncio
class TestCostByOutcome:
    async def test_calls_are_grouped_by_disposition(self, async_session):
        org, workflow = await _org_with_workflow(async_session, "outcome")
        await _call(async_session, workflow, disposition="booked", charged_paise=1000)
        await _call(async_session, workflow, disposition="booked", charged_paise=2000)
        await _call(
            async_session, workflow, disposition="not_interested", charged_paise=500
        )

        rows = await cost_by_outcome(async_session, organization_id=org.id, days=7)
        by_disposition = {r["disposition"]: r for r in rows}

        assert by_disposition["booked"]["calls"] == 2
        assert by_disposition["booked"]["total_charged_paise"] == 3000
        assert by_disposition["booked"]["cost_per_call_paise"] == 1500
        assert by_disposition["not_interested"]["calls"] == 1

    async def test_a_call_with_no_disposition_recorded_is_labelled_unknown(
        self, async_session
    ):
        """Dropping it would leave a complete-looking breakdown over whatever
        calls happened to have a disposition, which is how a broken End Call
        tool reads as a clean report."""
        org, workflow = await _org_with_workflow(async_session, "outcome-unknown")
        await _call(async_session, workflow, disposition=None)

        rows = await cost_by_outcome(async_session, organization_id=org.id, days=7)
        assert rows == [
            {
                "disposition": "unknown",
                "calls": 1,
                "total_charged_paise": 1000,
                "cost_per_call_paise": 1000,
            }
        ]

    async def test_an_uncosted_call_contributes_zero_cost_not_a_missing_row(
        self, async_session
    ):
        org, workflow = await _org_with_workflow(async_session, "outcome-uncosted")
        await _call(async_session, workflow, disposition="booked", charged_paise=None)

        rows = await cost_by_outcome(async_session, organization_id=org.id, days=7)
        assert rows == [
            {
                "disposition": "booked",
                "calls": 1,
                "total_charged_paise": 0,
                "cost_per_call_paise": 0,
            }
        ]

    async def test_rows_are_ordered_by_call_count_descending(self, async_session):
        org, workflow = await _org_with_workflow(async_session, "outcome-order")
        await _call(async_session, workflow, disposition="rare")
        for _ in range(3):
            await _call(async_session, workflow, disposition="common")

        rows = await cost_by_outcome(async_session, organization_id=org.id, days=7)
        assert [r["disposition"] for r in rows] == ["common", "rare"]

    async def test_another_orgs_calls_are_not_counted(self, async_session):
        mine, my_workflow = await _org_with_workflow(async_session, "outcome-mine")
        theirs, their_workflow = await _org_with_workflow(
            async_session, "outcome-theirs"
        )
        await _call(async_session, my_workflow, disposition="booked")
        await _call(async_session, their_workflow, disposition="booked")

        rows = await cost_by_outcome(async_session, organization_id=mine.id, days=7)
        assert sum(r["calls"] for r in rows) == 1


@pytest.mark.asyncio
class TestOutcomesRoute:
    async def test_the_route_returns_both_metrics(
        self, async_session, db_session, test_client_factory
    ):
        org, workflow = await _org_with_workflow(async_session, "route")
        user = UserModel(
            provider_id="user-route-caller", selected_organization_id=org.id
        )
        async_session.add(user)
        await async_session.flush()
        await _call(async_session, workflow, answered=True, disposition="booked")
        await _call(async_session, workflow, answered=False, disposition=None)

        async with test_client_factory(user) as client:
            response = await client.get("/api/v1/organizations/usage/outcomes?days=7")

        assert response.status_code == 200
        body = response.json()
        assert body["answer_seizure_ratio"] == {
            "attempted": 2,
            "connected": 1,
            "asr": 0.5,
        }
        dispositions = {r["disposition"] for r in body["cost_by_outcome"]}
        assert dispositions == {"booked", "unknown"}
