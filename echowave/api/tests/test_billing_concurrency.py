"""Peak concurrency for a campaign.

Concurrency is the one dashboard figure that can't be read off a single row —
it only exists in the overlap between calls — so it gets its own tests.
"""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from api.db.billing_dashboard_client import campaign_concurrency
from api.db.models import (
    CampaignModel,
    OrganizationModel,
    UserModel,
    WorkflowModel,
    WorkflowRunModel,
)

# A mid-afternoon IST anchor, far from the 00:00 IST boundary, so a test that
# isn't about bucketing never trips over it.
ANCHOR = datetime(2026, 7, 12, 9, 0, tzinfo=UTC)  # 14:30 IST


async def _campaign(async_session, slug: str):
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

    campaign = CampaignModel(
        name=f"campaign-{slug}",
        organization_id=org.id,
        workflow_id=workflow.id,
        created_by=user.id,
        source_type="csv",
        source_id=f"src-{slug}",
    )
    async_session.add(campaign)
    await async_session.flush()
    return campaign, workflow


async def _call(async_session, campaign, workflow, *, start, seconds):
    run = WorkflowRunModel(
        name="run",
        workflow_id=workflow.id,
        campaign_id=campaign.id,
        mode="twilio",
        usage_info={},
        cost_info={},
        initial_context={},
        gathered_context={},
        is_completed=True,
        created_at=start,
        answered_at=start,
        ended_at=start + timedelta(seconds=seconds),
        billable_seconds=seconds,
    )
    async_session.add(run)
    await async_session.flush()
    return run


class TestPeakConcurrency:
    async def test_overlapping_calls_count_together(self, async_session):
        campaign, workflow = await _campaign(async_session, "overlap")
        # Three calls all in flight at ANCHOR+90s.
        for offset in (0, 30, 60):
            await _call(
                async_session,
                campaign,
                workflow,
                start=ANCHOR + timedelta(seconds=offset),
                seconds=300,
            )

        result = await campaign_concurrency(
            async_session, campaign_id=campaign.id
        )
        assert max(row["peak"] for row in result["series"]) == 3

    async def test_sequential_calls_never_overlap(self, async_session):
        campaign, workflow = await _campaign(async_session, "sequential")
        # Back-to-back, each starting after the previous one ended.
        for index in range(4):
            await _call(
                async_session,
                campaign,
                workflow,
                start=ANCHOR + timedelta(seconds=index * 120),
                seconds=60,
            )

        result = await campaign_concurrency(
            async_session, campaign_id=campaign.id
        )
        assert max(row["peak"] for row in result["series"]) == 1

    async def test_a_call_ending_as_another_starts_is_not_concurrent(
        self, async_session
    ):
        """The tie-break at a shared instant: the end is applied before the start."""
        campaign, workflow = await _campaign(async_session, "handoff")
        await _call(
            async_session, campaign, workflow, start=ANCHOR, seconds=60
        )
        await _call(
            async_session,
            campaign,
            workflow,
            start=ANCHOR + timedelta(seconds=60),
            seconds=60,
        )

        result = await campaign_concurrency(
            async_session, campaign_id=campaign.id
        )
        assert max(row["peak"] for row in result["series"]) == 1

    async def test_a_still_running_call_falls_back_to_its_billed_duration(
        self, async_session
    ):
        campaign, workflow = await _campaign(async_session, "inflight")
        run = await _call(
            async_session, campaign, workflow, start=ANCHOR, seconds=120
        )
        run.ended_at = None  # still up; only billable_seconds is known
        await async_session.flush()

        result = await campaign_concurrency(
            async_session, campaign_id=campaign.id
        )
        # It still occupies the line rather than vanishing from the series.
        assert max(row["peak"] for row in result["series"]) == 1

    async def test_a_campaign_with_no_calls_is_empty_not_an_error(
        self, async_session
    ):
        campaign, _ = await _campaign(async_session, "empty")
        result = await campaign_concurrency(
            async_session, campaign_id=campaign.id
        )
        assert result == {"bucket": "hour", "series": []}


