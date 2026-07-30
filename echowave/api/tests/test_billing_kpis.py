"""Unit economics — the screen behind the pricing decision.

The numbers here are the ones a pricing change is judged on, so the tests care
less about shapes than about two properties:

* per-minute figures divide by **connected** seconds, not by billed seconds or
  whole minutes, so our own rounding convention cannot flatter them; and
* the cost of the 15-second pulse is reported as a real number, because a
  differentiator whose price is invisible is one nobody can decide to keep.
"""

from datetime import UTC, datetime, timedelta

import pytest

from api.db.models import (
    CallCostItemModel,
    OrganizationModel,
    UsdInrRateHistoryModel,
    WorkflowModel,
    WorkflowRunModel,
)
from api.enums import WorkflowRunMode, WorkflowRunState
from api.services.billing.kpis import FX_STALE_AFTER_DAYS, unit_economics_report

# Mid-afternoon IST, comfortably inside one IST day whichever way it is bucketed.
WHEN = datetime(2026, 6, 15, 6, 0, tzinfo=UTC)
DAY = WHEN.astimezone().date()
START = datetime(2026, 6, 15).date()
END = START


async def _org(session, slug: str) -> tuple[OrganizationModel, WorkflowModel]:
    org = OrganizationModel(provider_id=f"org-{slug}", quota_decibyl_tokens=0)
    session.add(org)
    await session.flush()
    workflow = WorkflowModel(name=f"wf-{slug}", organization_id=org.id, user_id=None)
    session.add(workflow)
    await session.flush()
    return org, workflow


async def _costed_call(
    session,
    workflow,
    *,
    billable_seconds: int,
    billed_seconds: int,
    charged_paise: int,
    provider_cost_paise: int = 0,
    rate_mpaise: int = 192_000,
    items: list[tuple[str, str, str, int]] | None = None,
    # Sentinel, because None is a meaningful value here: it is what a call
    # costed before we tracked unpriced usage looks like.
    uncosted: list[str] | None = (),
    when: datetime = WHEN,
) -> WorkflowRunModel:
    run = WorkflowRunModel(
        name="call",
        workflow_id=workflow.id,
        mode=WorkflowRunMode.PLIVO.value,
        state=WorkflowRunState.COMPLETED.value,
        created_at=when,
        billable_seconds=billable_seconds,
        billed_seconds=billed_seconds,
        platform_rate_mpaise_applied=rate_mpaise,
        pulse_seconds_applied=15,
        total_charged_paise=charged_paise,
        total_provider_cost_paise=provider_cost_paise,
        uncosted_usage=[] if uncosted == () else uncosted,
        costed_at=when,
    )
    session.add(run)
    await session.flush()

    for component, provider, model, cost in items or []:
        session.add(
            CallCostItemModel(
                workflow_run_id=run.id,
                component=component,
                provider=provider,
                model=model,
                units=1,
                unit_rate_mpaise=1,
                cost_paise=cost,
            )
        )
    await session.flush()
    return run


@pytest.mark.asyncio
class TestPerMinuteFigures:
    async def test_revenue_and_margin_are_per_connected_minute(
        self, db_session, async_session
    ):
        """Two calls, four connected minutes, ₹7.68 billed: ₹1.92 a minute."""
        _org_row, workflow = await _org(async_session, "per-min")
        for _ in range(2):
            await _costed_call(
                async_session,
                workflow,
                billable_seconds=120,
                billed_seconds=120,
                charged_paise=384,
                provider_cost_paise=84,
            )

        report = await unit_economics_report(async_session, start=START, end=END)

        assert report["billable_seconds"] == 240
        assert report["revenue_paise"] == 768
        assert report["revenue_paise_per_minute"] == 192
        assert report["provider_cost_paise_per_minute"] == 42
        assert report["margin_paise_per_minute"] == 150
        assert round(report["margin_pct"], 4) == round(600 / 768, 4)

    async def test_the_divisor_is_connected_time_not_billed_time(
        self, db_session, async_session
    ):
        """Dividing by billed seconds would make our own rounding look like
        service delivered, and quietly flatter every cost figure."""
        _org_row, workflow = await _org(async_session, "divisor")
        # A 50-second call billed as 60 (four 15s pulses).
        await _costed_call(
            async_session,
            workflow,
            billable_seconds=50,
            billed_seconds=60,
            charged_paise=192,
            provider_cost_paise=50,
        )

        report = await unit_economics_report(async_session, start=START, end=END)

        # 50 connected seconds at ₹0.50 of cost is ₹0.60/min, not ₹0.50.
        assert report["billable_seconds"] == 50
        assert report["provider_cost_paise_per_minute"] == 60

    async def test_an_empty_period_reports_no_margin_rather_than_zero(
        self, db_session, async_session
    ):
        """0% would read as "we lost everything"; there is simply nothing to
        report."""
        report = await unit_economics_report(
            async_session,
            start=(START - timedelta(days=400)),
            end=(START - timedelta(days=400)),
        )
        assert report["calls"] == 0
        assert report["margin_pct"] is None
        assert report["revenue_paise_per_minute"] == 0


