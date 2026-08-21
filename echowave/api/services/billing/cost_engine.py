"""Per-call cost computation.

    total_charged = platform fee (uplifted when the customer brought keys)
                  + Σ(add-on fees for features the call used)
                  + Σ(provider costs × the managed markup)

Two kinds of line exist and the distinction is structural rather than
conventional. **Provider lines** multiply measured usage by a rate read from
``provider_rates`` and record what the vendor charged us in
``provider_cost_paise`` alongside what the customer pays. **Revenue lines** —
the platform fee and add-ons — are ours, so they carry a
provider cost of zero and are never marked up; marking up our own fee would be
a margin on a margin. Nothing in the schema sums the two into one number, which
is what keeps every margin figure downstream honest.

Not every call has provider costs. An account that brings its own model keys
pays those vendors directly, so Decibyl incurs no inference cost and there is
no provider line to earn a margin on. That is why the platform rate arriving
here is already uplifted for such a call — the uplift replaces the margin the
missing provider line would have carried, and it is sized per component
because speech synthesis is worth thirty times what the language model is.
See ``billing/usage.py:byok_platform_tier``.

The pure computation lives in :func:`compute_call_cost` so the rounding
invariant can be tested without a database.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from api.enums import CostComponent, RateUnit

#: Components the managed markup applies to — everything we buy from a vendor
#: and resell.
#:
#: Telephony was excluded on the grounds that its rate card row held the *sell*
#: price, so what an operator typed was what a customer paid. The rows never
#: held that. Every seeded figure is a vendor's published price to us — Plivo's
#: Rs0.60 India local, Twilio's Rs1.20 to mobile — so carriage was being resold
#: at cost, earning nothing on the one component we front the money for.
#:
#: One rule now, for every provider row: **the number is what the vendor
#: charges us, and the markup is what we add.** That is the same sentence for
#: speech, language and carriage, which is worth more than the flexibility of
#: having one component mean something different from the rest.
#:
#: Only carriage we actually sell reaches here. A call on the customer's own
#: carrier records no telephony usage at all — see
#: ``services/telephony/status_processor.py`` — so there is no line to mark up.
MARKED_UP_COMPONENTS = frozenset(
    {
        CostComponent.STT.value,
        CostComponent.LLM.value,
        CostComponent.TTS.value,
        CostComponent.TELEPHONY.value,
    }
)
from api.services.billing.money import (
    DEFAULT_PULSE_SECONDS,
    cost_paise,
    round_half_up_div,
)
from api.services.billing.money import (
    billable_minutes as to_billable_minutes,
)
from api.services.billing.money import (
    billed_seconds as to_billed_seconds,
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
    #: What the customer is charged for this line — the vendor's price with the
    #: managed markup applied. This is the figure that sums to the invoice.
    cost_paise: int
    # The model this line was priced against, so a receipt can say which one
    # was actually billed rather than only naming the provider.
    model: str | None = None
    #: What the vendor charges *us* for the same line, before markup. Equal to
    #: ``cost_paise`` on a platform line and whenever the markup is 1.0.
    #:
    #: Stored rather than derived because the markup is a setting that changes:
    #: recomputing an old receipt against today's multiplier would rewrite what
    #: a customer was actually charged last March.
    provider_cost_paise: int = 0


@dataclass(frozen=True)
class CallCost:
    line_items: tuple[CostLine, ...]
    billable_minutes: int
    platform_rate_mpaise: int
    platform_fee_paise: int
    total_provider_cost_paise: int
    total_charged_paise: int
    #: Add-on revenue, reported separately so the unit-economics screen can
    #: show what priced features earn without re-summing line items. Already
    #: inside ``total_charged_paise``.
    addon_fee_paise: int = 0
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
    markup_bps: int = 10_000,
    pulse_seconds: int = DEFAULT_PULSE_SECONDS,
    usage: tuple[UsageItem, ...] | list[UsageItem] = (),
    provider_rates: Mapping[tuple[str, str, str], RateSpec] | None = None,
    addon_rates: Mapping[str, int] | None = None,
) -> CallCost:
    """Cost one call. Pure — no I/O, no clock, no database.

    ``provider_rates`` is keyed by ``(component, provider, model)`` — the model
    included, because rates differ by more than an order of magnitude between
    models from one vendor. A caller keys the same map twice: once under the
    exact model, and once under ``""`` for the provider-wide fallback. The
    lookup below tries the exact model first. A key matching neither means no
    rate is on file; that usage is reported in ``uncosted`` instead of being
    priced at zero, so an unpriced model understates nothing silently.

    The platform fee is charged on time rounded up to a whole ``pulse_seconds``,
    not to a whole minute. At ``pulse_seconds=60`` this reproduces whole-minute
    billing exactly, which is what makes the comparison against competitors a
    matter of one parameter rather than of two different code paths.

    ``addon_rates`` maps a catalogue key to a per-minute rate for the features
    this call actually used. Both are charged on pulse-rounded time, the same
    basis as the platform fee, so a 40-second call is charged 45 seconds of
    every fee and not a full minute of any of them.

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
        vendor_cost = cost_paise(
            quantity=item.quantity,
            rate_mpaise=spec.rate_mpaise,
            unit=spec.unit,
        )
        # The markup covers model services bought on our keys — the thing a
        # customer would otherwise hold their own API key for. Telephony is
        # excluded: its rate card holds the price we sell a minute at, set
        # directly, so marking it up again would mean the number an operator
        # types is not the number a customer pays.
        #
        # Marked up per line rather than on the total, so each line on a
        # receipt adds up to the figure printed beside it. Rounding once per
        # line is the same rule the rest of this module follows.
        line_markup = markup_bps if component_value in MARKED_UP_COMPONENTS else 10_000
        lines.append(
            CostLine(
                component=component_value,
                provider=item.provider,
                model=item.model or None,
                units=item.quantity,
                unit_rate_mpaise=spec.rate_mpaise,
                cost_paise=round_half_up_div(vendor_cost * line_markup, 10_000),
                provider_cost_paise=vendor_cost,
            )
        )

    provider_total = sum(line.provider_cost_paise for line in lines)

    # The rate is quoted per minute and the quantity is in seconds, which is
    # exactly the contract cost_paise already implements for a per-minute rate.
    # So the platform line is structurally identical to a provider one: measured
    # units times a rate, rounded once.
    fee = cost_paise(
        quantity=billed,
        rate_mpaise=platform_rate_mpaise,
        unit=RateUnit.MINUTE,
    )
    # The platform fee is ours, not a vendor's, so it is never marked up and
    # its provider cost is zero. Marking it up would be charging a margin on a
    # margin.
    lines.append(
        CostLine(
            component=CostComponent.PLATFORM.value,
            provider=None,
            units=billed,
            unit_rate_mpaise=platform_rate_mpaise,
            cost_paise=fee,
            provider_cost_paise=0,
        )
    )

    # One line per priced feature the call used. The catalogue key rides in
    # ``provider`` so a receipt can name which feature was charged without the
    # schema growing a column per feature — and so adding one to the catalogue
    # never needs a migration.
    addon_fee = 0
    for key in sorted(addon_rates or {}):
        rate = (addon_rates or {})[key]
        if rate <= 0:
            continue
        line_fee = cost_paise(
            quantity=billed,
            rate_mpaise=rate,
            unit=RateUnit.MINUTE,
        )
        addon_fee += line_fee
        lines.append(
            CostLine(
                component=CostComponent.ADDON.value,
                provider=key,
                units=billed,
                unit_rate_mpaise=rate,
                cost_paise=line_fee,
                provider_cost_paise=0,
            )
        )

    # Defined as the sum of the rounded line items — see money.py.
    total = sum(line.cost_paise for line in lines)

    return CallCost(
        line_items=tuple(lines),
        billable_minutes=minutes,
        platform_rate_mpaise=platform_rate_mpaise,
        platform_fee_paise=fee,
        addon_fee_paise=addon_fee,
        total_provider_cost_paise=provider_total,
        total_charged_paise=total,
        pulse_seconds=pulse_seconds,
        billed_seconds=billed,
        uncosted=tuple(uncosted),
    )
