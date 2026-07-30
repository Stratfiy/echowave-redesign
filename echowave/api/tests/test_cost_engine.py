"""Per-call cost computation.

Covers the commercial invariants that make the pricing model defensible:
provider costs are passed through untouched, the platform fee is always a
separate line, and an invoice always reconciles against its own line items.
"""

import random

import pytest

from api.enums import CostComponent, RateUnit
from api.services.billing.cost_engine import (
    RateSpec,
    UsageItem,
    compute_call_cost,
)
from api.services.billing.money import (
    DEFAULT_PLATFORM_RATE_MPAISE,
    DEFAULT_PULSE_SECONDS,
)

# Deepgram STT, Sarvam TTS, an LLM and Twilio telephony — a realistic mix of
# all three rate units.
RATES = {
    ("stt", "deepgram", ""): RateSpec(rate_mpaise=25_000, unit=RateUnit.MINUTE),
    ("tts", "sarvam", ""): RateSpec(rate_mpaise=40_000, unit=RateUnit.THOUSAND_CHARS),
    ("llm", "openai", ""): RateSpec(rate_mpaise=12_000, unit=RateUnit.THOUSAND_TOKENS),
    ("telephony", "twilio", ""): RateSpec(rate_mpaise=55_000, unit=RateUnit.MINUTE),
}


def _usage(seconds: int, chars: int, tokens: int):
    return (
        UsageItem(CostComponent.STT, "deepgram", seconds),
        UsageItem(CostComponent.TTS, "sarvam", chars),
        UsageItem(CostComponent.LLM, "openai", tokens),
        UsageItem(CostComponent.TELEPHONY, "twilio", seconds),
    )


class TestDefaultRate:
    def test_byok_call_is_platform_fee_only(self):
        """No provider usage means no provider cost — only our platform fee.

        This is the bring-your-own-keys case: the account pays its model
        providers directly, so there is nothing to pass through.
        """
        cost = compute_call_cost(
            billable_seconds=90,
            platform_rate_mpaise=DEFAULT_PLATFORM_RATE_MPAISE,
        )
        assert cost.total_provider_cost_paise == 0
        # 90s is already a whole number of 15s pulses, so the fee is exactly
        # 1.5 minutes of rate rather than the two minutes a competitor bills.
        assert cost.billed_seconds == 90
        assert cost.platform_fee_paise == 300  # 1.5 min * ₹2.00
        assert cost.total_charged_paise == 300
        assert [line.component for line in cost.line_items] == ["platform"]

    def test_managed_call_itemises_every_component(self):
        cost = compute_call_cost(
            billable_seconds=60,
            platform_rate_mpaise=DEFAULT_PLATFORM_RATE_MPAISE,
            usage=_usage(seconds=60, chars=1000, tokens=1000),
            provider_rates=RATES,
        )
        by_component = {line.component: line for line in cost.line_items}
        assert set(by_component) == {"stt", "tts", "llm", "telephony", "platform"}

        assert by_component["stt"].cost_paise == 25  # 1 min @ 25_000 mpaise
        assert by_component["tts"].cost_paise == 40  # 1k chars @ 40_000
        assert by_component["llm"].cost_paise == 12  # 1k tokens @ 12_000
        assert by_component["telephony"].cost_paise == 55  # 1 min @ 55_000
        assert by_component["platform"].cost_paise == 200  # 1 min @ ₹2.00

        assert cost.total_provider_cost_paise == 25 + 40 + 12 + 55
        assert cost.total_charged_paise == 132 + 200


class TestNoMarkupOnInference:
    def test_provider_line_is_exactly_usage_times_the_configured_rate(self):
        """The commercial differentiator, asserted directly.

        A provider line may never exceed measured usage multiplied by the rate
        on file. If a markup were ever folded in, this fails.
        """
        cost = compute_call_cost(
            billable_seconds=120,
            platform_rate_mpaise=DEFAULT_PLATFORM_RATE_MPAISE,
            usage=(UsageItem(CostComponent.STT, "deepgram", 120),),
            provider_rates=RATES,
        )
        stt = next(line for line in cost.line_items if line.component == "stt")
        # 120s = 2 min at 25_000 mpaise/min = 50_000 mpaise = 50 paise, exactly.
        assert stt.cost_paise == 50
        assert stt.unit_rate_mpaise == 25_000
        assert stt.units == 120

    def test_platform_fee_is_never_folded_into_a_provider_line(self):
        cost = compute_call_cost(
            billable_seconds=60,
            platform_rate_mpaise=DEFAULT_PLATFORM_RATE_MPAISE,
            usage=_usage(60, 1000, 1000),
            provider_rates=RATES,
        )
        platform_lines = [
            line for line in cost.line_items if line.component == "platform"
        ]
        assert len(platform_lines) == 1
        assert platform_lines[0].provider is None
        # Provider total excludes the fee; margin is exactly the fee.
        assert (
            cost.total_charged_paise - cost.total_provider_cost_paise
            == cost.platform_fee_paise
        )

    def test_margin_on_a_managed_call_equals_the_platform_fee_alone(self):
        """Gross margin can only ever come from the platform fee."""
        for seconds in (1, 30, 59, 60, 61, 600, 3607):
            cost = compute_call_cost(
                billable_seconds=seconds,
                platform_rate_mpaise=DEFAULT_PLATFORM_RATE_MPAISE,
                usage=_usage(seconds, seconds * 17, seconds * 23),
                provider_rates=RATES,
            )
            margin = cost.total_charged_paise - cost.total_provider_cost_paise
            assert margin == cost.platform_fee_paise


