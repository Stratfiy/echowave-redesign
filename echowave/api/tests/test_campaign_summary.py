"""The aggregate campaign report — connection and completion rates, retries,
language distribution, daily progress.

These tests are almost entirely about **denominators**. The arithmetic is
division and cannot really be wrong; what can be wrong is what goes under the
line, and the failure mode is not an exception but a plausible percentage that
disagrees with the dashboard, or flatters the campaign, or reads 0% on a
campaign that has not started.

So the cases that matter are the ones where two defensible denominators exist
and the choice changes the answer:

* a call nobody answered cannot complete, so it is not in the completion
  denominator;
* a contact dialled three times is one contact and three attempts;
* a campaign with no calls has *no* connection rate, which is not 0%.
"""

from datetime import UTC, datetime, timedelta

import pytest

from api.db.models import (
    CampaignModel,
    OrganizationModel,
    QueuedRunModel,
    UserModel,
    WorkflowModel,
    WorkflowRunModel,
)
from api.services.reports.campaign_summary import campaign_summary

IST_OFFSET = timedelta(hours=5, minutes=30)


async def _campaign(async_session, slug: str, *, total_rows: int | None = None):
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
        source_id=f"{slug}.csv",
        state="running",
        total_rows=total_rows,
    )
    async_session.add(campaign)
    await async_session.flush()
    return campaign, workflow


async def _call(
    async_session,
    campaign,
    workflow,
    *,
    answered: bool = True,
    completed: bool = True,
    language: str | None = "te-IN",
    seconds: int | None = 100,
    created_at: datetime | None = None,
):
    created = created_at or datetime.now(UTC)
    run = WorkflowRunModel(
        name="run",
        workflow_id=workflow.id,
        campaign_id=campaign.id,
        mode="twilio",
        usage_info={},
        cost_info={},
        initial_context={},
        gathered_context={},
        is_completed=completed,
        language=language,
        billable_seconds=seconds,
        created_at=created,
        answered_at=created if answered else None,
    )
    async_session.add(run)
    await async_session.flush()
    return run


@pytest.mark.asyncio
class TestConnectionAndCompletionRates:
    async def test_connection_rate_is_answered_over_attempted(self, async_session):
        campaign, workflow = await _campaign(async_session, "conn")
        await _call(async_session, campaign, workflow, answered=True)
        await _call(async_session, campaign, workflow, answered=True)
        await _call(async_session, campaign, workflow, answered=False, completed=False)
        await _call(async_session, campaign, workflow, answered=False, completed=False)

        summary = await campaign_summary(async_session, campaign=campaign)
        assert summary["totals"]["attempted"] == 4
        assert summary["totals"]["connected"] == 2
        assert summary["totals"]["connection_rate"] == 0.5

    async def test_completion_rate_excludes_calls_nobody_answered(self, async_session):
        """The denominator choice that matters most.

        Three dials, one answered, and that one ran to the end. The agent
        completed everything it was given the chance to complete, so this is
        100% — over attempts it would read 33% and blame the agent for the
        carrier's unanswered calls.
        """
        campaign, workflow = await _campaign(async_session, "compl")
        await _call(async_session, campaign, workflow, answered=True, completed=True)
        await _call(async_session, campaign, workflow, answered=False, completed=False)
        await _call(async_session, campaign, workflow, answered=False, completed=False)

        summary = await campaign_summary(async_session, campaign=campaign)
        assert summary["totals"]["completion_rate"] == 1.0
        assert summary["totals"]["connection_rate"] == pytest.approx(0.3333, abs=1e-4)

    async def test_an_answered_call_that_did_not_finish_lowers_completion(
        self, async_session
    ):
        campaign, workflow = await _campaign(async_session, "dropped")
        await _call(async_session, campaign, workflow, answered=True, completed=True)
        await _call(async_session, campaign, workflow, answered=True, completed=False)

        summary = await campaign_summary(async_session, campaign=campaign)
        assert summary["totals"]["completion_rate"] == 0.5

    async def test_a_campaign_with_no_calls_has_no_rate_rather_than_zero(
        self, async_session
    ):
        """0% is a measurement. A campaign that has not dialled has not
        measured anything, and reporting 0% reads as total failure."""
        campaign, _ = await _campaign(async_session, "empty")

        summary = await campaign_summary(async_session, campaign=campaign)
        assert summary["totals"]["connection_rate"] is None
        assert summary["totals"]["completion_rate"] is None

    async def test_completion_rate_is_none_when_nothing_connected(self, async_session):
        """Dials were made and none answered. Connection rate is a real 0%;
        completion has still measured nothing."""
        campaign, workflow = await _campaign(async_session, "nobody-home")
        await _call(async_session, campaign, workflow, answered=False, completed=False)

        summary = await campaign_summary(async_session, campaign=campaign)
        assert summary["totals"]["connection_rate"] == 0.0
        assert summary["totals"]["completion_rate"] is None

    async def test_reach_against_the_contact_list_is_reported_separately(
        self, async_session
    ):
        """A reach target is written against the list you were given, not
        against the dials you chose to make."""
        campaign, workflow = await _campaign(async_session, "reach", total_rows=10)
        for _ in range(4):
            await _call(async_session, campaign, workflow, answered=True)

        summary = await campaign_summary(async_session, campaign=campaign)
        assert summary["totals"]["contact_connection_rate"] == 0.4
        assert summary["totals"]["connection_rate"] == 1.0