class TestBucketing:
    async def test_a_short_campaign_buckets_by_hour(self, async_session):
        campaign, workflow = await _campaign(async_session, "short")
        await _call(async_session, campaign, workflow, start=ANCHOR, seconds=60)
        await _call(
            async_session,
            campaign,
            workflow,
            start=ANCHOR + timedelta(hours=5),
            seconds=60,
        )

        result = await campaign_concurrency(
            async_session, campaign_id=campaign.id
        )
        assert result["bucket"] == "hour"
        assert len(result["series"]) == 2

    async def test_a_long_campaign_buckets_by_day(self, async_session):
        campaign, workflow = await _campaign(async_session, "long")
        for day in range(0, 12, 2):
            await _call(
                async_session,
                campaign,
                workflow,
                start=ANCHOR + timedelta(days=day),
                seconds=60,
            )

        result = await campaign_concurrency(
            async_session, campaign_id=campaign.id
        )
        assert result["bucket"] == "day"
        assert len(result["series"]) == 6

    @pytest.mark.parametrize(
        "start_utc,expected_ist_day",
        [
            # The IST day turns over at 18:30 UTC. Straddle it: 18:00 UTC is
            # still 23:30 IST on the 11th...
            (datetime(2026, 7, 11, 18, 0, tzinfo=UTC), "2026-07-11"),
            # ...while 19:00 UTC is already 00:30 IST on the 12th. Bucketing by
            # UTC day would file both under the 11th.
            (datetime(2026, 7, 11, 19, 0, tzinfo=UTC), "2026-07-12"),
        ],
    )
    async def test_daily_buckets_are_ist_days_not_utc_days(
        self, async_session, start_utc, expected_ist_day
    ):
        campaign, workflow = await _campaign(
            async_session, f"ist-{expected_ist_day}"
        )
        await _call(async_session, campaign, workflow, start=start_utc, seconds=60)
        # A second call two weeks out forces the daily bucket.
        await _call(
            async_session,
            campaign,
            workflow,
            start=start_utc + timedelta(days=14),
            seconds=60,
        )

        result = await campaign_concurrency(
            async_session, campaign_id=campaign.id
        )
        assert result["bucket"] == "day"
        assert result["series"][0]["bucket_start"].startswith(expected_ist_day)


class TestRollupCronTask:
    """The scheduled refresh — the thing whose absence made Overview read zero."""

    async def test_the_task_writes_rollup_rows_for_costed_calls(
        self, async_session, monkeypatch
    ):
        from contextlib import asynccontextmanager

        from api.db.models import DailyOrganizationRollupModel
        from api.tasks import billing_rollup

        campaign, workflow = await _campaign(async_session, "rollup-cron")
        run = await _call(
            async_session, campaign, workflow, start=datetime.now(UTC), seconds=90
        )
        run.total_charged_paise = 300
        run.total_provider_cost_paise = 100
        run.costed_at = datetime.now(UTC)
        await async_session.flush()

        # The task owns its own session; hand it the test's so the work lands
        # in the same transaction the assertions read from.
        @asynccontextmanager
        async def _session():
            yield async_session

        monkeypatch.setattr(
            billing_rollup.db_client, "async_session", _session, raising=False
        )
        monkeypatch.setattr(async_session, "commit", async_session.flush)

        await billing_rollup.refresh_billing_rollups(None)

        org_id = workflow.organization_id
        rows = (
            await async_session.execute(
                select(DailyOrganizationRollupModel).where(
                    DailyOrganizationRollupModel.organization_id == org_id
                )
            )
        ).scalars().all()

        assert rows, "the cron task must actually write rollup rows"
        assert sum(r.charged_paise for r in rows) == 300
        assert sum(r.provider_cost_paise for r in rows) == 100

    async def test_running_twice_converges_rather_than_doubling(
        self, async_session, monkeypatch
    ):
        from contextlib import asynccontextmanager

        from api.db.models import DailyOrganizationRollupModel
        from api.tasks import billing_rollup

        campaign, workflow = await _campaign(async_session, "rollup-idempotent")
        run = await _call(
            async_session, campaign, workflow, start=datetime.now(UTC), seconds=90
        )
        run.total_charged_paise = 300
        run.costed_at = datetime.now(UTC)
        await async_session.flush()

        @asynccontextmanager
        async def _session():
            yield async_session

        monkeypatch.setattr(
            billing_rollup.db_client, "async_session", _session, raising=False
        )
        monkeypatch.setattr(async_session, "commit", async_session.flush)

        await billing_rollup.refresh_billing_rollups(None)
        await billing_rollup.refresh_billing_rollups(None)

        total = await async_session.scalar(
            select(func.sum(DailyOrganizationRollupModel.charged_paise)).where(
                DailyOrganizationRollupModel.organization_id
                == workflow.organization_id
            )
        )
        assert total == 300