class TestAccountOverrideAndRateChanges:
    def test_account_override_rate_is_applied(self):
        """An enterprise rate of ₹1.20/min instead of the ₹2.00 default."""
        cost = compute_call_cost(billable_seconds=180, platform_rate_mpaise=120_000)
        assert cost.billable_minutes == 3
        assert cost.platform_fee_paise == 360  # 3 * 120 paise
        assert cost.total_charged_paise == 360

    def test_same_usage_at_two_rates_differs_only_in_the_platform_line(self):
        """An effective-dated rate change must not disturb provider costs.

        Recomputing an old call with the rate that was in force then has to
        reproduce the original number, and the pass-through lines must be
        untouched by the change.
        """
        usage = _usage(seconds=125, chars=3210, tokens=4567)
        old = compute_call_cost(
            billable_seconds=125,
            platform_rate_mpaise=DEFAULT_PLATFORM_RATE_MPAISE,
            usage=usage,
            provider_rates=RATES,
        )
        new = compute_call_cost(
            billable_seconds=125,
            platform_rate_mpaise=150_000,
            usage=usage,
            provider_rates=RATES,
        )
        assert old.total_provider_cost_paise == new.total_provider_cost_paise

        def provider_lines(cost):
            return sorted(
                (li.component, li.units, li.unit_rate_mpaise, li.cost_paise)
                for li in cost.line_items
                if li.component != "platform"
            )

        assert provider_lines(old) == provider_lines(new)
        assert old.platform_fee_paise != new.platform_fee_paise


class TestUncostedUsage:
    def test_usage_without_a_rate_is_reported_not_silently_zero(self):
        """A missing rate must not quietly understate provider cost.

        Costing it at zero would inflate reported margin, which is exactly the
        number this dashboard exists to tell the truth about.
        """
        cost = compute_call_cost(
            billable_seconds=60,
            platform_rate_mpaise=DEFAULT_PLATFORM_RATE_MPAISE,
            usage=(UsageItem(CostComponent.STT, "unpriced-vendor", 60),),
            provider_rates=RATES,
        )
        assert len(cost.uncosted) == 1
        assert cost.uncosted[0].provider == "unpriced-vendor"
        assert cost.total_provider_cost_paise == 0
        assert [line.component for line in cost.line_items] == ["platform"]


class TestInvoiceReconciliation:
    def test_line_items_sum_to_the_total_for_one_call(self):
        cost = compute_call_cost(
            billable_seconds=137,
            platform_rate_mpaise=DEFAULT_PLATFORM_RATE_MPAISE,
            usage=_usage(137, 4321, 8765),
            provider_rates=RATES,
        )
        assert sum(li.cost_paise for li in cost.line_items) == cost.total_charged_paise

    def test_ten_thousand_calls_reconcile_exactly(self):
        """The invoice total must equal the summed line items, exactly.

        Ten thousand calls with deliberately awkward durations and usage — the
        values most likely to expose a rounding drift, since each one lands
        part-way between paise. Any per-call rounding that did not feed the
        total would show up as a non-zero difference here.
        """
        rng = random.Random(20260729)  # fixed seed: a failure must be reproducible

        summed_line_items = 0
        summed_totals = 0
        summed_provider = 0
        summed_platform = 0

        for _ in range(10_000):
            seconds = rng.randint(1, 3600)
            # Rates chosen so most products land on a fraction of a paise.
            rates = {
                ("stt", "deepgram", ""): RateSpec(
                    rate_mpaise=rng.randint(1, 99_999), unit=RateUnit.MINUTE
                ),
                ("tts", "sarvam", ""): RateSpec(
                    rate_mpaise=rng.randint(1, 99_999),
                    unit=RateUnit.THOUSAND_CHARS,
                ),
                ("llm", "openai", ""): RateSpec(
                    rate_mpaise=rng.randint(1, 99_999),
                    unit=RateUnit.THOUSAND_TOKENS,
                ),
                ("telephony", "twilio", ""): RateSpec(
                    rate_mpaise=rng.randint(1, 99_999), unit=RateUnit.MINUTE
                ),
            }
            cost = compute_call_cost(
                billable_seconds=seconds,
                platform_rate_mpaise=rng.choice([200_000, 150_000, 120_000, 99_999]),
                usage=_usage(
                    seconds=seconds,
                    chars=rng.randint(0, 20_000),
                    tokens=rng.randint(0, 30_000),
                ),
                provider_rates=rates,
            )

            # Per call: the total is the sum of its own lines.
            assert sum(li.cost_paise for li in cost.line_items) == (
                cost.total_charged_paise
            )
            # Every stored figure is a whole number of paise — never a float.
            for line in cost.line_items:
                assert isinstance(line.cost_paise, int)

            summed_line_items += sum(li.cost_paise for li in cost.line_items)
            summed_totals += cost.total_charged_paise
            summed_provider += cost.total_provider_cost_paise
            summed_platform += cost.platform_fee_paise

        # Across the whole run: no drift accumulated.
        assert summed_line_items == summed_totals
        # And the split still holds in aggregate, so margin reporting is exact.
        assert summed_provider + summed_platform == summed_totals
        assert summed_totals > 0  # the fixture actually exercised something


