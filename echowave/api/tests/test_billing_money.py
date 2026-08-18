"""Integer money arithmetic: rounding, units and the paise/millipaise split."""

import pytest

from api.enums import RateUnit
from api.services.billing.money import (
    COMMITTED_PLATFORM_RATE_MICROS_USD,
    DEFAULT_PLATFORM_RATE_MICROS_USD,
    DEFAULT_PLATFORM_RATE_MPAISE,
    DEFAULT_PULSE_SECONDS,
    DEFAULT_USD_INR_PAISE,
    billable_minutes,
    billed_seconds,
    cost_paise,
    format_micros_usd,
    format_paise,
    mpaise_to_micros_usd,
    platform_fee_paise,
    round_half_up_div,
    usd_to_mpaise,
)


def test_global_default_is_two_rupees_per_minute():
    """₹2.00/min = 200 paise = 200_000 millipaise.

    Guards the units: a factor-of-100 slip here silently misprices everything.
    """
    assert DEFAULT_PLATFORM_RATE_MPAISE == 200_000
    assert (
        platform_fee_paise(billable_minutes=1, rate_mpaise=DEFAULT_PLATFORM_RATE_MPAISE)
        == 200
    )
    assert format_paise(200) == "₹2.00"


def test_the_committed_price_is_two_cents_a_minute():
    """$0.02/min in micro-dollars — the number quoted in marketing.

    Guards the units the same way the rupee test above does: a factor-of-1000
    slip here misprices every call on the platform.
    """
    assert COMMITTED_PLATFORM_RATE_MICROS_USD == 20_000
    assert format_micros_usd(COMMITTED_PLATFORM_RATE_MICROS_USD) == "$0.02"


def test_the_default_is_the_uncommitted_price_not_the_committed_one():
    """The constant is a floor for a rate card with no tier configured, so it
    has to be the dearer of the two prices.

    Defaulting to the committed rate would hand the discount to every
    unconfigured account, and nothing would surface it — an under-charge
    produces no error and no complaint, so it would be found in a margin
    report months later or not at all.
    """
    assert DEFAULT_PLATFORM_RATE_MICROS_USD > COMMITTED_PLATFORM_RATE_MICROS_USD
    assert format_micros_usd(DEFAULT_PLATFORM_RATE_MICROS_USD) == "$0.0365"


class TestUsdToInr:
    def test_the_committed_price_converts_to_the_expected_rupee_rate(self):
        """$0.02 at ₹96 is ₹1.92 a minute — 192 paise, 192_000 millipaise."""
        rate = usd_to_mpaise(
            micros_usd=COMMITTED_PLATFORM_RATE_MICROS_USD,
            usd_inr_paise=DEFAULT_USD_INR_PAISE,
        )
        assert rate == 192_000
        assert platform_fee_paise(billable_minutes=1, rate_mpaise=rate) == 192
        assert format_paise(192) == "₹1.92"

    def test_the_uncommitted_price_is_three_fifty(self):
        """₹3.50/min is the number the pricing page shows without a
        commitment, so the constant has to land on it exactly at the fallback
        exchange rate — not ₹3.49 or ₹3.51."""
        rate = usd_to_mpaise(
            micros_usd=DEFAULT_PLATFORM_RATE_MICROS_USD,
            usd_inr_paise=DEFAULT_USD_INR_PAISE,
        )
        assert platform_fee_paise(billable_minutes=1, rate_mpaise=rate) == 350
        assert format_paise(350) == "₹3.50"

    def test_the_rupee_price_moves_with_the_dollar(self):
        """The point of pricing in USD: the dollar figure holds while the rupee
        amount tracks the market."""
        weak = usd_to_mpaise(micros_usd=20_000, usd_inr_paise=11_000)  # ₹110
        strong = usd_to_mpaise(micros_usd=20_000, usd_inr_paise=8_000)  # ₹80
        assert weak == 220_000
        assert strong == 160_000

    def test_a_fractional_rate_is_not_lost_to_rounding(self):
        """₹83.37 is not a round number, and neither is the result. Millipaise
        exist precisely so this survives."""
        assert usd_to_mpaise(micros_usd=20_000, usd_inr_paise=8_337) == 166_740

    def test_the_reverse_conversion_is_for_reporting_only(self):
        """Round-tripping is close but not guaranteed exact, which is why
        billing never goes through it."""
        assert (
            mpaise_to_micros_usd(mpaise=192_000, usd_inr_paise=DEFAULT_USD_INR_PAISE)
            == 20_000
        )

    @pytest.mark.parametrize("bad_rate", [0, -1])
    def test_a_non_positive_exchange_rate_is_refused(self, bad_rate):
        """Zero would make every call free and a negative would pay customers
        to call. Both are better as an exception than as an invoice."""
        with pytest.raises(ValueError):
            usd_to_mpaise(micros_usd=20_000, usd_inr_paise=bad_rate)


