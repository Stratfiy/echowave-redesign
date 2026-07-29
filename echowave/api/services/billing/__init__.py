"""Decibyl billing: rate configuration, per-call costing, credits.

    total_charged = platform_rate + Σ(provider costs at cost)

Money is integer paise; unit rates are integer millipaise. See
``money.py`` for the arithmetic rules and DASHBOARD.md for the pricing
resolution order and every metric definition.
"""

from api.services.billing.cost_engine import (
    CallCost,
    CostLine,
    RateSpec,
    UsageItem,
    compute_call_cost,
)
from api.services.billing.money import (
    DEFAULT_PLATFORM_RATE_MPAISE,
    billable_minutes,
    cost_paise,
    format_paise,
    platform_fee_paise,
    round_half_up_div,
)
from api.services.billing.rates import (
    ResolvedPlatformRate,
    ResolvedProviderRate,
    resolve_platform_rate,
    resolve_provider_rate,
)

__all__ = [
    "DEFAULT_PLATFORM_RATE_MPAISE",
    "CallCost",
    "CostLine",
    "RateSpec",
    "ResolvedPlatformRate",
    "ResolvedProviderRate",
    "UsageItem",
    "billable_minutes",
    "compute_call_cost",
    "cost_paise",
    "format_paise",
    "platform_fee_paise",
    "resolve_platform_rate",
    "resolve_provider_rate",
    "round_half_up_div",
]
