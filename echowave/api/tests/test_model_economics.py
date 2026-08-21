"""Model-wise usage and cost, and the payments behind the top-ups.

Three things here are easy to get wrong in a way that still produces a
plausible-looking number:

**Summing units across components.** ``call_cost_items.units`` is seconds for
speech-to-text, characters for synthesis and tokens for the language model. A
total over that column adds tokens to seconds and reads as a large, confident,
meaningless figure.

**Reporting one cost as both.** Every line stores what the customer paid *and*
what the vendor charged us. Collapsing them — or letting the platform fee claim
a vendor cost it never had — makes the margin wrong in the direction nobody
questions.

**Answering conversion and collection off the same timestamp.** An order
created on Tuesday and paid on Wednesday belongs to Tuesday's conversion and
Wednesday's cash. Keying both on one column makes the two numbers disagree.
"""

from datetime import UTC, datetime, timedelta

import pytest

from api.db import billing_dashboard_client as dash
from api.db.models import (
    CallCostItemModel,
    CreditLedgerModel,
    DailyOrganizationRollupModel,
    OrganizationModel,
    PaymentModel,
    ProviderRateModel,
    UserModel,
    WorkflowModel,
    WorkflowRunModel,
)
from api.enums import CreditLedgerKind


def _today():
    return datetime.now(UTC).date()


def _wide():
    """A window that cannot miss "now" whatever the hour.

    Buckets are truncated in IST, so a row written after 18:30 UTC lands on the
    *next* IST day. A single-day window would pass all morning and fail all
    evening, which is the worst kind of test.
    """
    return _today() - timedelta(days=60), _today() + timedelta(days=1)


async def _org(async_session, slug: str):
    org = OrganizationModel(provider_id=f"org-{slug}", quota_decibyl_tokens=0)
    user = UserModel(provider_id=f"user-{slug}")
    async_session.add_all([org, user])
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


async def _run(async_session, workflow, *, seconds=60):
    run = WorkflowRunModel(
        name="run",
        workflow_id=workflow.id,
        mode="twilio",
        created_at=datetime.now(UTC),
        billable_seconds=seconds,
        initial_context={},
        gathered_context={},
    )
    async_session.add(run)
    await async_session.flush()
    return run


async def _line(
    async_session,
    run,
    *,
    component,
    provider=None,
    model=None,
    units=0,
    charged=0,
    our_cost=0,
    rate=1,
):
    item = CallCostItemModel(
        workflow_run_id=run.id,
        component=component,
        provider=provider,
        model=model,
        units=units,
        unit_rate_mpaise=rate,
        cost_paise=charged,
        provider_cost_paise=our_cost,
        created_at=datetime.now(UTC),
    )
    async_session.add(item)
    await async_session.flush()
    return item


def _find(rows, model):
    return next(row for row in rows if row["model"] == model)


