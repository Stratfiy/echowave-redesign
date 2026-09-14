"""The super-admin KPI board: every number in pricing spec §9, on one screen.

Three windows — today, the last seven days, the last thirty — each with the
equal-length period before it beside it, so a figure is never read without
knowing which way it is moving. The route hands this module a date; this
module hands back the board.

**Every KPI in the spec is on the board, including the ones we cannot
compute yet.** A KPI whose inputs are not recorded (impersonation is not
audited until KAN-82; ads spend lives in a spreadsheet; no provider cost log
exists for text events) appears with ``available: false`` and a reason that
names what is missing. That is the *Silent Absence* rule applied to a
dashboard: a number that is quietly not there is the one nobody asks about,
and a founder reading the board deserves to know which columns are blank
because the business is quiet and which are blank because we never looked.

**Two shapes of KPI.** A *flow* KPI counts what happened inside a window
(signups, minutes, credits spent) and its "previous" is the window before.
A *point* KPI describes the state of the world at an instant (MRR, deferred
revenue, keys in use) and its "previous" is the state at the start of the
window, so "MRR, and where it was thirty days ago" reads the same way the
flow columns do.

Ratios are computed here, once, from counts the client returns, so no ratio
can be worked out two different ways on two screens. ``None`` where the
denominator is zero: 0% of nothing is not a result.
"""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from api.db import kpi_board_client as q
from api.enums import AgentEventKind
from api.services.billing import events, knowledge_pages, markup, plan_limits
from api.services.billing.rollup import IST

#: The spec's three windows, as inclusive day counts ending on ``as_of``.
WINDOWS: dict[str, int] = {"day": 1, "week": 7, "month": 30}

#: The Sarvam startup credit the burn KPI counts against (KAN-47).
SARVAM_CREDIT_PAISE = 25_000 * 100

#: The financial model's provider-cost assumption for an Indic minute.
INDIC_COST_ASSUMPTION_PAISE_PER_MINUTE = 278

#: Credits are ₹0.50 (KAN-52); revenue in credits × this is revenue in paise.
PAISE_PER_CREDIT = 50

SECONDS_PER_MINUTE = 60

Flow = Callable[[AsyncSession, date, date], Awaitable[Any]]
Point = Callable[[AsyncSession, datetime], Awaitable[Any]]


@dataclass(frozen=True)
class Kpi:
    """One row of the board."""

    key: str
    label: str
    definition: str
    #: ``count``, ``paise``, ``credits``, ``ratio``, ``days``, ``hours``,
    #: ``ms``, ``minutes`` or ``bps`` — what the value is measured in, so the
    #: screen formats it without guessing from the key.
    unit: str
    #: ``flow`` or ``point`` (see the module docstring).
    shape: str = "flow"
    flow: Flow | None = None
    point: Point | None = None
    #: Why it cannot be computed yet, when it cannot. Names the missing input.
    unavailable_reason: str | None = None
    #: Which way is good, so the screen colours the change: ``up``, ``down``
    #: or ``none`` for figures that are neither (a count of refunds is a
    #: quality signal, not a target).
    good: str = "up"

    @property
    def available(self) -> bool:
        return self.unavailable_reason is None


@dataclass(frozen=True)
class Section:
    key: str
    title: str
    kpis: list[Kpi] = field(default_factory=list)


@dataclass(frozen=True)
class Window:
    key: str
    start: date
    end: date
    previous_start: date
    previous_end: date

    @classmethod
    def ending(cls, key: str, as_of: date, days: int) -> Window:
        start = as_of - timedelta(days=days - 1)
        prev_end = start - timedelta(days=1)
        prev_start = prev_end - timedelta(days=days - 1)
        return cls(key, start, as_of, prev_start, prev_end)


def _ratio(numerator: float | None, denominator: float | None):
    if not denominator or numerator is None:
        return None
    return numerator / denominator


def _value(value: Any, **breakdown: Any) -> dict:
    """A KPI cell: the headline number plus whatever breaks it down."""
    return {"value": value, **({"breakdown": breakdown} if breakdown else {})}


# ---------------------------------------------------------------------------
# 9.1 Revenue
# ---------------------------------------------------------------------------


async def _mrr(session: AsyncSession, at: datetime) -> dict:
    by_org = await q.mrr_by_org(session, at=at)
    return _value(sum(by_org.values()), accounts=len(by_org))


async def _arr(session: AsyncSession, at: datetime) -> dict:
    by_org = await q.mrr_by_org(session, at=at)
    return _value(sum(by_org.values()) * q.MONTHS_PER_YEAR)