@pytest.mark.asyncio
class TestTalkTime:
    async def test_minutes_come_from_the_costed_measure(self, async_session):
        campaign, workflow = await _campaign(async_session, "minutes")
        await _call(async_session, campaign, workflow, seconds=90)
        await _call(async_session, campaign, workflow, seconds=30)

        summary = await campaign_summary(async_session, campaign=campaign)
        assert summary["totals"]["talk_minutes"] == 2.0

    async def test_a_call_not_yet_costed_falls_back_to_measured_duration(
        self, async_session
    ):
        """billable_seconds is only written once the cost engine has run. A
        campaign on a deployment whose worker is behind would otherwise report
        zero talk time while calls were plainly happening."""
        campaign, workflow = await _campaign(async_session, "uncosted")
        run = await _call(async_session, campaign, workflow, seconds=None)
        run.usage_info = {"call_duration_seconds": "120"}
        await async_session.flush()

        summary = await campaign_summary(async_session, campaign=campaign)
        assert summary["totals"]["talk_minutes"] == 2.0


@pytest.mark.asyncio
class TestRetries:
    async def _queued(
        self,
        async_session,
        campaign,
        *,
        uuid,
        retry_count=0,
        reason=None,
        parent=None,
        state="processed",
    ):
        row = QueuedRunModel(
            campaign_id=campaign.id,
            source_uuid=uuid,
            context_variables={},
            state=state,
            retry_count=retry_count,
            retry_reason=reason,
            parent_queued_run_id=parent,
        )
        async_session.add(row)
        await async_session.flush()
        return row

    async def test_first_attempts_are_not_retries(self, async_session):
        campaign, _ = await _campaign(async_session, "no-retry")
        await self._queued(async_session, campaign, uuid="a")
        await self._queued(async_session, campaign, uuid="b")

        summary = await campaign_summary(async_session, campaign=campaign)
        assert summary["retries"]["retry_attempts"] == 0

    async def test_retries_are_counted_by_reason(self, async_session):
        campaign, _ = await _campaign(async_session, "by-reason")
        first = await self._queued(async_session, campaign, uuid="a")
        await self._queued(
            async_session,
            campaign,
            uuid="a_retry_1",
            retry_count=1,
            reason="busy",
            parent=first.id,
        )
        second = await self._queued(async_session, campaign, uuid="b")
        await self._queued(
            async_session,
            campaign,
            uuid="b_retry_1",
            retry_count=1,
            reason="no_answer",
            parent=second.id,
        )
        await self._queued(
            async_session,
            campaign,
            uuid="b_retry_2",
            retry_count=2,
            reason="no_answer",
            parent=second.id,
        )

        summary = await campaign_summary(async_session, campaign=campaign)
        assert summary["retries"]["retry_attempts"] == 3
        assert summary["retries"]["by_reason"] == {"busy": 1, "no_answer": 2}

    async def test_contacts_retried_counts_people_not_attempts(self, async_session):
        """Two redials of the same number is one person chased twice."""
        campaign, _ = await _campaign(async_session, "contacts")
        parent = await self._queued(async_session, campaign, uuid="a")
        await self._queued(
            async_session,
            campaign,
            uuid="a_retry_1",
            retry_count=1,
            reason="busy",
            parent=parent.id,
        )
        await self._queued(
            async_session,
            campaign,
            uuid="a_retry_2",
            retry_count=2,
            reason="busy",
            parent=parent.id,
        )

        summary = await campaign_summary(async_session, campaign=campaign)
        assert summary["retries"]["retry_attempts"] == 2
        assert summary["retries"]["contacts_retried"] == 1

    async def test_retries_still_waiting_are_reported(self, async_session):
        """A campaign that looks finished with redials queued has not finished,
        and calling its reach final would be early."""
        campaign, _ = await _campaign(async_session, "pending")
        parent = await self._queued(async_session, campaign, uuid="a")
        await self._queued(
            async_session,
            campaign,
            uuid="a_retry_1",
            retry_count=1,
            reason="busy",
            parent=parent.id,
            state="queued",
        )

        summary = await campaign_summary(async_session, campaign=campaign)
        assert summary["retries"]["retries_pending"] == 1