@pytest.mark.asyncio
class TestModelMargin:
    async def test_each_model_reports_what_it_earned_and_what_it_cost(
        self, async_session
    ):
        _, workflow = await _org(async_session, "margin")
        run = await _run(async_session, workflow)
        await _line(
            async_session,
            run,
            component="llm",
            provider="openai",
            model="gpt-4o-mini",
            units=1000,
            charged=120,
            our_cost=100,
        )
        start, end = _wide()

        report = await dash.model_usage(async_session, start=start, end=end)
        row = _find(report["models"], "gpt-4o-mini")

        assert row["charged_paise"] == 120
        assert row["our_cost_paise"] == 100
        assert row["margin_paise"] == 20
        assert row["margin_pct"] == pytest.approx(20 / 120)

    async def test_a_loss_making_model_shows_a_negative_margin(self, async_session):
        """Not clamped at zero. A model priced below what the vendor charges is
        exactly what this screen exists to surface, and hiding the sign would
        make it look merely unprofitable rather than actively losing money."""
        _, workflow = await _org(async_session, "loss")
        run = await _run(async_session, workflow)
        await _line(
            async_session,
            run,
            component="tts",
            provider="elevenlabs",
            model="turbo-v2",
            units=5000,
            charged=50,
            our_cost=80,
        )
        start, end = _wide()

        row = _find(
            (await dash.model_usage(async_session, start=start, end=end))["models"],
            "turbo-v2",
        )
        assert row["margin_paise"] == -30
        assert row["margin_pct"] < 0

    async def test_the_platform_fee_is_all_margin(self, async_session):
        """It has no vendor behind it. A platform line that reported a provider
        cost would be charging us for our own revenue."""
        _, workflow = await _org(async_session, "platform")
        run = await _run(async_session, workflow)
        await _line(
            async_session, run, component="platform", units=60, charged=500, our_cost=0
        )
        start, end = _wide()

        report = await dash.model_usage(
            async_session, start=start, end=end, component="platform"
        )
        row = report["models"][0]
        assert row["our_cost_paise"] == 0
        assert row["margin_paise"] == row["charged_paise"] == 500
        assert row["margin_pct"] == 1.0

    async def test_a_zero_revenue_row_has_no_margin_percentage(self, async_session):
        """None, not zero. A share of nothing is undefined, and "0%" reads as
        "we made no margin" — a different and much more alarming claim."""
        _, workflow = await _org(async_session, "zero")
        run = await _run(async_session, workflow)
        await _line(
            async_session,
            run,
            component="stt",
            provider="deepgram",
            model="nova-3",
            units=60,
            charged=0,
            our_cost=0,
        )
        start, end = _wide()

        row = _find(
            (await dash.model_usage(async_session, start=start, end=end))["models"],
            "nova-3",
        )
        assert row["margin_pct"] is None

    async def test_two_models_from_one_provider_are_separate_rows(self, async_session):
        """Rates differ by more than an order of magnitude between models from
        the same vendor, so grouping on provider alone would average away the
        only distinction the screen is for."""
        _, workflow = await _org(async_session, "two-models")
        run = await _run(async_session, workflow)
        await _line(
            async_session,
            run,
            component="llm",
            provider="openai",
            model="gpt-4o",
            units=100,
            charged=900,
            our_cost=800,
        )
        await _line(
            async_session,
            run,
            component="llm",
            provider="openai",
            model="gpt-4o-mini",
            units=100,
            charged=30,
            our_cost=25,
        )
        start, end = _wide()

        models = (await dash.model_usage(async_session, start=start, end=end))["models"]
        assert _find(models, "gpt-4o")["charged_paise"] == 900
        assert _find(models, "gpt-4o-mini")["charged_paise"] == 30