async def _mrr_movement(session: AsyncSession, start: date, end: date) -> dict:
    """New, expansion, contraction and churn between the window's two ends,
    per account, from the mandates active at each end."""
    before = await q.mrr_by_org(session, at=q.end_of_day_utc(start - timedelta(days=1)))
    after = await q.mrr_by_org(session, at=q.end_of_day_utc(end))
    new = expansion = contraction = churned = 0
    for org, now in after.items():
        was = before.get(org)
        if was is None:
            new += now
        elif now > was:
            expansion += now - was
        elif now < was:
            contraction += was - now
    for org, was in before.items():
        if org not in after:
            churned += was
    net = new + expansion - contraction - churned
    return _value(
        net, new=new, expansion=expansion, contraction=contraction, churned=churned
    )


async def _nrr(session: AsyncSession, start: date, end: date) -> dict:
    before = await q.mrr_by_org(session, at=q.end_of_day_utc(start - timedelta(days=1)))
    after = await q.mrr_by_org(session, at=q.end_of_day_utc(end))
    start_mrr = sum(before.values())
    retained = sum(after.get(org, 0) for org in before)
    return _value(_ratio(retained, start_mrr), start_mrr=start_mrr, retained=retained)


async def _topup_revenue(session: AsyncSession, start: date, end: date) -> dict:
    sales = await q.topup_sales(session, start=start, end=end)
    return _value(sales["amount_paise"], count=sales["count"], credits=sales["credits"])


async def _topup_attach(session: AsyncSession, start: date, end: date) -> dict:
    sales = await q.topup_sales(session, start=start, end=end)
    paying = await q.paying_org_count(session, start=start, end=end)
    return _value(
        _ratio(sales["buyers"], paying), buyers=sales["buyers"], paying=paying
    )


async def _overage(session: AsyncSession, start: date, end: date) -> dict:
    row = await q.overage_credits(session, start=start, end=end)
    return _value(row["credits"], calls=row["calls"], minutes=row["minutes"])


async def _deferred(session: AsyncSession, at: datetime) -> dict:
    return _value(await q.deferred_revenue_paise(session, at=at))


async def _export_revenue(session: AsyncSession, start: date, end: date) -> dict:
    exp = await q.export_revenue(session, start=start, end=end)
    return _value(exp["total_paise"], invoices=exp["count"])


async def _gst(session: AsyncSession, start: date, end: date) -> dict:
    gst = await q.gst_collected(session, start=start, end=end)
    return _value(sum(gst.values()), **gst)


async def _partner_liability(session: AsyncSession, at: datetime) -> dict:
    row = await q.partner_liability(session, at=at)
    return _value(row["amount_paise"], statements=row["count"])


async def _credit_notes(session: AsyncSession, start: date, end: date) -> dict:
    row = await q.credit_notes(session, start=start, end=end)
    return _value(row["total_paise"], count=row["count"])


# ---------------------------------------------------------------------------
# 9.2 Customers and funnel
# ---------------------------------------------------------------------------


async def _signups(session: AsyncSession, start: date, end: date) -> dict:
    row = await q.signups_by_source(session, start=start, end=end)
    return _value(row["total"], **row["by_source"])


async def _activation(session: AsyncSession, start: date, end: date) -> dict:
    row = await q.activation(session, start=start, end=end)
    return _value(_ratio(row["activated"], row["signups"]), **row)


async def _free_to_paid(session: AsyncSession, start: date, end: date) -> dict:
    row = await q.free_to_paid(session, start=start, end=end)
    return _value(_ratio(row["paid"], row["signups"]), **row)


async def _paying_by_plan(session: AsyncSession, at: datetime) -> dict:
    by_plan = await q.paying_orgs_by_plan(session, at=at)
    return _value(sum(by_plan.values()), **by_plan)


async def _churn(session: AsyncSession, start: date, end: date) -> dict:
    before = await q.mrr_by_org(session, at=q.end_of_day_utc(start - timedelta(days=1)))
    after = await q.mrr_by_org(session, at=q.end_of_day_utc(end))
    lost = [org for org in before if org not in after]
    logo = _ratio(len(lost), len(before))
    revenue = _ratio(sum(before[org] for org in lost), sum(before.values()))
    return _value(logo, churned=len(lost), at_start=len(before), revenue_churn=revenue)