@pytest.mark.asyncio
class TestLanguageDistribution:
    async def test_languages_are_counted_over_connected_calls(self, async_session):
        campaign, workflow = await _campaign(async_session, "lang")
        await _call(async_session, campaign, workflow, language="te-IN")
        await _call(async_session, campaign, workflow, language="te-IN")
        await _call(async_session, campaign, workflow, language="hi-IN")

        summary = await campaign_summary(async_session, campaign=campaign)
        by_language = {row["language"]: row for row in summary["languages"]}
        assert by_language["te-IN"]["calls"] == 2
        assert by_language["te-IN"]["share"] == pytest.approx(0.6667, abs=1e-4)
        assert by_language["hi-IN"]["calls"] == 1

    async def test_an_unanswered_dial_has_no_language_to_report(self, async_session):
        """Its language is the agent's configuration, not the population's.
        Counting it would describe how we set the campaign up rather than who
        we actually reached."""
        campaign, workflow = await _campaign(async_session, "lang-unanswered")
        await _call(async_session, campaign, workflow, language="te-IN")
        await _call(
            async_session,
            campaign,
            workflow,
            language="te-IN",
            answered=False,
            completed=False,
        )

        summary = await campaign_summary(async_session, campaign=campaign)
        assert summary["languages"][0]["calls"] == 1
        assert summary["languages"][0]["share"] == 1.0

    async def test_calls_with_no_language_recorded_are_shown_as_unknown(
        self, async_session
    ):
        """Dropping them would leave a complete-looking distribution over
        whatever rows survived, which is how broken instrumentation reads as a
        clean result."""
        campaign, workflow = await _campaign(async_session, "lang-null")
        await _call(async_session, campaign, workflow, language="te-IN")
        await _call(async_session, campaign, workflow, language=None)

        summary = await campaign_summary(async_session, campaign=campaign)
        by_language = {row["language"]: row["calls"] for row in summary["languages"]}
        assert by_language == {"te-IN": 1, "unknown": 1}


