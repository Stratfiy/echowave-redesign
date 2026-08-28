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

from sqlalchemy import Float, Integer, Text, case, cast, desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.db.models import (
    CallCostItemModel,
    EmbeddingIngestionCostModel,
    OrganizationModel,
    ProviderRateModel,
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


async def embedding_ingestion_totals(
    session: AsyncSession, *, start: date, end: date
) -> dict:
    """Cost and charge for knowledge-base ingestion embeddings in the range.

    Deliberately **not** folded into :func:`unit_economics`'s totals: that
    query's revenue/cost are per connected minute, and ingestion has no call
    behind it to attribute minutes to — mixing the two would inflate a
    per-minute figure with a document-level charge that has nothing to do
    with a minute of talk time. This is its own small report instead, the
    document-level counterpart to the call-level one above.

    Every ``embedding_ingestion_costs`` row already exists specifically so
    this margin is answerable — see that model's docstring.
    """
    lo, hi = _range_utc(start, end)
    row = (
        await session.execute(
            select(
                func.count().label("documents"),
                func.coalesce(func.sum(EmbeddingIngestionCostModel.tokens), 0).label(
                    "tokens"
                ),
                func.coalesce(
                    func.sum(EmbeddingIngestionCostModel.vendor_cost_paise), 0
                ).label("vendor_cost_paise"),
                func.coalesce(
                    func.sum(EmbeddingIngestionCostModel.charged_paise), 0
                ).label("charged_paise"),
            ).where(
                EmbeddingIngestionCostModel.created_at >= lo,
                EmbeddingIngestionCostModel.created_at < hi,
            )
        )
    ).one()

    return {
        "documents": int(row.documents or 0),
        "tokens": int(row.tokens or 0),
        "vendor_cost_paise": int(row.vendor_cost_paise or 0),
        "charged_paise": int(row.charged_paise or 0),
    }


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


# ---------------------------------------------------------------------------
# Pricing inputs
#
# The queries above answer "what did a minute earn". These answer the question
# that comes before it: what are the physical facts a price has to be set
# against. They exist because three internal documents assumed 850, 900 and
# 2,300 TTS characters per minute — a 2.7x spread nobody had ever measured —
# and every margin figure in the pricing study rests on which one is true.
#
# Percentiles rather than averages throughout. One call where the agent read a
# policy document aloud moves a mean by a third and quietly re-prices the
# product; a median ignores it, and the p90 is what capacity and worst-case
# margin are actually set by.
# ---------------------------------------------------------------------------


def _per_minute_expr(units_column):
    """Units divided by the call's connected minutes, as a float expression."""
    return cast(units_column, Float) / (
        cast(WorkflowRunModel.billable_seconds, Float) / SECONDS_PER_MINUTE
    )


#: Calls shorter than this make a per-minute rate meaningless — a four-second
#: wrong number with one word of greeting reads as 900 characters a minute.
MIN_SECONDS_FOR_RATE = 30

#: Below this many calls a percentile is an anecdote, so the row is dropped
#: rather than shown with false precision.
MIN_CALLS_FOR_PERCENTILE = 5


async def tts_chars_per_minute(
    session: AsyncSession, *, start: date, end: date
) -> list[dict]:
    """Characters synthesised per connected minute, by provider and model.

    The single number the rate card is most sensitive to: TTS is quoted per
    1,000 characters and is the largest line on an Indic call, so an error here
    scales straight into every margin figure the dashboard shows.
    """
    rate = _per_minute_expr(CallCostItemModel.units)
    rows = (
        await session.execute(
            select(
                CallCostItemModel.provider,
                CallCostItemModel.model,
                func.count().label("calls"),
                func.avg(WorkflowRunModel.billable_seconds).label("avg_seconds"),
                func.percentile_cont(0.5).within_group(rate).label("median"),
                func.avg(rate).label("mean"),
                func.percentile_cont(0.9).within_group(rate).label("p90"),
            )
            .join(
                WorkflowRunModel,
                WorkflowRunModel.id == CallCostItemModel.workflow_run_id,
            )
            .where(
                *_costed_runs(start, end),
                CallCostItemModel.component == "tts",
                CallCostItemModel.units > 0,
                WorkflowRunModel.billable_seconds >= MIN_SECONDS_FOR_RATE,
            )
            .group_by(CallCostItemModel.provider, CallCostItemModel.model)
            .having(func.count() >= MIN_CALLS_FOR_PERCENTILE)
            .order_by(desc("calls"))
        )
    ).all()

    return [
        {
            "provider": row.provider or "",
            "model": row.model or "",
            "calls": int(row.calls or 0),
            "avg_call_seconds": round(float(row.avg_seconds or 0)),
            "median_chars_per_minute": round(float(row.median or 0)),
            "mean_chars_per_minute": round(float(row.mean or 0)),
            "p90_chars_per_minute": round(float(row.p90 or 0)),
        }
        for row in rows
    ]


async def tts_chars_per_minute_by_language(
    session: AsyncSession, *, start: date, end: date
) -> list[dict]:
    """The same figure per language.

    Devanagari and Telugu encode a syllable in fewer characters than the Latin
    transliteration of the same sentence, so a per-1k-character rate is not
    language-neutral even when the vendor's price is. If these diverge, the
    rate card should too.
    """
    rate = _per_minute_expr(CallCostItemModel.units)
    rows = (
        await session.execute(
            select(
                WorkflowRunModel.language,
                func.count().label("calls"),
                func.percentile_cont(0.5).within_group(rate).label("median"),
                func.percentile_cont(0.9).within_group(rate).label("p90"),
            )
            .join(
                WorkflowRunModel,
                WorkflowRunModel.id == CallCostItemModel.workflow_run_id,
            )
            .where(
                *_costed_runs(start, end),
                CallCostItemModel.component == "tts",
                CallCostItemModel.units > 0,
                WorkflowRunModel.billable_seconds >= MIN_SECONDS_FOR_RATE,
            )
            .group_by(WorkflowRunModel.language)
            .having(func.count() >= MIN_CALLS_FOR_PERCENTILE)
            .order_by(desc("calls"))
        )
    ).all()

    return [
        {
            "language": row.language or "",
            "calls": int(row.calls or 0),
            "median_chars_per_minute": round(float(row.median or 0)),
            "p90_chars_per_minute": round(float(row.p90 or 0)),
        }
        for row in rows
    ]


async def rate_card_gaps(
    session: AsyncSession, *, start: date, end: date
) -> list[dict]:
    """Provider/model/component seen on a call with no live rate row.

    A missing rate row does not raise. It costs zero, marks the run uncosted,
    and inflates reported margin by exactly the amount it failed to charge —
    which is how a model can be resold below cost for a year without anything
    going red. Resolution is "exact model, else provider-wide", so a
    combination counts as missing only when neither row exists.
    """
    live_rate = (
        select(ProviderRateModel.id)
        .where(
            ProviderRateModel.provider == CallCostItemModel.provider,
            ProviderRateModel.component == CallCostItemModel.component,
            ProviderRateModel.model.in_(
                [func.lower(func.coalesce(CallCostItemModel.model, "")), ""]
            ),
            ProviderRateModel.effective_to.is_(None),
        )
        .exists()
    )

    rows = (
        await session.execute(
            select(
                CallCostItemModel.provider,
                CallCostItemModel.model,
                CallCostItemModel.component,
                func.count().label("cost_items"),
                func.coalesce(func.sum(CallCostItemModel.units), 0).label("units"),
                func.coalesce(func.sum(CallCostItemModel.cost_paise), 0).label(
                    "charged_paise"
                ),
            )
            .join(
                WorkflowRunModel,
                WorkflowRunModel.id == CallCostItemModel.workflow_run_id,
            )
            .where(*_costed_runs(start, end), ~live_rate)
            .group_by(
                CallCostItemModel.provider,
                CallCostItemModel.model,
                CallCostItemModel.component,
            )
            .order_by(desc("cost_items"))
        )
    ).all()

    return [
        {
            "provider": row.provider or "",
            "model": row.model or "",
            "component": row.component,
            "cost_items": int(row.cost_items or 0),
            "units": int(row.units or 0),
            "charged_paise": int(row.charged_paise or 0),
        }
        for row in rows
    ]


async def monthly_minutes_by_account(
    session: AsyncSession, *, start: date, end: date
) -> list[dict]:
    """Connected minutes per organisation per month.

    What a bundle's included balance has to be sized against. A grant a typical
    account uses a fifth of reads as a waste and churns to pay-as-you-go; one
    they exhaust in a fortnight produces an overage conversation nobody enjoys.
    Neither is guessable — this is the distribution that settles it.
    """
    # A run has no organization of its own — it belongs to a workflow, and the
    # workflow belongs to the organization. Every account-scoped query here
    # takes the same two hops.
    month = func.date_trunc(
        "month", func.timezone("Asia/Kolkata", WorkflowRunModel.created_at)
    )
    seconds = func.coalesce(func.sum(WorkflowRunModel.billable_seconds), 0)
    rows = (
        await session.execute(
            select(
                month.label("month"),
                WorkflowModel.organization_id.label("organization_id"),
                func.count().label("calls"),
                seconds.label("billable_seconds"),
                func.coalesce(func.sum(WorkflowRunModel.total_charged_paise), 0).label(
                    "charged_paise"
                ),
            )
            .join(WorkflowModel, WorkflowRunModel.workflow_id == WorkflowModel.id)
            .where(*_costed_runs(start, end))
            .group_by(month, WorkflowModel.organization_id)
            .order_by(desc("month"), desc(seconds))
        )
    ).all()

    return [
        {
            "month": row.month.date().isoformat() if row.month else None,
            "organization_id": int(row.organization_id or 0),
            "calls": int(row.calls or 0),
            "minutes": round(int(row.billable_seconds or 0) / SECONDS_PER_MINUTE),
            "charged_paise": int(row.charged_paise or 0),
        }
        for row in rows
    ]