async def _plan_moves(session: AsyncSession, start: date, end: date) -> dict:
    """Upgrades and downgrades: a plan mandate authorised in the window on an
    account that held a different-priced one before."""
    upgrades = downgrades = 0
    for row in await q.plan_mandate_history(session, start=start, end=end):
        started = row["started_at"]
        lo, _ = q.ist_day_bounds_utc(start)
        if started is None or started < lo:
            continue
        previous = await q.previous_mandate_price(
            session, organization_id=row["organization_id"], before=started
        )
        if previous is None:
            continue
        now = row["price_paise"] / (
            q.MONTHS_PER_YEAR if row["billing_period"] == q.ANNUAL else 1
        )
        if now > previous:
            upgrades += 1
        elif now < previous:
            downgrades += 1
    return _value(upgrades - downgrades, upgrades=upgrades, downgrades=downgrades)


async def _time_to_paid(session: AsyncSession, start: date, end: date) -> dict:
    return _value(await q.median_days_to_first_payment(session, start=start, end=end))


async def _champions(session: AsyncSession, start: date, end: date) -> dict:
    return _value(await q.champion_accounts(session, start=start, end=end))


async def _campus(session: AsyncSession, at: datetime) -> dict:
    by_plan = await q.paying_orgs_by_plan(session, at=at)
    return _value(by_plan.get("campus", 0))


# ---------------------------------------------------------------------------
# 9.3 Usage and unit economics
# ---------------------------------------------------------------------------

TEXT_EVENT_KINDS = (
    events.TEXT_REPLY,
    events.KNOWLEDGE_ANSWER,
    events.ROUTINE_RUN,
    events.TOOL_CALL,
    events.TOOL_CALL_PREMIUM,
)


async def _voice_minutes(session: AsyncSession, start: date, end: date) -> dict:
    row = await q.voice_minutes(session, start=start, end=end)
    return _value(
        row["minutes"],
        calls=row["calls"],
        by_language=row["by_language"],
        by_provider=row["by_provider"],
    )


async def _text_events(session: AsyncSession, start: date, end: date) -> dict:
    counts = await q.text_events(session, start=start, end=end, kinds=TEXT_EVENT_KINDS)
    return _value(sum(counts.values()), **counts)


async def _credits_consumed(session: AsyncSession, start: date, end: date) -> dict:
    row = await q.credits_consumed(session, start=start, end=end)
    return _value(row["credits"], **row["by_kind"])


async def _sold_vs_consumed(session: AsyncSession, start: date, end: date) -> dict:
    sold = await q.credits_sold(session, start=start, end=end)
    used = await q.credits_consumed(session, start=start, end=end)
    by_plan = await q.consumption_by_plan(session, start=start, end=end)
    return _value(
        _ratio(used["credits"], sold["credits"]),
        sold=sold["credits"],
        consumed=used["credits"],
        by_plan={
            code: {**row, "ratio": _ratio(row["consumed"], row["sold"])}
            for code, row in by_plan.items()
        },
    )


async def _voice_margin(session: AsyncSession, start: date, end: date) -> dict:
    row = await q.voice_economics(session, start=start, end=end)
    margin = row["revenue_paise"] - row["provider_cost_paise"]
    minutes = row["billable_seconds"] / SECONDS_PER_MINUTE
    return _value(
        _ratio(margin, row["revenue_paise"]),
        revenue_paise=row["revenue_paise"],
        provider_cost_paise=row["provider_cost_paise"],
        margin_paise=margin,
        margin_paise_per_minute=int(margin / minutes) if minutes else None,
    )


async def _indic_cost(session: AsyncSession, start: date, end: date) -> dict:
    row = await q.voice_economics(session, start=start, end=end)
    minutes = row["indic_seconds"] / SECONDS_PER_MINUTE
    per_minute = int(row["indic_provider_cost_paise"] / minutes) if minutes else None
    return _value(
        per_minute,
        assumption_paise_per_minute=INDIC_COST_ASSUMPTION_PAISE_PER_MINUTE,
        indic_minutes=int(minutes),
        by_language=[
            {
                "language": lang["language"],
                "paise_per_minute": (
                    int(
                        lang["provider_cost_paise"]
                        / (lang["billable_seconds"] / SECONDS_PER_MINUTE)
                    )
                    if lang["billable_seconds"]
                    else None
                ),
            }
            for lang in row["by_language"]
        ],
    )


