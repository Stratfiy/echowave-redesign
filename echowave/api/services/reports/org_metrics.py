"""Answer rate and cost-per-outcome, across an organization's calls.

Two numbers a customer reads to judge whether the product is working and
whether it pays — both queries over data already stored, no new capture
needed:

* **ASR (Answer-Seizure Ratio)** = connected / attempted. The same signal
  ``campaign_summary.py``'s ``connection_rate`` already uses
  (``answered_at IS NOT NULL`` — the carrier telling us the callee picked
  up, not a guess from call duration), computed here across every call in
  the org rather than one campaign.

* **Cost by outcome** = charged paise, grouped by
  ``gathered_context->>'mapped_call_disposition'``. There is no fixed,
  cross-tenant "booking" taxonomy — each workflow's prompt or End Call tool
  produces its own disposition strings — so this reports cost per call for
  every disposition an account's calls actually produced. A customer reads
  whichever row is their own success outcome ("booked", "qualified",
  whatever their workflow calls it) rather than trusting one blessed
  universal number that doesn't exist across tenants.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.db.models import WorkflowModel, WorkflowRunModel

_IST = ZoneInfo("Asia/Kolkata")


def _ist_day_bounds_utc(days: int) -> tuple[datetime, datetime]:
    """UTC bounds covering the last ``days`` IST calendar days, inclusive of today."""
    today_ist = datetime.now(_IST).date()
    start_ist = datetime(
        today_ist.year, today_ist.month, today_ist.day, tzinfo=_IST
    ) - timedelta(days=days - 1)
    end_ist = start_ist + timedelta(days=days)
    return start_ist.astimezone(ZoneInfo("UTC")), end_ist.astimezone(ZoneInfo("UTC"))


def _rate(numerator: int, denominator: int) -> float | None:
    """A proportion, or ``None`` when nothing was measured — never ``0.0``
    for an empty denominator, matching campaign_summary.py's convention."""
    if denominator <= 0:
        return None
    return round(numerator / denominator, 4)


async def answer_seizure_ratio(
    session: AsyncSession, *, organization_id: int, days: int
) -> dict[str, Any]:
    start_utc, end_utc = _ist_day_bounds_utc(days)
    row = (
        await session.execute(
            select(
                func.count(WorkflowRunModel.id).label("attempted"),
                func.count(WorkflowRunModel.answered_at).label("connected"),
            )
            .join(WorkflowModel, WorkflowRunModel.workflow_id == WorkflowModel.id)
            .where(
                WorkflowModel.organization_id == organization_id,
                WorkflowRunModel.created_at >= start_utc,
                WorkflowRunModel.created_at < end_utc,
            )
        )
    ).one()

    attempted = int(row.attempted or 0)
    connected = int(row.connected or 0)
    return {
        "attempted": attempted,
        "connected": connected,
        "asr": _rate(connected, attempted),
    }


async def cost_by_outcome(
    session: AsyncSession, *, organization_id: int, days: int
) -> list[dict[str, Any]]:
    start_utc, end_utc = _ist_day_bounds_utc(days)
    disposition = func.coalesce(
        WorkflowRunModel.gathered_context["mapped_call_disposition"].as_string(),
        "unknown",
    )

    rows = (
        await session.execute(
            select(
                disposition.label("disposition"),
                func.count(WorkflowRunModel.id).label("calls"),
                func.coalesce(
                    func.sum(func.coalesce(WorkflowRunModel.total_charged_paise, 0)), 0
                ).label("total_charged_paise"),
            )
            .join(WorkflowModel, WorkflowRunModel.workflow_id == WorkflowModel.id)
            .where(
                WorkflowModel.organization_id == organization_id,
                WorkflowRunModel.created_at >= start_utc,
                WorkflowRunModel.created_at < end_utc,
            )
            .group_by(disposition)
            .order_by(func.count(WorkflowRunModel.id).desc())
        )
    ).all()

    out = []
    for r in rows:
        calls = int(r.calls or 0)
        total_paise = int(r.total_charged_paise or 0)
        out.append(
            {
                "disposition": r.disposition,
                "calls": calls,
                "total_charged_paise": total_paise,
                "cost_per_call_paise": (total_paise // calls) if calls else None,
            }
        )
    return out
