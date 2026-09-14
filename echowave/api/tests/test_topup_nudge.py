"""The nudge names a week, rounded to a top-up step, and only when low."""

from api.services.billing import topup_nudge


def test_a_week_of_burn_less_the_balance_rounded_up():
    # 300/day, 500 left, floor 2000 paise: low. Need 2100-500=1600 → the
    # ₹1,000 minimum (KAN-47, 14 Sept).
    assert (
        topup_nudge.suggest(
            balance_paise=500, daily_burn_paise=300, min_balance_paise=2000
        )
        == 100000
    )
    # 20,000/day, 4,000 left → 136,000 → rounds to a 10,000 step.
    assert (
        topup_nudge.suggest(
            balance_paise=4000, daily_burn_paise=20000, min_balance_paise=2000
        )
        == 140000
    )


def test_no_burn_or_not_low_means_no_nudge():
    assert (
        topup_nudge.suggest(
            balance_paise=500, daily_burn_paise=0, min_balance_paise=2000
        )
        is None
    )
    assert (
        topup_nudge.suggest(
            balance_paise=50000, daily_burn_paise=300, min_balance_paise=2000
        )
        is None
    )
