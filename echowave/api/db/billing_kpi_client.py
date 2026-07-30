"""Unit-economics queries — what a minute actually costs and earns.

The dashboard's other screens answer "how much did we bill". These answer the
question behind the pricing decision: at $0.02 a minute with a 15-second pulse,
does a minute make money, and where does the cost go when it does not.

Everything here scans ``workflow_runs`` and ``call_cost_items`` directly rather
than the daily rollup, because the rollup stores totals and these are ratios
and breakdowns the rollup does not carry. Each query is bounded by a UTC
timestamp range derived from IST days, and each is aggregate-only — no per-call
rows are returned — so the scan cost is bounded by the range, not by traffic.

Cross-account by design; only reachable from staff-gated routes.
"""

from __future__ import annotations

import json
from datetime import date, datetime

from sqlalchemy import Integer, Text, case, cast, desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.db.models import (
    CallCostItemModel,
    OrganizationModel,
    UsdInrRateHistoryModel,
    WorkflowModel,
    WorkflowRunModel,
)
from api.services.billing.money import MPAISE_PER_PAISE, round_half_up_div
from api.services.billing.rollup import ist_day_bounds_utc

# The billing convention every competitor uses, and the baseline the pulse is
# measured against. Not a setting — it is what "what would they have charged"
# means.
COMPETITOR_PULSE_SECONDS = 60

SECONDS_PER_MINUTE = 60


def _range_utc(start: date, end: date) -> tuple[datetime, datetime]:
    """An inclusive IST day range as the UTC half-open window it really is."""
    first, _ = ist_day_bounds_utc(start)
    _, last = ist_day_bounds_utc(end)
    return first, last


def _costed_runs(start: date, end: date):
    """Costed calls in the range, with their organization. The base of every
    query here — an uncosted call has no economics to report."""
    lo, hi = _range_utc(start, end)
    return [
        WorkflowRunModel.costed_at.isnot(None),
        WorkflowRunModel.created_at >= lo,
        WorkflowRunModel.created_at < hi,
        func.coalesce(WorkflowRunModel.billable_seconds, 0) > 0,
    ]


async def unit_economics(session: AsyncSession, *, start: date, end: date) -> dict:
    """Revenue, cost and margin for the range, plus the seconds behind them.

    Returns totals only. Per-minute figures are derived by the caller from
    ``billable_seconds`` so the division happens once, in one place, rather
    than being rounded independently into each field here.
    """
    row = (
        await session.execute(
            select(
                func.count().label("calls"),
                func.coalesce(func.sum(WorkflowRunModel.billable_seconds), 0).label(
                    "billable_seconds"
                ),
                func.coalesce(func.sum(WorkflowRunModel.billed_seconds), 0).label(
                    "billed_seconds"
                ),
                func.coalesce(func.sum(WorkflowRunModel.total_charged_paise), 0).label(
                    "revenue_paise"
                ),
                func.coalesce(
                    func.sum(WorkflowRunModel.total_provider_cost_paise), 0
                ).label("provider_cost_paise"),
            ).where(*_costed_runs(start, end))
        )
    ).one()

    return {
        "calls": int(row.calls or 0),
        "billable_seconds": int(row.billable_seconds or 0),
        "billed_seconds": int(row.billed_seconds or 0),
        "revenue_paise": int(row.revenue_paise or 0),
        "provider_cost_paise": int(row.provider_cost_paise or 0),
    }


async def cost_by_component(
    session: AsyncSession, *, start: date, end: date
) -> list[dict]:
    """Where the money goes, split STT / LLM / TTS / telephony / platform.

    The platform row is our fee rather than a cost, and is returned alongside
    the others deliberately: the useful comparison is what we charge against
    what the call consumed, and separating them into two screens hides it.
    """
    rows = (
        await session.execute(
            select(
                CallCostItemModel.component,
                func.coalesce(func.sum(CallCostItemModel.cost_paise), 0).label(
                    "cost_paise"
                ),
                func.count(func.distinct(CallCostItemModel.workflow_run_id)).label(
                    "calls"
                ),
            )
            .join(
                WorkflowRunModel,
                CallCostItemModel.workflow_run_id == WorkflowRunModel.id,
            )
            .where(*_costed_runs(start, end))
            .group_by(CallCostItemModel.component)
            .order_by(desc("cost_paise"))
        )
    ).all()

    return [
        {
            "component": r.component,
            "cost_paise": int(r.cost_paise or 0),
            "calls": int(r.calls or 0),
        }
        for r in rows
    ]


