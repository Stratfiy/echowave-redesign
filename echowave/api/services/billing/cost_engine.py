"""Per-call cost computation.

    total_charged = platform_rate + Σ(provider costs at cost)

Provider costs are passed through with **no markup**. That property is not a
convention here, it is structural: provider cost and the platform fee are
computed as separate line items, stored in separate columns, and never summed
into a single number anywhere in the schema. There is no code path that can
inflate a provider rate, because provider line items only ever multiply
measured usage by a rate read from ``provider_rates``.

Not every call has provider costs. An account that brings its own model keys
pays those providers directly, so Decibyl incurs no inference cost and the
receipt is a platform fee alone. An account on Decibyl-managed model services
does incur them, and they appear as itemised pass-through lines.

The pure computation lives in :func:`compute_call_cost` so the rounding
invariant can be tested without a database.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from api.enums import CostComponent, RateUnit
from api.services.billing.money import (
    billable_minutes as to_billable_minutes,
)
from api.services.billing.money import (
    cost_paise,
    platform_fee_paise,
)


@dataclass(frozen=True)
class UsageItem:
    """One measured provider usage on a call.

    ``quantity`` is the raw measurement in the unit the provider's rate is
    quoted against: seconds for per-minute rates, characters for per-1k-char
    rates, tokens for per-1k-token rates.
    """

    component: CostComponent
    provider: str
    quantity: int


@dataclass(frozen=True)
class RateSpec:
    """A provider rate as applied to a call."""

    rate_mpaise: int
    unit: RateUnit


@dataclass(frozen=True)
class CostLine:
    component: str
    provider: str | None
    units: int
    unit_rate_mpaise: int
    cost_paise: int


@dataclass(frozen=True)
class CallCost:
    line_items: tuple[CostLine, ...]
    billable_minutes: int
    platform_rate_mpaise: int
    platform_fee_paise: int
    total_provider_cost_paise: int
    total_charged_paise: int
    # Usage we could not price because no rate was on file. Surfaced rather
    # than silently costed at zero, which would understate provider cost and
    # overstate margin.
    uncosted: tuple[UsageItem, ...] = field(default_factory=tuple)


def compute_call_cost(
    *,
    billable_seconds: int,
    platform_rate_mpaise: int,
    usage: tuple[UsageItem, ...] | list[UsageItem] = (),
    provider_rates: Mapping[tuple[str, str], RateSpec] | None = None,
) -> CallCost:
    """Cost one call. Pure — no I/O, no clock, no database.

    ``provider_rates`` is keyed by ``(component, provider)``. A missing key
    means no rate is on file; that usage is reported in ``uncosted`` instead of
    being priced at zero.

    The returned ``total_charged_paise`` is exactly ``sum(line.cost_paise for
    line in line_items)``. It is never computed as a separately-rounded figure,
    so an invoice always reconciles against its own line items.
    """
    provider_rates = provider_rates or {}
    minutes = to_billable_minutes(billable_seconds)

    lines: list[CostLine] = []
    uncosted: list[UsageItem] = []

    # Provider pass-through lines first, so a receipt reads cost-then-fee.
    for item in usage:
        component_value = (
            item.component.value
            if isinstance(item.component, CostComponent)
            else str(item.component)
        )
        spec = provider_rates.get((component_value, item.provider))
        if spec is None:
            uncosted.append(item)
            continue
        lines.append(
            CostLine(
                component=component_value,
                provider=item.provider,
                units=item.quantity,
                unit_rate_mpaise=spec.rate_mpaise,
                cost_paise=cost_paise(
                    quantity=item.quantity,
                    rate_mpaise=spec.rate_mpaise,
                    unit=spec.unit,
                ),
            )
        )

    provider_total = sum(line.cost_paise for line in lines)

    fee = platform_fee_paise(billable_minutes=minutes, rate_mpaise=platform_rate_mpaise)
    lines.append(
        CostLine(
            component=CostComponent.PLATFORM.value,
            provider=None,
            units=minutes,
            unit_rate_mpaise=platform_rate_mpaise,
            cost_paise=fee,
        )
    )

    # Defined as the sum of the rounded line items — see money.py.
    total = sum(line.cost_paise for line in lines)

    return CallCost(
        line_items=tuple(lines),
        billable_minutes=minutes,
        platform_rate_mpaise=platform_rate_mpaise,
        platform_fee_paise=fee,
        total_provider_cost_paise=provider_total,
        total_charged_paise=total,
        uncosted=tuple(uncosted),
    )