@pytest.mark.asyncio
class TestThePulseHasAPrice:
    async def test_it_reports_what_the_pulse_gave_away(self, db_session, async_session):
        """The number the pricing decision turns on. A 70-second call bills 75
        seconds with us and 120 with a whole-minute competitor, so we chose not
        to collect 45 seconds of revenue."""
        _org_row, workflow = await _org(async_session, "pulse")
        await _costed_call(
            async_session,
            workflow,
            billable_seconds=70,
            billed_seconds=75,
            charged_paise=240,
        )

        pulse = (await unit_economics_report(async_session, start=START, end=END))[
            "pulse"
        ]

        assert pulse["billed_seconds"] == 75
        assert pulse["competitor_seconds"] == 120
        assert pulse["seconds_saved_for_customers"] == 45
        # 45 seconds at ₹1.92/min = 144 mpaise*... = 144 paise.
        assert pulse["foregone_paise"] == 144
        assert pulse["seconds_saved_per_call"] == 45.0

    async def test_calls_the_pulse_did_not_affect_are_reported_separately(
        self, db_session, async_session
    ):
        """Otherwise the headline reads as though every customer saw a
        discount, which is the overclaim the honest version avoids."""
        _org_row, workflow = await _org(async_session, "pulse-mixed")
        # Exactly two minutes: nothing to save.
        await _costed_call(
            async_session,
            workflow,
            billable_seconds=120,
            billed_seconds=120,
            charged_paise=384,
        )
        # 20 seconds: a whole minute saved, less our 30.
        await _costed_call(
            async_session,
            workflow,
            billable_seconds=20,
            billed_seconds=30,
            charged_paise=96,
        )

        pulse = (await unit_economics_report(async_session, start=START, end=END))[
            "pulse"
        ]

        assert pulse["calls"] == 2
        assert pulse["unaffected_calls"] == 1
        assert pulse["seconds_saved_for_customers"] == 30

    async def test_it_prices_the_give_away_at_each_account_s_own_rate(
        self, db_session, async_session
    ):
        """An account on a negotiated rate gives away less per second, and the
        total has to reflect that rather than the list price."""
        _org_row, cheap = await _org(async_session, "pulse-cheap")
        await _costed_call(
            async_session,
            cheap,
            billable_seconds=20,
            billed_seconds=30,
            charged_paise=25,
            rate_mpaise=50_000,  # ₹0.50/min
        )

        pulse = (await unit_economics_report(async_session, start=START, end=END))[
            "pulse"
        ]

        # 30 seconds foregone at ₹0.50/min = 25 paise.
        assert pulse["seconds_saved_for_customers"] == 30
        assert pulse["foregone_paise"] == 25


@pytest.mark.asyncio
class TestWhereTheMoneyGoes:
    async def test_cost_splits_by_component_with_shares(
        self, db_session, async_session
    ):
        _org_row, workflow = await _org(async_session, "components")
        await _costed_call(
            async_session,
            workflow,
            billable_seconds=60,
            billed_seconds=60,
            charged_paise=292,
            provider_cost_paise=100,
            items=[
                ("llm", "openai", "gpt-4o-mini", 60),
                ("tts", "elevenlabs", "turbo-v2", 40),
                ("platform", None, None, 192),
            ],
        )

        report = await unit_economics_report(async_session, start=START, end=END)
        by_component = {c["component"]: c for c in report["components"]}

        assert by_component["llm"]["cost_paise"] == 60
        assert by_component["llm"]["paise_per_minute"] == 60
        assert by_component["llm"]["share"] == 0.6
        # The platform line is our fee, not a cost, so it carries no cost share.
        assert by_component["platform"]["share"] is None

    async def test_the_model_league_table_uses_each_model_s_own_minutes(
        self, db_session, async_session
    ):
        """ "Our LLM cost" is not actionable; "gpt-4o costs 3x mini a minute"
        is. That only works if each row divides by its own traffic."""
        _org_row, workflow = await _org(async_session, "models")
        # One minute on the expensive model.
        await _costed_call(
            async_session,
            workflow,
            billable_seconds=60,
            billed_seconds=60,
            charged_paise=192,
            provider_cost_paise=90,
            items=[("llm", "openai", "gpt-4o", 90)],
        )
        # Four minutes on the cheap one, costing more in total but less a minute.
        await _costed_call(
            async_session,
            workflow,
            billable_seconds=240,
            billed_seconds=240,
            charged_paise=768,
            provider_cost_paise=100,
            items=[("llm", "openai", "gpt-4o-mini", 100)],
        )

        models = (await unit_economics_report(async_session, start=START, end=END))[
            "models"
        ]
        by_model = {m["model"]: m for m in models}

        assert by_model["gpt-4o"]["paise_per_minute"] == 90
        assert by_model["gpt-4o-mini"]["paise_per_minute"] == 25
        # Sorted by total cost, so the cheap-per-minute model leads on spend.
        assert models[0]["model"] == "gpt-4o-mini"

    async def test_worst_margin_accounts_come_first(self, db_session, async_session):
        """The account losing money on every minute is the one worth finding,
        and it is rarely the biggest."""
        _big, big_wf = await _org(async_session, "big-healthy")
        _small, small_wf = await _org(async_session, "small-bleeding")
        await _costed_call(
            async_session,
            big_wf,
            billable_seconds=600,
            billed_seconds=600,
            charged_paise=1920,
            provider_cost_paise=400,
        )
        await _costed_call(
            async_session,
            small_wf,
            billable_seconds=60,
            billed_seconds=60,
            charged_paise=192,
            provider_cost_paise=500,  # underwater
        )

        accounts = (await unit_economics_report(async_session, start=START, end=END))[
            "accounts"
        ]

        assert accounts[0]["name"] == "org-small-bleeding"
        assert accounts[0]["margin_paise"] == -308
        assert accounts[0]["margin_paise_per_minute"] == -308


