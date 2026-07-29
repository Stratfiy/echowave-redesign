"""Integer money arithmetic: rounding, units and the paise/millipaise split."""

import pytest

from api.enums import RateUnit
from api.services.billing.money import (
    DEFAULT_PLATFORM_RATE_MPAISE,
    billable_minutes,
    cost_paise,
    format_paise,
    platform_fee_paise,
    round_half_up_div,
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