async def cost_by_model(
    session: AsyncSession, *, start: date, end: date, limit: int = 20
) -> list[dict]:
    """Provider cost per connected minute, by the model that incurred it.

    The league table the pricing decision actually turns on: two models from
    one vendor differ by more than an order of magnitude, so "our LLM cost" is
    not an actionable number and "gpt-4o is costing 3x mini per minute" is.

    Minutes are attributed from the calls a model appeared on, so a call using
    two models contributes its duration to both. That overstates any single
    model's minutes on mixed calls, which is why the per-minute figure here is
    a cost intensity for comparison and not a share of the invoice.
    """
    seconds = func.coalesce(func.sum(WorkflowRunModel.billable_seconds), 0)
    cost = func.coalesce(func.sum(CallCostItemModel.cost_paise), 0)

    rows = (
        await session.execute(
            select(
                CallCostItemModel.component,
                CallCostItemModel.provider,
                CallCostItemModel.model,
                cost.label("cost_paise"),
                seconds.label("billable_seconds"),
                func.count(func.distinct(CallCostItemModel.workflow_run_id)).label(
                    "calls"
                ),
            )
            .join(
                WorkflowRunModel,
                CallCostItemModel.workflow_run_id == WorkflowRunModel.id,
            )
            .where(
                *_costed_runs(start, end),
                CallCostItemModel.component != "platform",
            )
            .group_by(
                CallCostItemModel.component,
                CallCostItemModel.provider,
                CallCostItemModel.model,
            )
            .order_by(desc("cost_paise"))
            .limit(limit)
        )
    ).all()

    return [
        {
            "component": r.component,
            "provider": r.provider,
            "model": r.model or None,
            "cost_paise": int(r.cost_paise or 0),
            "billable_seconds": int(r.billable_seconds or 0),
            "calls": int(r.calls or 0),
        }
        for r in rows
    ]


async def pulse_effect(session: AsyncSession, *, start: date, end: date) -> dict:
    """What the 15-second pulse gives away, in rupees.

    Three quantities, per call and summed:

    * ``billable_seconds`` — what the call actually took.
    * ``billed_seconds`` — what we charged for, after rounding up to our pulse.
    * ``competitor_seconds`` — what a whole-minute platform would have charged.

    The gap between the last two, priced at the rate each call was billed at,
    is the revenue the pulse costs us. It is the price of the differentiator,
    and it belongs on a screen rather than in a pitch deck.
    """
    competitor_seconds = (
        func.ceil(
            cast(WorkflowRunModel.billable_seconds, Integer)
            / float(COMPETITOR_PULSE_SECONDS)
        )
        * COMPETITOR_PULSE_SECONDS
    )
    # Priced at the rate that call was actually billed at, so an account on a
    # negotiated rate contributes its own economics rather than the list price.
    #
    # The rate is per minute and the quantity is in seconds, so this accumulates
    # in millipaise-seconds and is divided by 60 * 1000 once at the end. Doing
    # the division per call would round every row and drift the total.
    rate = func.coalesce(WorkflowRunModel.platform_rate_mpaise_applied, 0)
    foregone_mpaise_seconds = (
        competitor_seconds - WorkflowRunModel.billed_seconds
    ) * rate

    row = (
        await session.execute(
            select(
                func.coalesce(func.sum(competitor_seconds), 0).label(
                    "competitor_seconds"
                ),
                func.coalesce(func.sum(foregone_mpaise_seconds), 0).label(
                    "foregone_mpaise_seconds"
                ),
                func.count().label("calls"),
                # Calls where the pulse changed nothing, because the duration
                # already landed on a whole minute. Reported so the headline is
                # not read as applying to every call.
                func.coalesce(
                    func.sum(
                        case(
                            (
                                competitor_seconds == WorkflowRunModel.billed_seconds,
                                1,
                            ),
                            else_=0,
                        )
                    ),
                    0,
                ).label("unaffected_calls"),
            ).where(
                *_costed_runs(start, end),
                WorkflowRunModel.billed_seconds.isnot(None),
            )
        )
    ).one()

    # millipaise-seconds → paise, rounded once for the whole period.
    foregone = int(row.foregone_mpaise_seconds or 0)
    return {
        "calls": int(row.calls or 0),
        "unaffected_calls": int(row.unaffected_calls or 0),
        "competitor_seconds": int(row.competitor_seconds or 0),
        "foregone_paise": (
            round_half_up_div(foregone, SECONDS_PER_MINUTE * MPAISE_PER_PAISE)
            if foregone > 0
            else 0
        ),
    }