@pytest.mark.asyncio
class TestUnitsAreNeverAddedAcrossComponents:
    async def test_every_row_carries_the_unit_its_count_is_in(self, async_session):
        _, workflow = await _org(async_session, "units")
        run = await _run(async_session, workflow)
        async_session.add_all(
            [
                ProviderRateModel(
                    provider="deepgram",
                    model="",
                    component="stt",
                    unit="minute",
                    rate_mpaise=5,
                    effective_from=datetime.now(UTC) - timedelta(days=90),
                ),
                ProviderRateModel(
                    provider="openai",
                    model="",
                    component="llm",
                    unit="1k_tokens",
                    rate_mpaise=5,
                    effective_from=datetime.now(UTC) - timedelta(days=90),
                ),
            ]
        )
        await async_session.flush()
        await _line(
            async_session,
            run,
            component="stt",
            provider="deepgram",
            model="nova-3",
            units=60,
            charged=10,
            our_cost=8,
        )
        await _line(
            async_session,
            run,
            component="llm",
            provider="openai",
            model="gpt-4o",
            units=2000,
            charged=40,
            our_cost=30,
        )
        start, end = _wide()

        models = (await dash.model_usage(async_session, start=start, end=end))["models"]
        assert _find(models, "nova-3")["unit_label"] == "seconds"
        assert _find(models, "gpt-4o")["unit_label"] == "tokens"

    async def test_the_totals_block_sums_money_and_not_units(self, async_session):
        """The guard against the obvious mistake. Money is comparable across
        components; the raw counts are not, so there is deliberately no total
        for them to be summed into."""
        _, workflow = await _org(async_session, "totals")
        run = await _run(async_session, workflow)
        await _line(
            async_session,
            run,
            component="stt",
            provider="deepgram",
            units=60,
            charged=10,
            our_cost=8,
        )
        await _line(
            async_session,
            run,
            component="llm",
            provider="openai",
            units=2000,
            charged=40,
            our_cost=30,
        )
        start, end = _wide()

        report = await dash.model_usage(async_session, start=start, end=end)
        assert report["totals"]["charged_paise"] == 50
        assert report["totals"]["our_cost_paise"] == 38
        assert report["totals"]["margin_paise"] == 12
        assert "units" not in report["totals"]

    async def test_a_model_without_its_own_rate_uses_the_provider_fallback_unit(
        self, async_session
    ):
        """A model with no rate row of its own was priced at the provider-wide
        rate, so it is quoted in that rate's unit — not in a guess."""
        _, workflow = await _org(async_session, "fallback-unit")
        run = await _run(async_session, workflow)
        async_session.add(
            ProviderRateModel(
                provider="elevenlabs",
                model="",
                component="tts",
                unit="1k_chars",
                rate_mpaise=20,
                effective_from=datetime.now(UTC) - timedelta(days=90),
            )
        )
        await async_session.flush()
        await _line(
            async_session,
            run,
            component="tts",
            provider="elevenlabs",
            model="some-unlisted-voice",
            units=1200,
            charged=24,
            our_cost=20,
        )
        start, end = _wide()

        row = _find(
            (await dash.model_usage(async_session, start=start, end=end))["models"],
            "some-unlisted-voice",
        )
        assert row["unit_label"] == "characters"


@pytest.mark.asyncio
class TestScoping:
    async def test_a_component_filter_excludes_everything_else(self, async_session):
        _, workflow = await _org(async_session, "filter")
        run = await _run(async_session, workflow)
        await _line(async_session, run, component="llm", provider="openai", charged=10)
        await _line(
            async_session, run, component="tts", provider="cartesia", charged=10
        )
        start, end = _wide()

        report = await dash.model_usage(
            async_session, start=start, end=end, component="llm"
        )
        assert {row["component"] for row in report["models"]} == {"llm"}

    async def test_one_account_sees_only_its_own_lines(self, async_session):
        """The same query backs the customer-facing usage route, where the id is
        forced from the authenticated user. A leak here is a cross-tenant leak."""
        org_a, workflow_a = await _org(async_session, "scope-a")
        _, workflow_b = await _org(async_session, "scope-b")
        run_a = await _run(async_session, workflow_a)
        run_b = await _run(async_session, workflow_b)
        await _line(
            async_session,
            run_a,
            component="llm",
            provider="openai",
            model="mine",
            charged=10,
        )
        await _line(
            async_session,
            run_b,
            component="llm",
            provider="openai",
            model="theirs",
            charged=999,
        )
        start, end = _wide()

        report = await dash.model_usage(
            async_session, start=start, end=end, organization_id=org_a.id
        )
        assert {row["model"] for row in report["models"]} == {"mine"}
        assert report["totals"]["charged_paise"] == 10

    async def test_an_empty_window_is_empty_rather_than_an_error(self, async_session):
        report = await dash.model_usage(
            async_session,
            start=_today() - timedelta(days=4000),
            end=_today() - timedelta(days=3990),
        )
        assert report["models"] == []
        assert report["totals"]["margin_pct"] is None


async def _payment(
    async_session,
    org,
    *,
    status="paid",
    amount=100_000,
    gross=None,
    tax=0,
    created_days_ago=0,
    paid_days_ago=0,
    order="order",
):
    now = datetime.now(UTC)
    payment = PaymentModel(
        organization_id=org.id,
        provider="razorpay",
        order_id=f"{order}-{org.id}",
        payment_id=f"pay-{order}-{org.id}" if status == "paid" else None,
        amount_paise=amount,
        gross_paise=gross,
        igst_paise=tax or None,
        status=status,
        created_at=now - timedelta(days=created_days_ago),
        paid_at=(now - timedelta(days=paid_days_ago)) if status == "paid" else None,
    )
    async_session.add(payment)
    await async_session.flush()
    return payment