@pytest.mark.asyncio
class TestHonestyMetrics:
    async def test_unpriced_usage_is_counted_and_named(self, db_session, async_session):
        """Every margin figure above is optimistic by exactly this much, so the
        screen has to say so rather than presenting them as complete."""
        _org_row, workflow = await _org(async_session, "uncosted")
        await _costed_call(
            async_session,
            workflow,
            billable_seconds=60,
            billed_seconds=60,
            charged_paise=192,
            uncosted=["llm:anthropic/claude-x", "tts:cartesia"],
        )
        await _costed_call(
            async_session,
            workflow,
            billable_seconds=60,
            billed_seconds=60,
            charged_paise=192,
        )

        uncosted = (await unit_economics_report(async_session, start=START, end=END))[
            "uncosted"
        ]

        assert uncosted["costed_calls"] == 2
        assert uncosted["affected_calls"] == 1
        assert {u["usage"] for u in uncosted["by_usage"]} == {
            "llm:anthropic/claude-x",
            "tts:cartesia",
        }

    async def test_calls_from_before_we_tracked_it_are_not_counted_as_clean(
        self, db_session, async_session
    ):
        """ "We do not know" is not "nothing was missing"."""
        _org_row, workflow = await _org(async_session, "legacy")
        await _costed_call(
            async_session,
            workflow,
            billable_seconds=60,
            billed_seconds=60,
            charged_paise=192,
            uncosted=None,
        )

        uncosted = (await unit_economics_report(async_session, start=START, end=END))[
            "uncosted"
        ]

        assert uncosted["affected_calls"] == 0
        assert uncosted["unknown_calls"] == 1

    async def test_a_stale_exchange_rate_is_flagged(self, db_session, async_session):
        """The list price is fixed in dollars, so a rate nobody has updated is
        a margin leak with no other symptom.

        Age is measured from when the rate was recorded, not from when it took
        effect — the opening row is backdated to the epoch so it covers old
        calls, and reading that as its age would call it fifty years stale.
        """
        from sqlalchemy import update

        old = datetime.now(UTC) - timedelta(days=FX_STALE_AFTER_DAYS + 10)
        await async_session.execute(
            update(UsdInrRateHistoryModel)
            .where(UsdInrRateHistoryModel.effective_to.is_(None))
            .values(created_at=old)
        )

        fx = (await unit_economics_report(async_session, start=START, end=END))["fx"]

        assert fx["is_stale"] is True
        assert fx["age_days"] >= FX_STALE_AFTER_DAYS

    async def test_a_fresh_exchange_rate_is_not_flagged(
        self, db_session, async_session
    ):
        from sqlalchemy import update

        await async_session.execute(
            update(UsdInrRateHistoryModel)
            .where(UsdInrRateHistoryModel.effective_to.is_(None))
            .values(created_at=datetime.now(UTC) - timedelta(days=1))
        )

        fx = (await unit_economics_report(async_session, start=START, end=END))["fx"]

        assert fx["is_stale"] is False
        assert fx["paise_per_usd"] == 9_600


@pytest.mark.asyncio
class TestRangeBounds:
    async def test_a_call_outside_the_range_is_excluded(
        self, db_session, async_session
    ):
        _org_row, workflow = await _org(async_session, "range")
        await _costed_call(
            async_session,
            workflow,
            billable_seconds=60,
            billed_seconds=60,
            charged_paise=192,
            when=WHEN - timedelta(days=5),
        )

        report = await unit_economics_report(async_session, start=START, end=END)
        assert report["calls"] == 0

    async def test_an_uncosted_call_has_no_economics_to_report(
        self, db_session, async_session
    ):
        """A call still in flight would otherwise drag every per-minute figure
        toward zero."""
        _org_row, workflow = await _org(async_session, "in-flight")
        run = await _costed_call(
            async_session,
            workflow,
            billable_seconds=60,
            billed_seconds=60,
            charged_paise=192,
        )
        run.costed_at = None
        await async_session.flush()

        report = await unit_economics_report(async_session, start=START, end=END)
        assert report["calls"] == 0