async def _sarvam_burn(session: AsyncSession, start: date, end: date) -> dict:
    """Month-to-date Sarvam spend against the startup credit. The window is
    ignored on purpose — the credit is monthly — and the projection uses the
    month's own daily rate."""
    month_start = end.replace(day=1)
    spend = await q.provider_spend(
        session, start=month_start, end=end, provider_like="%sarvam%"
    )
    elapsed = (end - month_start).days + 1
    daily = spend["cost_paise"] / elapsed if elapsed else 0
    remaining = SARVAM_CREDIT_PAISE - spend["cost_paise"]
    exhausted_on = None
    if daily > 0 and remaining > 0:
        exhausted_on = (end + timedelta(days=int(remaining / daily))).isoformat()
    elif remaining <= 0:
        exhausted_on = end.isoformat()
    return _value(
        spend["cost_paise"],
        credit_paise=SARVAM_CREDIT_PAISE,
        remaining_paise=remaining,
        daily_paise=int(daily),
        projected_exhaustion=exhausted_on,
        month=month_start.isoformat()[:7],
    )


async def _markup(session: AsyncSession, start: date, end: date) -> dict:
    configured = {
        row["component"]: row["markup_bps"]
        for row in markup.component_multipliers()
        if row["provider"] is None
    }
    rows = await q.markup_realised(session, start=start, end=end)
    by_component = {}
    for row in rows:
        realised = (
            int(row["billed_paise"] * 10_000 / row["provider_cost_paise"])
            if row["provider_cost_paise"]
            else None
        )
        by_component[row["component"]] = {
            "realised_bps": realised,
            "configured_bps": configured.get(row["component"]),
            "billed_paise": row["billed_paise"],
            "provider_cost_paise": row["provider_cost_paise"],
        }
    billed = sum(r["billed_paise"] for r in rows)
    cost = sum(r["provider_cost_paise"] for r in rows)
    return _value(int(billed * 10_000 / cost) if cost else None, **by_component)


async def _builder(session: AsyncSession, start: date, end: date) -> dict:
    row = await q.builder_past_allowance(
        session, start=start, end=end, ref_type=events.BUILDER_MESSAGE
    )
    return _value(row["messages"], orgs_past_allowance=row["orgs"])