@pytest.mark.asyncio
class TestDailyProgress:
    async def test_days_are_bucketed_in_ist_not_utc(self, async_session):
        """A call at 01:00 IST is that day's work. In UTC it is 19:30 the day
        before, and an operator comparing this to their own day would find the
        boundary five and a half hours out.
        """
        campaign, workflow = await _campaign(async_session, "ist")
        # 2026-03-10 01:00 IST == 2026-03-09 19:30 UTC
        await _call(
            async_session,
            campaign,
            workflow,
            created_at=datetime(2026, 3, 9, 19, 30, tzinfo=UTC),
        )

        summary = await campaign_summary(async_session, campaign=campaign)
        assert [row["date"] for row in summary["daily"]] == ["2026-03-10"]

    async def test_daily_rows_are_ordered_and_carry_their_own_rate(self, async_session):
        campaign, workflow = await _campaign(async_session, "daily")
        day_one = datetime(2026, 3, 10, 6, 0, tzinfo=UTC)
        day_two = day_one + timedelta(days=1)
        await _call(async_session, campaign, workflow, created_at=day_one)
        await _call(
            async_session,
            campaign,
            workflow,
            created_at=day_two,
            answered=False,
            completed=False,
        )

        summary = await campaign_summary(async_session, campaign=campaign)
        assert [row["date"] for row in summary["daily"]] == [
            "2026-03-10",
            "2026-03-11",
        ]
        assert summary["daily"][0]["connection_rate"] == 1.0
        assert summary["daily"][1]["connection_rate"] == 0.0


@pytest.mark.asyncio
class TestCircuitBreakerTrips:
    async def test_trips_are_counted_from_the_campaign_log(
        self, async_session, db_session
    ):
        """A trip is an entry in `logs`, written the way the real breaker
        writes it (services/campaign/circuit_breaker.py), not a hand-crafted
        row — so this exercises the same JSON shape production produces."""
        campaign, _ = await _campaign(async_session, "trips")

        await db_session.append_campaign_log(
            campaign.id,
            "warning",
            "circuit_breaker_tripped",
            "Circuit breaker tripped",
            details={"failure_rate": 0.6},
        )
        await db_session.append_campaign_log(
            campaign.id,
            "warning",
            "circuit_breaker_tripped",
            "Circuit breaker tripped again",
            details={"failure_rate": 0.7},
        )
        await db_session.append_campaign_log(
            campaign.id, "info", "campaign_started", "Campaign started"
        )

        summary = await campaign_summary(async_session, campaign=campaign)
        assert summary["totals"]["circuit_breaker_trips"] == 2

    async def test_a_campaign_with_no_trips_reports_zero(self, async_session):
        campaign, _ = await _campaign(async_session, "no-trips")

        summary = await campaign_summary(async_session, campaign=campaign)
        assert summary["totals"]["circuit_breaker_trips"] == 0


@pytest.mark.asyncio
class TestScoping:
    async def test_another_campaigns_calls_are_not_counted(self, async_session):
        """Every aggregate here filters on campaign_id. A leak would inflate a
        customer's numbers with a stranger's calls."""
        mine, my_workflow = await _campaign(async_session, "mine")
        theirs, their_workflow = await _campaign(async_session, "theirs")
        await _call(async_session, mine, my_workflow)
        for _ in range(5):
            await _call(async_session, theirs, their_workflow)

        summary = await campaign_summary(async_session, campaign=mine)
        assert summary["totals"]["attempted"] == 1

    async def test_another_campaigns_retries_are_not_counted(self, async_session):
        mine, _ = await _campaign(async_session, "mine-r")
        theirs, _ = await _campaign(async_session, "theirs-r")
        parent = QueuedRunModel(
            campaign_id=theirs.id,
            source_uuid="x",
            context_variables={},
            state="processed",
            retry_count=0,
        )
        async_session.add(parent)
        await async_session.flush()
        async_session.add(
            QueuedRunModel(
                campaign_id=theirs.id,
                source_uuid="x_retry_1",
                context_variables={},
                state="processed",
                retry_count=1,
                retry_reason="busy",
                parent_queued_run_id=parent.id,
            )
        )
        await async_session.flush()

        summary = await campaign_summary(async_session, campaign=mine)
        assert summary["retries"]["retry_attempts"] == 0