@pytest.mark.asyncio
class TestPaymentsAndTopUps:
    async def test_collected_reports_gross_and_net_separately(self, async_session):
        """Net is what reaches the ledger as credit; gross is what the customer
        was charged. Reporting one as the other overstates either the revenue
        or the credit granted, depending which way round it is done."""
        org, _ = await _org(async_session, "gross-net")
        await _payment(async_session, org, amount=100_000, gross=118_000, tax=18_000)
        start, end = _wide()

        report = await dash.payments_summary(async_session, start=start, end=end)
        assert report["collected"]["net_paise"] == 100_000
        assert report["collected"]["gross_paise"] == 118_000
        assert report["collected"]["tax_paise"] == 18_000

    async def test_a_zero_rated_payment_falls_back_to_its_net(self, async_session):
        """gross_paise is nullable — payments taken before GST existed have no
        split recorded. Those were zero-rated, so gross equals net; treating the
        NULL as zero would silently erase the payment from the revenue total."""
        org, _ = await _org(async_session, "zero-rated")
        await _payment(async_session, org, amount=50_000, gross=None)
        start, end = _wide()

        report = await dash.payments_summary(async_session, start=start, end=end)
        assert report["collected"]["gross_paise"] == 50_000

    async def test_only_settled_payments_count_as_money(self, async_session):
        org, _ = await _org(async_session, "unsettled")
        await _payment(async_session, org, status="created", amount=90_000)
        await _payment(
            async_session, org, status="failed", amount=90_000, order="second"
        )
        start, end = _wide()

        report = await dash.payments_summary(async_session, start=start, end=end)
        assert report["collected"]["payments"] == 0
        assert report["collected"]["net_paise"] == 0

    async def test_an_abandoned_order_is_neither_paid_nor_failed(self, async_session):
        """It is the only one of the three that a change to the checkout page
        can move, so it gets its own number rather than being folded into
        failures."""
        org, _ = await _org(async_session, "abandoned")
        await _payment(async_session, org, status="created")
        start, end = _wide()

        cohort = (await dash.payments_summary(async_session, start=start, end=end))[
            "cohort"
        ]
        assert cohort["abandoned"] == 1
        assert cohort["failed"] == 0
        assert cohort["paid"] == 0

    async def test_conversion_follows_the_order_not_the_settlement(self, async_session):
        """An order created on day one and paid on day two converted on day one.
        Keying the cohort on paid_at would let a window contain the payment but
        not the order it converted, and report a rate above 100%."""
        org, _ = await _org(async_session, "cohort")
        await _payment(async_session, org, created_days_ago=5, paid_days_ago=0)
        start, end = _wide()

        report = await dash.payments_summary(async_session, start=start, end=end)
        assert report["cohort"]["started"] == 1
        assert report["cohort"]["paid"] == 1
        assert report["cohort"]["conversion"] == 1.0

    async def test_an_empty_window_has_no_conversion_rate(self, async_session):
        report = await dash.payments_summary(
            async_session,
            start=_today() - timedelta(days=4000),
            end=_today() - timedelta(days=3990),
        )
        assert report["cohort"]["conversion"] is None
        assert report["collected"]["average_topup_paise"] is None
        assert report["series"] == []

    async def test_credit_granted_without_a_payment_is_visible(self, async_session):
        """Staff credit, trial grants and manual corrections all reach the
        ledger without a payment behind them. The gap is a reconciliation
        signal — and it can only be read if both halves are reported."""
        org, _ = await _org(async_session, "unpaid-credit")
        async_session.add(
            CreditLedgerModel(
                organization_id=org.id,
                delta_paise=25_000,
                kind=CreditLedgerKind.TOPUP.value,
                balance_after_paise=25_000,
            )
        )
        await async_session.flush()
        start, end = _wide()

        report = await dash.payments_summary(async_session, start=start, end=end)
        assert report["ledger"]["credited_paise"] == 25_000
        assert report["collected"]["net_paise"] == 0
        assert report["ledger"]["unpaid_credit_paise"] == 25_000

    async def test_usage_debits_are_not_counted_as_top_ups(self, async_session):
        """Filtered on kind, not on the sign of delta_paise — the same rule the
        activation funnel uses. A reservation release is positive too."""
        org, _ = await _org(async_session, "kinds")
        async_session.add_all(
            [
                CreditLedgerModel(
                    organization_id=org.id,
                    delta_paise=-500,
                    kind=CreditLedgerKind.USAGE.value,
                    balance_after_paise=0,
                ),
                CreditLedgerModel(
                    organization_id=org.id,
                    delta_paise=9_000,
                    kind=CreditLedgerKind.ADJUSTMENT.value,
                    balance_after_paise=9_000,
                ),
            ]
        )
        await async_session.flush()
        start, end = _wide()

        report = await dash.payments_summary(async_session, start=start, end=end)
        assert report["ledger"]["credited_paise"] == 0

    async def test_the_daily_series_buckets_on_when_the_money_settled(
        self, async_session
    ):
        org, _ = await _org(async_session, "series")
        await _payment(async_session, org, created_days_ago=10, paid_days_ago=1)
        start, end = _wide()

        series = (await dash.payments_summary(async_session, start=start, end=end))[
            "series"
        ]
        assert len(series) == 1
        settled_on = (datetime.now(UTC) - timedelta(days=1)).date()
        # IST is ahead of UTC, so a settlement late in the UTC day lands on the
        # next IST day. Either is correct; a bucket ten days back is not.
        assert series[0]["period"] in {
            settled_on.isoformat(),
            (settled_on + timedelta(days=1)).isoformat(),
        }

    async def test_top_accounts_are_ordered_by_what_they_paid(self, async_session):
        big, _ = await _org(async_session, "big-spender")
        small, _ = await _org(async_session, "small-spender")
        await _payment(async_session, big, amount=500_000)
        await _payment(async_session, small, amount=1_000)
        start, end = _wide()

        top = (await dash.payments_summary(async_session, start=start, end=end))[
            "top_accounts"
        ]
        assert top[0]["organization_id"] == big.id
        assert top[0]["net_paise"] == 500_000

    async def test_one_account_sees_only_its_own_payments(self, async_session):
        mine, _ = await _org(async_session, "pay-mine")
        theirs, _ = await _org(async_session, "pay-theirs")
        await _payment(async_session, mine, amount=1_000)
        await _payment(async_session, theirs, amount=900_000)
        start, end = _wide()

        report = await dash.payments_summary(
            async_session, start=start, end=end, organization_id=mine.id
        )
        assert report["collected"]["net_paise"] == 1_000
        assert report["collected"]["paying_accounts"] == 1


@pytest.mark.asyncio
class TestDailySeriesSuccessRate:
    """The revenue-trend and success-rate-trend superadmin charts both read
    ``daily_series`` — this is the day it started carrying ``answered_calls``
    and ``success_rate`` rather than only ``calls``."""

    async def test_success_rate_divides_answered_by_total(self, async_session):
        org, _ = await _org(async_session, "rollup-success")
        day = _today()
        async_session.add(
            DailyOrganizationRollupModel(
                organization_id=org.id,
                day=day,
                calls=4,
                answered_calls=3,
            )
        )
        await async_session.flush()

        series = await dash.daily_series(async_session, start=day, end=day)

        assert len(series) == 1
        assert series[0]["calls"] == 4
        assert series[0]["answered_calls"] == 3
        assert series[0]["success_rate"] == 0.75

    async def test_a_day_with_no_calls_reports_no_rate_rather_than_zero(
        self, async_session
    ):
        """0% and "nothing happened" render identically as a number; only one
        of them is a problem, so they must not share a value."""
        day = _today()

        series = await dash.daily_series(async_session, start=day, end=day)

        assert series[0]["calls"] == 0
        assert series[0]["success_rate"] is None