async def _knowledge_pages(session: AsyncSession, at: datetime) -> dict:
    rows = await q.knowledge_pages_per_org(session)
    if not rows:
        return _value(None, orgs=0, over_cap=0, share_over_cap=None)
    codes = sorted({row["plan_code"] for row in rows})
    caps = await plan_limits.limits_for_plans(session, plan_codes=codes)
    over = 0
    for row in rows:
        cap = caps.get(row["plan_code"], {}).get(knowledge_pages.CAP_KEY)
        if cap is not None and row["pages"] > cap:
            over += 1
    pages = sorted(row["pages"] for row in rows)
    median = pages[len(pages) // 2]
    return _value(
        median, orgs=len(rows), over_cap=over, share_over_cap=over / len(rows)
    )


# ---------------------------------------------------------------------------
# 9.4 Quality and reliability
# ---------------------------------------------------------------------------


async def _answer_rate(session: AsyncSession, start: date, end: date) -> dict:
    row = await q.call_outcomes(session, start=start, end=end)
    return _value(
        _ratio(row["inbound_answered"], row["inbound_offered"]),
        offered=row["inbound_offered"],
        answered=row["inbound_answered"],
    )


async def _completion_rate(session: AsyncSession, start: date, end: date) -> dict:
    row = await q.call_outcomes(session, start=start, end=end)
    return _value(
        _ratio(row["completed"], row["calls"]),
        calls=row["calls"],
        completed=row["completed"],
    )


async def _transfer_rate(session: AsyncSession, start: date, end: date) -> dict:
    row = await q.call_outcomes(session, start=start, end=end)
    return _value(
        _ratio(row["transferred"], row["calls"]), transferred=row["transferred"]
    )


async def _attention_rate(session: AsyncSession, start: date, end: date) -> dict:
    runs = (await q.call_outcomes(session, start=start, end=end))["runs"]
    counts = await q.event_counts(
        session, start=start, end=end, kinds=(AgentEventKind.NEEDS_ATTENTION.value,)
    )
    n = counts[AgentEventKind.NEEDS_ATTENTION.value]
    return _value(_ratio(n, runs), events=n, runs=runs)


async def _could_not_rate(session: AsyncSession, start: date, end: date) -> dict:
    runs = (await q.call_outcomes(session, start=start, end=end))["runs"]
    counts = await q.event_counts(
        session, start=start, end=end, kinds=(AgentEventKind.COULD_NOT.value,)
    )
    n = counts[AgentEventKind.COULD_NOT.value]
    return _value(_ratio(n, runs), events=n, runs=runs)


def _stage(name: str):
    async def _fn(session: AsyncSession, start: date, end: date) -> dict:
        row = await q.latency_percentiles(session, start=start, end=end)
        return _value(
            row[name]["p95_ms"], p50_ms=row[name]["p50_ms"], turns=row["turns"]
        )

    return _fn


async def _connector_errors(session: AsyncSession, start: date, end: date) -> dict:
    row = await q.connector_errors(session, start=start, end=end)
    return _value(
        _ratio(row["errors"], row["calls"]),
        calls=row["calls"],
        errors=row["errors"],
        by_app=row["by_app"],
    )


async def _webhooks(session: AsyncSession, start: date, end: date) -> dict:
    row = await q.webhook_outcomes(session, start=start, end=end)
    finished = row["succeeded"] + row["dead_letter"]
    return _value(_ratio(row["succeeded"], finished), **row)


async def _routines(session: AsyncSession, start: date, end: date) -> dict:
    counts = await q.event_counts(
        session,
        start=start,
        end=end,
        kinds=(
            AgentEventKind.ROUTINE_FIRED.value,
            AgentEventKind.ROUTINE_SKIPPED.value,
        ),
    )
    fired = counts[AgentEventKind.ROUTINE_FIRED.value]
    skipped = counts[AgentEventKind.ROUTINE_SKIPPED.value]
    return _value(_ratio(fired, fired + skipped), fired=fired, skipped=skipped)


async def _qa(session: AsyncSession, start: date, end: date) -> dict:
    by_status = await q.eval_outcomes(session, start=start, end=end)
    passed = by_status.get("passed", 0)
    finished = sum(n for s, n in by_status.items() if s not in ("queued", "running"))
    return _value(_ratio(passed, finished), **by_status)


# ---------------------------------------------------------------------------
# 9.5 Trust and admin
# ---------------------------------------------------------------------------


async def _staff_actions(session: AsyncSession, start: date, end: date) -> dict:
    row = await q.superadmin_actions(session, start=start, end=end)
    return _value(row["total"], **row["by_action"])


async def _data_requests(session: AsyncSession, start: date, end: date) -> dict:
    row = await q.data_requests(session, start=start, end=end)
    return _value(row["erasures"] + row["exports"], **row)


async def _api_keys(session: AsyncSession, at: datetime) -> dict:
    row = await q.api_key_hygiene(session, at=at)
    return _value(row["stale"], **row)


# ---------------------------------------------------------------------------
# The board
# ---------------------------------------------------------------------------

_NO_UTM = "Signups carry no UTM or ad-click attribution yet; only referral and champion sources are recorded."
_NO_PROVIDER_LOG = "No provider cost is recorded per text event (there is no provider_cost_log); voice margin is on the board, text margin is not."

SECTIONS: list[Section] = [
    Section(
        "revenue",
        "Revenue",
        [
            Kpi(
                "mrr",
                "MRR",
                "Sum of active subscription prices, annual divided by 12",
                "paise",
                "point",
                point=_mrr,
            ),
            Kpi("arr", "ARR", "MRR × 12", "paise", "point", point=_arr),
            Kpi(
                "mrr_movement",
                "MRR movement",
                "New + expansion − contraction − churned, by account, across the window",
                "paise",
                flow=_mrr_movement,
            ),
            Kpi(
                "nrr",
                "Net revenue retention",
                "MRR retained from accounts subscribed at the start of the window ÷ their MRR then",
                "ratio",
                flow=_nrr,
            ),
            Kpi(
                "topup_revenue",
                "Top-up revenue",
                "Packs paid for in the window, ₹ and credits",
                "paise",
                flow=_topup_revenue,
            ),
            Kpi(
                "topup_attach",
                "Top-up attach rate",
                "Accounts buying a top-up ÷ paying accounts",
                "ratio",
                flow=_topup_attach,
            ),
            Kpi(
                "overage_credits",
                "Overage credits billed",
                "Credits billed on calls priced past the plan, one credit a minute more",
                "credits",
                flow=_overage,
            ),
            Kpi(
                "enterprise_committed",
                "Enterprise committed revenue",
                "Prepaid minute contracts recognised monthly",
                "paise",
                unavailable_reason="No contract table exists yet; enterprise prepaids are recorded as plain top-ups.",
            ),
            Kpi(
                "deferred_revenue",
                "Deferred revenue",
                "Unused prepaid balance across customer accounts",
                "paise",
                "point",
                point=_deferred,
                good="none",
            ),
            Kpi(
                "export_revenue",
                "Export revenue",
                "Zero-rated export invoices issued in the window",
                "paise",
                flow=_export_revenue,
            ),
            Kpi(
                "gst_collected",
                "GST collected",
                "CGST, SGST and IGST on documents issued, credit notes netted",
                "paise",
                flow=_gst,
                good="none",
            ),
            Kpi(
                "partner_liability",
                "Partner commission liability",
                "Statements accrued and not yet paid",
                "paise",
                "point",
                point=_partner_liability,
                good="down",
            ),
            Kpi(
                "credit_notes",
                "Refunds and credit notes",
                "Credit notes issued in the window, count and ₹",
                "paise",
                flow=_credit_notes,
                good="down",
            ),
        ],
    ),
    Section(
        "customers",
        "Customers and funnel",
        [
            Kpi(
                "signups",
                "Free signups by source",
                "New customer accounts, by referral, champion or organic",
                "count",
                flow=_signups,
            ),
            Kpi(
                "activation",
                "Activation",
                "First bot run within 7 days ÷ signups",
                "ratio",
                flow=_activation,
            ),
            Kpi(
                "free_to_paid",
                "Free to paid conversion",
                "Paid within 30 days ÷ signups",
                "ratio",
                flow=_free_to_paid,
            ),
            Kpi(
                "cac",
                "CAC by channel",
                "Ad spend ÷ paid accounts from that channel",
                "paise",
                unavailable_reason="Ad spend is not in the database and " + _NO_UTM,
            ),
            Kpi(
                "paying_by_plan",
                "Paying accounts by plan",
                "Accounts with an authorised plan mandate, by plan",
                "count",
                "point",
                point=_paying_by_plan,
            ),
            Kpi(
                "churn",
                "Logo churn",
                "Subscribed accounts lost across the window ÷ subscribed at its start (revenue churn in the breakdown)",
                "ratio",
                flow=_churn,
                good="down",
            ),
            Kpi(
                "plan_moves",
                "Upgrades and downgrades",
                "Plan mandates replaced by a dearer or cheaper one",
                "count",
                flow=_plan_moves,
            ),
            Kpi(
                "time_to_paid",
                "Time to first paid event",
                "Median days from signup to first payment, for first payments in the window",
                "days",
                flow=_time_to_paid,
                good="down",
            ),
            Kpi(
                "champions",
                "Champion-sourced accounts",
                "Accounts attributed to a partner or champion in the window",
                "count",
                flow=_champions,
            ),
            Kpi(
                "campus",
                "Campus Builder accounts",
                "Accounts on the campus plan",
                "count",
                "point",
                point=_campus,
            ),
        ],
    ),
    Section(
        "usage",
        "Usage and unit economics",
        [
            Kpi(
                "voice_minutes",
                "Voice minutes",
                "Connected minutes on costed calls, by language and carrier",
                "minutes",
                flow=_voice_minutes,
            ),
            Kpi(
                "text_events",
                "Text events",
                "Metered replies, knowledge answers, routine runs and tool calls",
                "count",
                flow=_text_events,
            ),
            Kpi(
                "credits_consumed",
                "Credits consumed",
                "Credits spent, by what spent them",
                "credits",
                flow=_credits_consumed,
            ),
            Kpi(
                "sold_vs_consumed",
                "Credits consumed ÷ sold",
                "Consumption ratio; the model assumes 60–80%",
                "ratio",
                flow=_sold_vs_consumed,
                good="none",
            ),
            Kpi(
                "voice_margin",
                "Gross margin, voice",
                "(charged − provider cost) ÷ charged on costed calls",
                "ratio",
                flow=_voice_margin,
            ),
            Kpi(
                "text_margin",
                "Gross margin, text events",
                "(credits × ₹0.50 − provider cost) ÷ revenue per reply, answer, builder message",
                "ratio",
                unavailable_reason=_NO_PROVIDER_LOG,
            ),
            Kpi(
                "indic_cost",
                "Provider cost per Indic minute",
                "Provider cost ÷ connected minutes on non-English calls, against the ₹2.78 assumption",
                "paise",
                flow=_indic_cost,
                good="down",
            ),
            Kpi(
                "sarvam_burn",
                "Sarvam credit burn",
                "₹ of the ₹25,000 monthly credit used, with the projected exhaustion date",
                "paise",
                flow=_sarvam_burn,
                good="none",
            ),
            Kpi(
                "markup",
                "Markup realised vs configured",
                "Billed ÷ provider cost per component, beside the configured multiplier",
                "bps",
                flow=_markup,
                good="none",
            ),
            Kpi(
                "infra_cost",
                "Infra cost per minute",
                "AWS bill ÷ minutes",
                "paise",
                unavailable_reason="The AWS bill is not recorded in the database.",
            ),
            Kpi(
                "builder",
                "Builder messages past allowance",
                "Builder messages charged past the monthly allowance, and accounts paying",
                "count",
                flow=_builder,
                good="none",
            ),
            Kpi(
                "knowledge_pages",
                "Knowledge pages per account",
                "Median active pages per account, and the share over their plan's cap",
                "count",
                "point",
                point=_knowledge_pages,
                good="none",
            ),
            Kpi(
                "desktop_steps",
                "Desktop steps per task",
                "Median steps and cost per task",
                "count",
                unavailable_reason="Desktop tasks are not built (deferred: teach a task in a hosted browser).",
            ),
        ],
    ),
    Section(
        "quality",
        "Quality and reliability",
        [
            Kpi(
                "answer_rate",
                "Call answer rate",
                "Inbound answered ÷ inbound offered",
                "ratio",
                flow=_answer_rate,
            ),
            Kpi(
                "completion_rate",
                "Call completion rate",
                "Calls ended in the completed state ÷ calls",
                "ratio",
                flow=_completion_rate,
            ),
            Kpi(
                "transfer_rate",
                "Transfer rate",
                "Calls with a transfer ÷ calls",
                "ratio",
                flow=_transfer_rate,
                good="none",
            ),
            Kpi(
                "attention_rate",
                "Needs-attention rate",
                "needs_attention events ÷ runs",
                "ratio",
                flow=_attention_rate,
                good="down",
            ),
            Kpi(
                "could_not_rate",
                "Could-not rate",
                "could_not events ÷ runs",
                "ratio",
                flow=_could_not_rate,
                good="down",
            ),
            Kpi(
                "stt_latency",
                "STT latency p95",
                "User stopped → final transcript, p95 (p50 in the breakdown)",
                "ms",
                flow=_stage("stt"),
                good="down",
            ),
            Kpi(
                "llm_latency",
                "LLM latency p95",
                "Final transcript → first token",
                "ms",
                flow=_stage("llm"),
                good="down",
            ),
            Kpi(
                "tts_latency",
                "TTS latency p95",
                "First token → first audio byte",
                "ms",
                flow=_stage("tts"),
                good="down",
            ),
            Kpi(
                "first_response",
                "First-response latency p95",
                "User stopped → audio out; target under 700 ms",
                "ms",
                flow=_stage("first_response"),
                good="down",
            ),
            Kpi(
                "connector_errors",
                "Connector error rate",
                "Tool calls that ended in an error ÷ tool calls, by app (the recorded proxy for provider errors)",
                "ratio",
                flow=_connector_errors,
                good="down",
            ),
            Kpi(
                "concurrency",
                "Concurrency peak ÷ plan cap",
                "Peak simultaneous calls against the media box ceiling",
                "ratio",
                unavailable_reason="Peak concurrency is on the Campaigns screen per day; a plan-level cap is not stored, so the ratio cannot be formed here yet.",
            ),
            Kpi(
                "webhooks",
                "Webhook delivery success",
                "Succeeded ÷ finished deliveries",
                "ratio",
                flow=_webhooks,
            ),
            Kpi(
                "routines",
                "Routine fire rate",
                "Routines fired ÷ (fired + skipped); on-time within a minute is not recorded",
                "ratio",
                flow=_routines,
            ),
            Kpi(
                "qa",
                "QA pass rate",
                "Eval results passed ÷ finished",
                "ratio",
                flow=_qa,
            ),
        ],
    ),
    Section(
        "trust",
        "Trust and admin",
        [
            Kpi(
                "impersonation",
                "Impersonation events",
                "Who impersonated whom, when and why",
                "count",
                unavailable_reason="Impersonation is not audited to the database until KAN-82 ships.",
                good="down",
            ),
            Kpi(
                "staff_actions",
                "Superadmin actions",
                "Audited billing actions: credit adjustments, rate edits, markup changes",
                "count",
                flow=_staff_actions,
                good="none",
            ),
            Kpi(
                "aup_hits",
                "AUP screen hits",
                "Prompts blocked or flagged",
                "count",
                unavailable_reason="The acceptable-use screen is not built yet.",
                good="none",
            ),
            Kpi(
                "dnc_hits",
                "DNC hits",
                "Dials prevented by the do-not-call list",
                "count",
                unavailable_reason="Prevented dials are refused in memory and not written anywhere; needs a refusal row.",
                good="none",
            ),
            Kpi(
                "outside_window",
                "Calls outside window prevented",
                "Dials refused for falling outside calling hours",
                "count",
                unavailable_reason="Calling-window refusals are not recorded; same fix as DNC hits.",
                good="none",
            ),
            Kpi(
                "data_requests",
                "Data requests",
                "Erasure requests and exports in the window, with the median hours to complete",
                "count",
                flow=_data_requests,
                good="none",
            ),
            Kpi(
                "api_keys",
                "Stale API keys",
                "Live keys unused for 90 days, and the oldest key's age",
                "count",
                "point",
                point=_api_keys,
                good="down",
            ),
            Kpi(
                "failed_logins",
                "Failed logins and rate-limit trips",
                "By IP",
                "count",
                unavailable_reason="Login failures and limiter trips are not persisted.",
                good="down",
            ),
        ],
    ),
]


def catalogue() -> list[dict]:
    """The board's rows without values: for tests and the screen's skeleton."""
    return [
        {
            "key": section.key,
            "title": section.title,
            "kpis": [
                {
                    "key": kpi.key,
                    "label": kpi.label,
                    "definition": kpi.definition,
                    "unit": kpi.unit,
                    "shape": kpi.shape,
                    "available": kpi.available,
                    "unavailable_reason": kpi.unavailable_reason,
                    "good": kpi.good,
                }
                for kpi in section.kpis
            ],
        }
        for section in SECTIONS
    ]


async def _cell(
    kpi: Kpi, session: AsyncSession, window: Window, *, previous: bool
) -> dict | None:
    if not kpi.available:
        return None
    if kpi.shape == "point":
        assert kpi.point is not None
        day = window.previous_end if previous else window.end
        return await kpi.point(session, q.end_of_day_utc(day))
    assert kpi.flow is not None
    start, end = (
        (window.previous_start, window.previous_end)
        if previous
        else (window.start, window.end)
    )
    return await kpi.flow(session, start, end)


async def board(session: AsyncSession, *, as_of: date | None = None) -> dict:
    """Every KPI, for every window, with the previous period beside it."""
    as_of = as_of or datetime.now(IST).date()
    windows = [Window.ending(key, as_of, days) for key, days in WINDOWS.items()]
    sections = []
    for section in SECTIONS:
        rows = []
        for kpi in section.kpis:
            values = {}
            for window in windows:
                values[window.key] = {
                    "current": await _cell(kpi, session, window, previous=False),
                    "previous": await _cell(kpi, session, window, previous=True),
                }
            rows.append(
                {
                    "key": kpi.key,
                    "label": kpi.label,
                    "definition": kpi.definition,
                    "unit": kpi.unit,
                    "shape": kpi.shape,
                    "available": kpi.available,
                    "unavailable_reason": kpi.unavailable_reason,
                    "good": kpi.good,
                    "values": values,
                }
            )
        sections.append({"key": section.key, "title": section.title, "kpis": rows})
    return {
        "as_of": as_of.isoformat(),
        "windows": [
            {
                "key": w.key,
                "start": w.start.isoformat(),
                "end": w.end.isoformat(),
                "previous_start": w.previous_start.isoformat(),
                "previous_end": w.previous_end.isoformat(),
            }
            for w in windows
        ],
        "sections": sections,
        "unavailable": [
            {"key": kpi.key, "label": kpi.label, "reason": kpi.unavailable_reason}
            for section in SECTIONS
            for kpi in section.kpis
            if not kpi.available
        ],
    }


# Guard against a KPI declared with neither a query nor a reason — that is a
# row that would render blank without saying why, the exact thing the board
# exists to prevent. Checked at import so it cannot reach production.
for _section in SECTIONS:
    for _kpi in _section.kpis:
        if _kpi.available and not (
            (_kpi.shape == "flow" and inspect.iscoroutinefunction(_kpi.flow))
            or (_kpi.shape == "point" and inspect.iscoroutinefunction(_kpi.point))
        ):
            raise TypeError(f"KPI {_kpi.key!r} has neither a query nor a reason")
