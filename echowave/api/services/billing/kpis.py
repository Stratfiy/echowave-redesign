"""Unit economics: what a minute costs, earns and gives away.

Assembles the KPI screen. The database client returns totals; every ratio is
derived here, once, so a per-minute figure is never computed two different ways
in two different places.

Division is by **connected seconds**, not by billed seconds or by whole
minutes. A per-minute cost is a statement about how much service was delivered,
and connected time is the only one of the three that measures that — billed
seconds carry our rounding convention, and whole minutes carry it twice.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from api.db import billing_kpi_client as kpi_client
from api.services.billing.money import (
    DEFAULT_PULSE_SECONDS,
    mpaise_to_micros_usd,
    round_half_up_div,
)

SECONDS_PER_MINUTE = 60

#: An FX rate older than this is stale enough to be worth acting on. Six weeks
#: is long enough not to nag over ordinary drift and short enough that a 2-3%
#: move has not silently eaten the margin on a quarter's traffic.
FX_STALE_AFTER_DAYS = 42


@dataclass(frozen=True)
class PerMinute:
    """A total expressed against the connected minutes that produced it."""

    total_paise: int
    paise_per_minute: int


def _per_minute(total_paise: int, billable_seconds: int) -> PerMinute:
    """Rupees per connected minute. Zero seconds yields zero, not a crash."""
    if billable_seconds <= 0:
        return PerMinute(total_paise=total_paise, paise_per_minute=0)
    return PerMinute(
        total_paise=total_paise,
        paise_per_minute=round_half_up_div(
            total_paise * SECONDS_PER_MINUTE, billable_seconds
        ),
    )


async def unit_economics_report(
    session: AsyncSession, *, start: date, end: date
) -> dict:
    """Everything the unit-economics screen needs, in one round of queries."""
    totals = await kpi_client.unit_economics(session, start=start, end=end)
    seconds = totals["billable_seconds"]

    revenue = _per_minute(totals["revenue_paise"], seconds)
    provider_cost = _per_minute(totals["provider_cost_paise"], seconds)
    margin_paise = totals["revenue_paise"] - totals["provider_cost_paise"]
    margin = _per_minute(margin_paise, seconds)

    fx = await kpi_client.fx_status(session)
    fx_block = None
    if fx is not None:
        recorded = fx["recorded_at"]
        if recorded.tzinfo is None:
            recorded = recorded.replace(tzinfo=UTC)
        age_days = (datetime.now(UTC) - recorded).days
        fx_block = {
            "paise_per_usd": fx["paise_per_usd"],
            "effective_from": fx["effective_from"].isoformat(),
            "recorded_at": recorded.isoformat(),
            "source": fx["source"],
            "age_days": age_days,
            # Flagged rather than silently tolerated: the list price is fixed
            # in dollars, so a stale rate is a margin leak with no symptom.
            "is_stale": age_days > FX_STALE_AFTER_DAYS,
        }

    components = await kpi_client.cost_by_component(session, start=start, end=end)
    models = await kpi_client.cost_by_model(session, start=start, end=end)
    pulse = await kpi_client.pulse_effect(session, start=start, end=end)
    uncosted = await kpi_client.uncosted_usage(session, start=start, end=end)
    accounts = await kpi_client.margin_by_account(session, start=start, end=end)

    fx_paise = fx["paise_per_usd"] if fx else None

    return {
        "calls": totals["calls"],
        "billable_seconds": seconds,
        "billed_seconds": totals["billed_seconds"],
        "billable_minutes": round_half_up_div(seconds, SECONDS_PER_MINUTE)
        if seconds
        else 0,
        "revenue_paise": revenue.total_paise,
        "revenue_paise_per_minute": revenue.paise_per_minute,
        "provider_cost_paise": provider_cost.total_paise,
        "provider_cost_paise_per_minute": provider_cost.paise_per_minute,
        "margin_paise": margin.total_paise,
        "margin_paise_per_minute": margin.paise_per_minute,
        # None rather than zero when there is no revenue: a margin percentage
        # of an empty period is undefined, and 0% reads as "we lost it all".
        "margin_pct": (
            margin_paise / totals["revenue_paise"]
            if totals["revenue_paise"] > 0
            else None
        ),
        # The same figures in the currency the price is quoted in, so the
        # screen can be read against a competitor's rate card directly.
        "revenue_micros_usd_per_minute": (
            mpaise_to_micros_usd(
                mpaise=revenue.paise_per_minute * 1000, usd_inr_paise=fx_paise
            )
            if fx_paise
            else None
        ),
        "margin_micros_usd_per_minute": (
            mpaise_to_micros_usd(
                mpaise=margin.paise_per_minute * 1000, usd_inr_paise=fx_paise
            )
            if fx_paise and margin.paise_per_minute >= 0
            else None
        ),
        "fx": fx_block,
        "components": [
            {
                **row,
                "paise_per_minute": _per_minute(
                    row["cost_paise"], seconds
                ).paise_per_minute,
                "share": (
                    row["cost_paise"] / totals["provider_cost_paise"]
                    if row["component"] != "platform"
                    and totals["provider_cost_paise"] > 0
                    else None
                ),
            }
            for row in components
        ],
        "models": [
            {
                **row,
                # Against this model's own minutes, not the period's — the
                # question is how expensive a minute on *this* model is.
                "paise_per_minute": _per_minute(
                    row["cost_paise"], row["billable_seconds"]
                ).paise_per_minute,
            }
            for row in models
        ],
        "pulse": _pulse_block(pulse, totals),
        "uncosted": uncosted,
        "accounts": [
            {
                **row,
                "margin_paise": row["revenue_paise"] - row["provider_cost_paise"],
                "margin_paise_per_minute": _per_minute(
                    row["revenue_paise"] - row["provider_cost_paise"],
                    row["billable_seconds"],
                ).paise_per_minute,
            }
            for row in accounts
        ],
    }


def _pulse_block(pulse: dict, totals: dict) -> dict:
    """What the 15-second pulse is worth to customers, and what it costs us.

    Both directions of the same number, because the screen exists to let
    someone decide whether the differentiator is still affordable.
    """
    billed = totals["billed_seconds"]
    competitor = pulse["competitor_seconds"]
    calls = pulse["calls"]

    return {
        "pulse_seconds": DEFAULT_PULSE_SECONDS,
        "competitor_pulse_seconds": kpi_client.COMPETITOR_PULSE_SECONDS,
        "calls": calls,
        # Calls whose duration already landed on a whole minute, where the
        # pulse changed nothing. Without this the headline reads as if every
        # customer saw a discount.
        "unaffected_calls": pulse["unaffected_calls"],
        "billed_seconds": billed,
        "competitor_seconds": competitor,
        "seconds_saved_for_customers": max(competitor - billed, 0),
        # The revenue we chose not to collect. This is the price of the
        # differentiator; it is a real cost and belongs on the screen.
        "foregone_paise": pulse["foregone_paise"],
        "foregone_pct_of_revenue": (
            pulse["foregone_paise"]
            / (totals["revenue_paise"] + pulse["foregone_paise"])
            if (totals["revenue_paise"] + pulse["foregone_paise"]) > 0
            else None
        ),
        "seconds_saved_per_call": (
            round((competitor - billed) / calls, 1) if calls else 0.0
        ),
    }