class TestPulseBilling:
    def test_the_default_pulse_is_fifteen_seconds(self):
        assert DEFAULT_PULSE_SECONDS == 15

    @pytest.mark.parametrize(
        "actual, expected",
        [
            (0, 0),
            (1, 15),
            (15, 15),
            (16, 30),
            (62, 75),
            (180, 180),
            (181, 195),
        ],
    )
    def test_time_rounds_up_to_a_whole_pulse(self, actual, expected):
        assert billed_seconds(actual, DEFAULT_PULSE_SECONDS) == expected

    def test_a_sixty_second_pulse_reproduces_whole_minute_billing(self):
        """The pulse is a setting, not a rewrite: at 60 it is exactly what
        every competitor does, which is what makes the comparison fair."""
        for seconds in (1, 59, 60, 61, 119, 120):
            assert billed_seconds(seconds, 60) == billable_minutes(seconds) * 60

    def test_the_pulse_never_bills_less_than_the_call_took(self):
        """Rounding is up, always. Rounding down would give away time."""
        for seconds in range(0, 300):
            assert billed_seconds(seconds, DEFAULT_PULSE_SECONDS) >= seconds

    def test_what_the_pulse_is_worth_on_a_short_call(self):
        """The claim the pricing rests on, as arithmetic rather than a slogan.

        A 45-second call: competitors bill a whole minute, we bill 45 seconds.
        """
        ours = billed_seconds(45, 15)
        theirs = billed_seconds(45, 60)
        assert ours == 45 and theirs == 60
        assert (theirs - ours) / theirs == 0.25

    def test_what_the_pulse_is_worth_on_a_long_call(self):
        """And the honest other half: on a four-minute call it is almost
        nothing, so the pitch belongs on short, high-volume traffic."""
        ours = billed_seconds(222, 15)
        theirs = billed_seconds(222, 60)
        assert ours == 225 and theirs == 240
        assert round((theirs - ours) / theirs, 3) == 0.062

    @pytest.mark.parametrize("bad_pulse", [0, -15])
    def test_a_non_positive_pulse_is_refused(self, bad_pulse):
        with pytest.raises(ValueError):
            billed_seconds(60, bad_pulse)

    def test_rejects_negative_duration(self):
        with pytest.raises(ValueError):
            billed_seconds(-1, 15)


class TestRoundHalfUpDiv:
    @pytest.mark.parametrize(
        "numerator,denominator,expected",
        [
            (0, 10, 0),
            (4, 10, 0),  # 0.4 -> 0
            (5, 10, 1),  # 0.5 -> 1, half away from zero (not banker's)
            (6, 10, 1),
            (14, 10, 1),
            (15, 10, 2),  # 1.5 -> 2
            (25, 10, 3),  # 2.5 -> 3 (banker's rounding would give 2)
            (-5, 10, -1),
            (-15, 10, -2),
            (7, 1, 7),
        ],
    )
    def test_rounds_halves_away_from_zero(self, numerator, denominator, expected):
        assert round_half_up_div(numerator, denominator) == expected

    def test_rejects_non_positive_denominator(self):
        with pytest.raises(ValueError):
            round_half_up_div(1, 0)

    def test_is_exact_for_values_a_float_would_misrepresent(self):
        # 0.1 + 0.2 != 0.3 in binary floating point; integer math has no such
        # failure mode, which is the whole reason money never touches a float.
        assert round_half_up_div(3, 10) == 0
        assert round_half_up_div(10**18 + 5, 10) == 10**17 + 1


class TestBillableMinutes:
    @pytest.mark.parametrize(
        "seconds,expected",
        [(0, 0), (1, 1), (59, 1), (60, 1), (61, 2), (119, 2), (120, 2), (3600, 60)],
    )
    def test_ceilings_to_whole_minutes(self, seconds, expected):
        assert billable_minutes(seconds) == expected

    def test_rejects_negative(self):
        with pytest.raises(ValueError):
            billable_minutes(-1)


class TestCostPaise:
    def test_per_minute_rate_takes_quantity_in_seconds(self):
        # 90s at 60_000 mpaise/min = 1.5 min * 60 paise = 90 paise
        assert cost_paise(quantity=90, rate_mpaise=60_000, unit=RateUnit.MINUTE) == 90

    def test_per_1k_chars(self):
        # 2500 chars at 40_000 mpaise / 1k chars = 2.5 * 40 paise = 100 paise
        assert (
            cost_paise(quantity=2500, rate_mpaise=40_000, unit=RateUnit.THOUSAND_CHARS)
            == 100
        )

    def test_per_1k_tokens(self):
        # 1500 tokens at 20_000 mpaise / 1k tokens = 1.5 * 20 paise = 30 paise
        assert (
            cost_paise(quantity=1500, rate_mpaise=20_000, unit=RateUnit.THOUSAND_TOKENS)
            == 30
        )

    def test_sub_paise_rate_is_not_lost_to_the_rate_itself(self):
        """A rate below one paise per unit must survive being stored.

        Deepgram-style rates land at fractions of a paise per audio-minute. If
        rates were held in paise this would round to 0 and the provider cost
        would vanish; millipaise keeps it.
        """
        # 1 mpaise/min is 1/1000 paise per minute — 600 minutes to reach 1 paise.
        assert cost_paise(quantity=60, rate_mpaise=1, unit=RateUnit.MINUTE) == 0
        assert cost_paise(quantity=60 * 600, rate_mpaise=1, unit=RateUnit.MINUTE) == 1

    def test_zero_quantity_costs_nothing(self):
        assert cost_paise(quantity=0, rate_mpaise=999_999, unit=RateUnit.MINUTE) == 0

    def test_rejects_negative_quantity(self):
        with pytest.raises(ValueError):
            cost_paise(quantity=-1, rate_mpaise=1, unit=RateUnit.MINUTE)

    def test_rejects_unknown_unit(self):
        with pytest.raises(ValueError):
            cost_paise(quantity=1, rate_mpaise=1, unit="per_furlong")


class TestFormatPaise:
    @pytest.mark.parametrize(
        "paise,expected",
        [
            (0, "₹0.00"),
            (5, "₹0.05"),
            (200, "₹2.00"),
            (123_456, "₹1,234.56"),
            (-250, "-₹2.50"),
        ],
    )
    def test_renders_rupees_for_display(self, paise, expected):
        assert format_paise(paise) == expected
