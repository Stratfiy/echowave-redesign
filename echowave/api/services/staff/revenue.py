"""Revenue, costs and beta economics (handoff 37; screen 37).

Reuses the billing dashboard's sources -- the daily rollup for usage charged
and provider cost, the payments table for cash collected, the cost items for
the provider/category breakdown -- and adds what they lacked: refunds from
the ledger, the free-beta view, cost per useful user and cost per success.

Kept apart, never summed into each other: cash collected, refunds, recurring
subscription value, and usage charged (what the rate card charged for work
done; whether that is recognised revenue is the finance owner's rule). Direct
contribution is net cash less direct provider cost; infrastructure is not
allocated here and is labelled so. A ratio with a zero denominator is
``None`` ("undefined"), never zero cost.

Amounts are paise (INR). Dollar payments are reported in rupees at the rate
pinned on each payment, as the billing screens already do.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api import constants
from api.db import billing_dashboard_client as dash
from api.db.models import DailyOrganizationRollupModel, WorkflowRunModel
from api.db.staff_models import StaffRefundModel
from api.services import features
from api.services.billing.rollup import IST
from api.services.staff import analytics

R = DailyOrganizationRollupModel


def ist_range(days: int, today: date | None = None) -> tuple[date, date]:
    today = today or datetime.now(IST).date()
    return today - timedelta(days=days - 1), today


async def report(session: AsyncSession, *, days: int) -> dict[str, Any]:
    start, end = ist_range(days)
    start_utc = datetime.combine(start, datetime.min.time(), IST).astimezone(UTC)
    end_utc = datetime.combine(
        end + timedelta(days=1), datetime.min.time(), IST
    ).astimezone(UTC)

    totals = await dash.overview_totals(session, start=start, end=end)
    payments = await dash.payments_summary(session, start=start, end=end)
    collected = payments["collected"]
    refunded = int(
        await session.scalar(
            select(func.coalesce(func.sum(StaffRefundModel.amount_minor), 0)).where(
                StaffRefundModel.state == "refunded",
                StaffRefundModel.currency == "INR",
                StaffRefundModel.reconciled_at >= start_utc,
                StaffRefundModel.reconciled_at < end_utc,
            )
        )
        or 0
    )
    refund_pending = int(
        await session.scalar(
            select(func.count(StaffRefundModel.id)).where(
                StaffRefundModel.state.in_(("requested", "pending", "outcome_unknown"))
            )
        )
        or 0
    )
    uncosted = int(
        await session.scalar(
            select(func.count(WorkflowRunModel.id)).where(
                WorkflowRunModel.created_at >= start_utc,
                WorkflowRunModel.created_at < end_utc,
                WorkflowRunModel.is_completed.is_(True),
                WorkflowRunModel.costed_at.is_(None),
            )
        )
        or 0
    )

    provider_cost = totals["provider_cost_paise"]
    outcomes = await analytics.useful_outcomes(session, since=start_utc, until=end_utc)
    useful_users = len({o.user_id for o in outcomes})
    successes = len(outcomes)
    free = features.is_on("free_mode")
    budget = constants.STAFF_PILOT_BUDGET_PAISE
    spent_to_date = int(
        await session.scalar(select(func.coalesce(func.sum(R.provider_cost_paise), 0)))
        or 0
    )
    net_cash = collected["gross_paise"] - refunded

    return {
        "period": {
            "start": start.isoformat(),
            "end": end.isoformat(),
            "days": days,
            "timezone": "Asia/Kolkata",
        },
        "currency": "INR",
        "free_mode": free,
        "revenue": {
            "cash_collected_paise": collected["gross_paise"],
            "cash_collected_net_of_tax_paise": collected["net_paise"],
            "tax_paise": collected["tax_paise"],
            "payments": collected["payments"],
            "refunds_paise": refunded,
            "refunds_pending": refund_pending,
            "net_cash_paise": net_cash,
            # No subscription ledger exists; a fake MRR would be worse than none.
            "recurring_value_paise": None,
            "recurring_value_reason": "Subscriptions are not recorded as a ledger yet.",
            "usage_charged_paise": totals["revenue_paise"],
            "usage_charged_note": "Charged by the rate card for work done; recognised revenue is the finance owner's definition.",
        },
        "costs": {
            "provider_cost_paise": provider_cost,
            "estimated": uncosted > 0,
            "uncosted_runs": uncosted,
            "infrastructure": None,
            "infrastructure_reason": "Not allocated here; shown separately once the finance owner sets the rule.",
        },
        "contribution": {
            "direct_paise": net_cash - provider_cost,
            "definition": "Net cash collected (less refunds) minus direct provider cost. Infrastructure excluded.",
        },
        "beta": {
            "spend_paise": provider_cost,
            "useful_users": useful_users,
            "cost_per_useful_user_paise": round(provider_cost / useful_users)
            if useful_users
            else None,
            "subsidy_paise": max(0, provider_cost - totals["revenue_paise"]),
            "budget": {
                "state": "needs_setup" if budget is None else "ok",
                "limit_paise": budget,
                "spent_to_date_paise": spent_to_date,
                "remaining_paise": None if budget is None else budget - spent_to_date,
                "warning": budget is not None and spent_to_date >= 0.8 * budget,
                "setting": "STAFF_PILOT_BUDGET_PAISE",
            },
        },
        "cost_per_success": {
            "value_paise": round(provider_cost / successes) if successes else None,
            "successes": successes,
            "state": "ok" if successes else "undefined",
            "definition": "All provider cost in the period, failed attempts included, divided by useful outcomes in the same period.",
        },
        "trend": await dash.daily_series(session, start=start, end=end),
        "breakdown": await dash.cost_composition_series(session, start=start, end=end),
        "as_of": datetime.now(UTC).isoformat(),
    }
