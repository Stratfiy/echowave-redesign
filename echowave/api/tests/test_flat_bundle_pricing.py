"""One number a minute: the bundle's list price is the whole charge."""

from api.enums import CostComponent, RateUnit
from api.services.billing.cost_engine import RateSpec, UsageItem, compute_call_cost
from api.services.billing.money import MPAISE_PER_PAISE
from api.services.configuration import bundles


class _Row:
    def __init__(self, price, tiers=None):
        self.list_paise_per_minute = price
        self.volume_tiers = tiers or []


def test_the_tier_earned_this_month_wins_and_the_list_price_is_the_floor():
    row = _Row(
        556,
        [
            {"min_minutes": 5000, "paise_per_minute": 500},
            {"min_minutes": 20000, "paise_per_minute": 450},
        ],
    )
    assert bundles.flat_rate_paise(row, period_minutes=0) == 556
    assert bundles.flat_rate_paise(row, period_minutes=5000) == 500
    assert bundles.flat_rate_paise(row, period_minutes=30000) == 450
    assert bundles.flat_rate_paise(_Row(None), period_minutes=99999) is None


def test_a_flat_rate_is_the_only_charge_and_vendor_cost_is_still_measured():
    usage = [
        UsageItem(
            component=CostComponent.LLM,
            provider="openai",
            model="gpt-4.1",
            quantity=1000,
        )
    ]
    rates = {
        ("llm", "openai", "gpt-4.1"): RateSpec(
            rate_mpaise=200 * MPAISE_PER_PAISE, unit=RateUnit.THOUSAND_TOKENS
        )
    }
    cost = compute_call_cost(
        billable_seconds=120,
        platform_rate_mpaise=300_000,
        usage=usage,
        provider_rates=rates,
        markup_bps=17_000,
        flat_rate_mpaise=556 * MPAISE_PER_PAISE,
    )
    # 2 minutes at Rs 5.56.
    assert cost.total_charged_paise == 1112
    assert cost.platform_fee_paise == 1112
    assert cost.addon_fee_paise == 0
    assert cost.total_provider_cost_paise > 0
    charged_lines = [line for line in cost.line_items if line.cost_paise > 0]
    assert len(charged_lines) == 1 and charged_lines[0].provider == "bundle"


def test_a_waived_flat_fee_charges_nothing():
    cost = compute_call_cost(
        billable_seconds=60,
        platform_rate_mpaise=300_000,
        flat_rate_mpaise=556_000,
        platform_fee_waived=True,
    )
    assert cost.total_charged_paise == 0
