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
    billed_seconds as to_billed_seconds,
)
from api.services.billing.money import (
    DEFAULT_PULSE_SECONDS,
    cost_paise,
)


@dataclass(frozen=True)
class UsageItem:
    """One measured provider usage on a call.

    ``quantity`` is the raw measurement in the unit the provider's rate is
    quoted against: seconds for per-minute rates, characters for per-1k-char
    rates, tokens for per-1k-token rates.

    ``model`` is the specific model the usage was incurred on — "gpt-4o-mini"
    rather than just "openai". Rates differ by more than an order of magnitude
    between models from the same provider, so pricing without it would be
    wrong for anyone not on the default. Empty when the pipeline did not record
    one, which resolves to the provider-wide rate.
    """

    component: CostComponent
    provider: str
    quantity: int
    model: str = ""


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
    # The model this line was priced against, so a receipt can say which one
    # was actually billed rather than only naming the provider.
    model: str | None = None


@dataclass(frozen=True)
class CallCost:
    line_items: tuple[CostLine, ...]
    billable_minutes: int
    platform_rate_mpaise: int
    platform_fee_paise: int
    total_provider_cost_paise: int
    total_charged_paise: int
    # The pulse this call was billed at, and the time it was billed for after
    # rounding up to a whole pulse. Reported so a receipt can show that a
    # 62-second call was charged for 75 seconds and not 120.
    pulse_seconds: int = DEFAULT_PULSE_SECONDS
    billed_seconds: int = 0
    # Usage we could not price because no rate was on file. Surfaced rather
    # than silently costed at zero, which would understate provider cost and
    # overstate margin.
    uncosted: tuple[UsageItem, ...] = field(default_factory=tuple)


def compute_call_cost(
    *,
    billable_seconds: int,
    platform_rate_mpaise: int,
    pulse_seconds: int = DEFAULT_PULSE_SECONDS,
    usage: tuple[UsageItem, ...] | list[UsageItem] = (),
    provider_rates: Mapping[tuple[str, str], RateSpec] | None = None,
) -> CallCost:
    """Cost one call. Pure — no I/O, no clock, no database.

    ``provider_rates`` is keyed by ``(component, provider)``. A missing key
    means no rate is on file; that usage is reported in ``uncosted`` instead of
    being priced at zero.

    The platform fee is charged on time rounded up to a whole ``pulse_seconds``,
    not to a whole minute. At ``pulse_seconds=60`` this reproduces whole-minute
    billing exactly, which is what makes the comparison against competitors a
    matter of one parameter rather than of two different code paths.

    The returned ``total_charged_paise`` is exactly ``sum(line.cost_paise for
    line in line_items)``. It is never computed as a separately-rounded figure,
    so an invoice always reconciles against its own line items.
    """
    provider_rates = provider_rates or {}
    minutes = to_billable_minutes(billable_seconds)
    billed = to_billed_seconds(billable_seconds, pulse_seconds)

    lines: list[CostLine] = []
    uncosted: list[UsageItem] = []

    # Provider pass-through lines first, so a receipt reads cost-then-fee.
    for item in usage:
        component_value = (
            item.component.value
            if isinstance(item.component, CostComponent)
            else str(item.component)
        )
        # Most specific rate wins: a rate quoted for this exact model, else the
        # provider-wide one. Callers key the map both ways.
        spec = provider_rates.get((component_value, item.provider, item.model))
        if spec is None:
            spec = provider_rates.get((component_value, item.provider, ""))
        if spec is None:
            uncosted.append(item)
            continue
        lines.append(
            CostLine(
                component=component_value,
                provider=item.provider,
                model=item.model or None,
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

    # The rate is quoted per minute and the quantity is in seconds, which is
    # exactly the contract cost_paise already implements for a per-minute rate.
    # So the platform line is structurally identical to a provider one: measured
    # units times a rate, rounded once.
    fee = cost_paise(
        quantity=billed,
        rate_mpaise=platform_rate_mpaise,
        unit=RateUnit.MINUTE,
    )
    lines.append(
        CostLine(
            component=CostComponent.PLATFORM.value,
            provider=None,
            units=billed,
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
        pulse_seconds=pulse_seconds,
        billed_seconds=billed,
        uncosted=tuple(uncosted),
    )