async def uncosted_usage(session: AsyncSession, *, start: date, end: date) -> dict:
    """How much of the book is knowingly incomplete.

    A call with unpriced usage has real provider cost we did not record, so its
    margin reads better than it is. This does not estimate the missing money —
    we have no rate, that is the whole problem — it reports how many calls are
    affected and which providers are responsible, so the rates can be filled in.
    """
    # Grouped on the whole list rather than unnested: the column is a plain
    # JSON array and the cardinality here is tiny — one distinct list per
    # combination of missing rates, not one per call.
    as_text = cast(WorkflowRunModel.uncosted_usage, Text)
    rows = (
        await session.execute(
            select(as_text.label("usage"), func.count().label("calls"))
            .where(
                *_costed_runs(start, end),
                WorkflowRunModel.uncosted_usage.isnot(None),
                as_text != "[]",
            )
            .group_by(as_text)
            .order_by(desc("calls"))
            .limit(50)
        )
    ).all()

    total_costed = await session.scalar(
        select(func.count()).where(*_costed_runs(start, end))
    )
    # Calls costed before uncosted_usage existed. Counted separately because
    # "we do not know" is not the same as "nothing was missing".
    unknown = await session.scalar(
        select(func.count()).where(
            *_costed_runs(start, end), WorkflowRunModel.uncosted_usage.is_(None)
        )
    )

    by_label: dict[str, int] = {}
    affected = 0
    for row in rows:
        calls = int(row.calls or 0)
        affected += calls
        try:
            labels = json.loads(row.usage or "[]")
        except ValueError:
            continue
        for label in labels or []:
            by_label[label] = by_label.get(label, 0) + calls

    return {
        "costed_calls": int(total_costed or 0),
        "affected_calls": affected,
        "unknown_calls": int(unknown or 0),
        "by_usage": [
            {"usage": label, "calls": calls}
            for label, calls in sorted(
                by_label.items(), key=lambda kv: kv[1], reverse=True
            )
        ],
    }


async def fx_status(session: AsyncSession) -> dict | None:
    """The exchange rate currently in force, and how old it is.

    Age matters: the list price is fixed in dollars, so a rate that has not
    been updated in months means we are billing rupees at a stale conversion
    and losing margin invisibly as the rupee weakens.
    """
    row = await session.scalar(
        select(UsdInrRateHistoryModel)
        .where(UsdInrRateHistoryModel.effective_to.is_(None))
        .order_by(UsdInrRateHistoryModel.effective_from.desc())
        .limit(1)
    )
    if row is None:
        return None
    return {
        "paise_per_usd": row.paise_per_usd,
        "effective_from": row.effective_from,
        # Age is measured from when the rate was *recorded*, not from when it
        # became effective. The opening row is deliberately backdated to the
        # epoch so it covers calls made before it was written, and reporting
        # that as its age would say the rate is fifty years stale.
        "recorded_at": row.created_at or row.effective_from,
        "source": row.source,
    }


async def margin_by_account(
    session: AsyncSession, *, start: date, end: date, limit: int = 15
) -> list[dict]:
    """Per-minute economics by account, worst margin first.

    Sorted by margin rather than revenue on purpose: the account quietly losing
    money on every minute is the one worth finding, and it is rarely the
    biggest.
    """
    seconds = func.coalesce(func.sum(WorkflowRunModel.billable_seconds), 0)
    revenue = func.coalesce(func.sum(WorkflowRunModel.total_charged_paise), 0)
    cost = func.coalesce(func.sum(WorkflowRunModel.total_provider_cost_paise), 0)

    rows = (
        await session.execute(
            select(
                OrganizationModel.id.label("organization_id"),
                OrganizationModel.billing_name,
                OrganizationModel.provider_id,
                seconds.label("billable_seconds"),
                revenue.label("revenue_paise"),
                cost.label("provider_cost_paise"),
                func.count().label("calls"),
            )
            .join(WorkflowModel, WorkflowRunModel.workflow_id == WorkflowModel.id)
            .join(
                OrganizationModel,
                OrganizationModel.id == WorkflowModel.organization_id,
            )
            .where(*_costed_runs(start, end))
            .group_by(OrganizationModel.id)
            .having(seconds > 0)
            .order_by((revenue - cost).asc())
            .limit(limit)
        )
    ).all()

    return [
        {
            "organization_id": r.organization_id,
            "name": r.billing_name or r.provider_id,
            "calls": int(r.calls or 0),
            "billable_seconds": int(r.billable_seconds or 0),
            "revenue_paise": int(r.revenue_paise or 0),
            "provider_cost_paise": int(r.provider_cost_paise or 0),
        }
        for r in rows
    ]