class TestValidation:
    def test_rejects_negative_duration(self):
        with pytest.raises(ValueError):
            compute_call_cost(
                billable_seconds=-1,
                platform_rate_mpaise=DEFAULT_PLATFORM_RATE_MPAISE,
            )

    def test_zero_second_call_costs_nothing(self):
        cost = compute_call_cost(
            billable_seconds=0,
            platform_rate_mpaise=DEFAULT_PLATFORM_RATE_MPAISE,
            usage=_usage(0, 0, 0),
            provider_rates=RATES,
        )
        assert cost.billable_minutes == 0
        assert cost.total_charged_paise == 0
        assert sum(li.cost_paise for li in cost.line_items) == 0


class TestPulseBilling:
    """The pulse is the commercial differentiator, so its effect on a receipt
    is asserted rather than left to the arithmetic in money.py."""

    def test_a_short_call_is_not_rounded_up_to_a_whole_minute(self):
        """The case the pitch is built on. A 20-second call bills 30 seconds
        with us and a full minute everywhere else."""
        ours = compute_call_cost(
            billable_seconds=20, platform_rate_mpaise=DEFAULT_PLATFORM_RATE_MPAISE
        )
        theirs = compute_call_cost(
            billable_seconds=20,
            platform_rate_mpaise=DEFAULT_PLATFORM_RATE_MPAISE,
            pulse_seconds=60,
        )

        assert ours.billed_seconds == 30
        assert theirs.billed_seconds == 60
        assert ours.total_charged_paise == 100  # 0.5 min @ ₹2.00
        assert theirs.total_charged_paise == 200

    def test_a_sixty_second_pulse_reproduces_the_old_whole_minute_billing(self):
        """One parameter separates us from the competition, not two code
        paths — which is what makes the comparison honest."""
        for seconds in (1, 45, 60, 61, 90, 222):
            cost = compute_call_cost(
                billable_seconds=seconds,
                platform_rate_mpaise=DEFAULT_PLATFORM_RATE_MPAISE,
                pulse_seconds=60,
            )
            assert cost.billed_seconds == cost.billable_minutes * 60

    def test_the_platform_line_reports_the_seconds_it_charged_for(self):
        """A receipt that said "2 minutes" for a 62-second call would be the
        thing the pulse exists to stop."""
        cost = compute_call_cost(
            billable_seconds=62, platform_rate_mpaise=DEFAULT_PLATFORM_RATE_MPAISE
        )
        platform = next(
            line for line in cost.line_items if line.component == "platform"
        )
        assert platform.units == 75
        assert cost.billed_seconds == 75

    def test_the_pulse_never_charges_for_less_time_than_the_call_took(self):
        """Rounding up is the invariant. Down would be giving away revenue on
        every single call, silently."""
        for seconds in range(0, 400, 7):
            cost = compute_call_cost(
                billable_seconds=seconds,
                platform_rate_mpaise=DEFAULT_PLATFORM_RATE_MPAISE,
            )
            assert cost.billed_seconds >= seconds
            assert cost.billed_seconds % DEFAULT_PULSE_SECONDS == 0

    def test_totals_still_reconcile_against_line_items_under_a_pulse(self):
        """The rounding invariant must survive the change to how the platform
        quantity is derived."""
        random.seed(20260730)
        for _ in range(2000):
            cost = compute_call_cost(
                billable_seconds=random.randint(0, 3600),
                platform_rate_mpaise=random.choice([192_000, 200_000, 144_000]),
                pulse_seconds=random.choice([1, 15, 30, 60]),
                usage=_usage(
                    seconds=random.randint(0, 3600),
                    chars=random.randint(0, 9000),
                    tokens=random.randint(0, 9000),
                ),
                provider_rates=RATES,
            )
            assert cost.total_charged_paise == sum(
                line.cost_paise for line in cost.line_items
            )
