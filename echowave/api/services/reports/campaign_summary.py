"""The aggregate view of a campaign: rates, retries, languages, daily progress.

`run_report.py` answers "what happened on each call" and streams a row per
run. This answers "how is the campaign doing", which is a different question
and the one a tender asks — connection and completion rates, retry statistics,
language distribution and progress by day (Telangana tender §10).

**The definitions are the whole content of this module.** The arithmetic is
division; the part that can be wrong is what goes above and below the line, and
a rate computed against the wrong denominator is confidently, plausibly wrong.
So they are fixed here, once, and match the admin dashboard's campaign query in
``db/billing_dashboard_client.py`` exactly:

* **Attempted** — a workflow run exists. One row per dial, so a retry is its
  own attempt rather than an amendment to the first one.
* **Connected** — ``answered_at`` is set. This is the carrier telling us the
  callee picked up, not our own guess from call duration.
* **Completed** — ``is_completed``. The run reached the end of its workflow.

The two rates chain deliberately and do not share a denominator:

* ``connection_rate = connected / attempted`` — did the phone get answered.
* ``completion_rate = completed / connected`` — **of the answered calls**, how
  many ran to the end. A call nobody picked up cannot complete a conversation,
  so including it in the denominator would report the agent as failing at
  something it never got to attempt, and the two rates would then multiply into
  a third number nobody defined.

A rate with nothing in its denominator is ``None``, never ``0.0``. Zero is a
measurement; this is the absence of one, and the codebase distinguishes them
everywhere else (see HANDOVER.md §6) because a fresh campaign reporting 0%
connection reads as a broken campaign.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import Integer, case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.db.models import CampaignModel, QueuedRunModel, WorkflowRunModel

#: Postgres timezone name. Daily progress buckets by IST calendar day because
#: the campaigns are dialled in India and an operator comparing this to their
#: own day would otherwise find the boundary 5h30m out.
_IST_SQL = "Asia/Kolkata"


def _rate(numerator: int, denominator: int) -> float | None:
    """A proportion, or ``None`` when nothing was measured.

    Rounded to four places: these are rendered as percentages to one decimal,
    and carrying full float precision into JSON invites a report that differs
    from the dashboard in the twelfth digit.
    """
    if denominator <= 0:
        return None
    return round(numerator / denominator, 4)


def _ist_date_expr():
    """The IST calendar date of a run, as SQL.

    ``created_at`` is ``timestamptz``; ``AT TIME ZONE 'Asia/Kolkata'`` converts
    it to local wall-clock time, which is then truncated to a date.
    """
    return func.date(func.timezone(_IST_SQL, WorkflowRunModel.created_at))


def _seconds_expr():
    """Talk time per run, in seconds.

    ``billable_seconds`` is the costed measure and is only set once the cost
    engine has run, so it is absent for calls in flight and for any campaign on
    a deployment where costing has stopped. ``usage_info -> call_duration_seconds``
    is written by the pipeline at call end and is present either way, so it is
    the fallback rather than the primary: the costed number is the one that
    reconciles with an invoice.
    """
    return func.coalesce(
        WorkflowRunModel.billable_seconds,
        func.cast(
            func.nullif(
                WorkflowRunModel.usage_info["call_duration_seconds"].as_string(), ""
            ),
            Integer,
        ),
        0,
    )


async def _totals(session: AsyncSession, *, campaign_id: int) -> dict[str, Any]:
    row = (
        await session.execute(
            select(
                func.count(WorkflowRunModel.id).label("attempted"),
                func.count(WorkflowRunModel.answered_at).label("connected"),
                func.coalesce(
                    func.sum(
                        case((WorkflowRunModel.is_completed.is_(True), 1), else_=0)
                    ),
                    0,
                ).label("completed"),
                func.coalesce(func.sum(_seconds_expr()), 0).label("seconds"),
            ).where(WorkflowRunModel.campaign_id == campaign_id)
        )
    ).one()

    attempted = int(row.attempted or 0)
    connected = int(row.connected or 0)
    completed = int(row.completed or 0)
    seconds = int(row.seconds or 0)

    return {
        "attempted": attempted,
        "connected": connected,
        "completed": completed,
        "connection_rate": _rate(connected, attempted),
        # Denominator is connected, not attempted — see the module docstring.
        "completion_rate": _rate(completed, connected),
        "talk_minutes": round(seconds / 60, 2),
    }


async def _retries(session: AsyncSession, *, campaign_id: int) -> dict[str, Any]:
    """Retry statistics, read from the queue rather than from the calls.

    ``queued_runs`` is where a retry exists as a distinct decision: a row with
    ``retry_count > 0`` was scheduled *because* an earlier attempt failed for a
    stated reason. The workflow runs cannot answer this — a redial looks like an
    ordinary call once it is placed.
    """
    rows = (
        await session.execute(
            select(
                QueuedRunModel.retry_reason,
                func.count(QueuedRunModel.id).label("count"),
            )
            .where(
                QueuedRunModel.campaign_id == campaign_id,
                QueuedRunModel.retry_count > 0,
            )
            .group_by(QueuedRunModel.retry_reason)
        )
    ).all()

    by_reason = {(r.retry_reason or "unknown"): int(r.count or 0) for r in rows}

    # Contacts that were retried at all, as opposed to retry attempts. A number
    # that a customer reads as "how many people did we have to chase".
    contacts_retried = (
        await session.execute(
            select(
                func.count(func.distinct(QueuedRunModel.parent_queued_run_id))
            ).where(
                QueuedRunModel.campaign_id == campaign_id,
                QueuedRunModel.retry_count > 0,
                QueuedRunModel.parent_queued_run_id.isnot(None),
            )
        )
    ).scalar() or 0

    # Still waiting for their scheduled slot. Reported because a campaign that
    # looks finished can have retries pending, and calling it complete while
    # redials are queued is how a reach target gets declared missed early.
    pending = (
        await session.execute(
            select(func.count(QueuedRunModel.id)).where(
                QueuedRunModel.campaign_id == campaign_id,
                QueuedRunModel.retry_count > 0,
                QueuedRunModel.state == "queued",
            )
        )
    ).scalar() or 0

    return {
        "retry_attempts": sum(by_reason.values()),
        "contacts_retried": int(contacts_retried),
        "retries_pending": int(pending),
        "by_reason": by_reason,
    }


async def _languages(
    session: AsyncSession, *, campaign_id: int
) -> list[dict[str, Any]]:
    """Language distribution over calls that actually connected.

    Deliberately not over every attempt: an unanswered dial has no conversation
    and its language is whatever the agent was configured for, so counting it
    would describe the campaign's configuration rather than the population it
    reached — which is the thing §10 asks about.
    """
    rows = (
        await session.execute(
            select(
                WorkflowRunModel.language,
                func.count(WorkflowRunModel.id).label("calls"),
            )
            .where(
                WorkflowRunModel.campaign_id == campaign_id,
                WorkflowRunModel.answered_at.isnot(None),
            )
            .group_by(WorkflowRunModel.language)
            .order_by(func.count(WorkflowRunModel.id).desc())
        )
    ).all()

    total = sum(int(r.calls or 0) for r in rows)
    return [
        {
            # NULL is reported as "unknown" rather than dropped: a campaign where
            # the pipeline stopped recording language would otherwise show a
            # complete-looking distribution over the few rows that still have it.
            "language": r.language or "unknown",
            "calls": int(r.calls or 0),
            "share": _rate(int(r.calls or 0), total),
        }
        for r in rows
    ]


async def _daily(session: AsyncSession, *, campaign_id: int) -> list[dict[str, Any]]:
    day = _ist_date_expr().label("day")
    rows = (
        await session.execute(
            select(
                day,
                func.count(WorkflowRunModel.id).label("attempted"),
                func.count(WorkflowRunModel.answered_at).label("connected"),
                func.coalesce(
                    func.sum(
                        case((WorkflowRunModel.is_completed.is_(True), 1), else_=0)
                    ),
                    0,
                ).label("completed"),
                func.coalesce(func.sum(_seconds_expr()), 0).label("seconds"),
            )
            .where(WorkflowRunModel.campaign_id == campaign_id)
            .group_by(day)
            .order_by(day)
        )
    ).all()

    out = []
    for r in rows:
        attempted = int(r.attempted or 0)
        connected = int(r.connected or 0)
        out.append(
            {
                "date": r.day.isoformat() if isinstance(r.day, date) else str(r.day),
                "attempted": attempted,
                "connected": connected,
                "completed": int(r.completed or 0),
                "connection_rate": _rate(connected, attempted),
                "talk_minutes": round(int(r.seconds or 0) / 60, 2),
            }
        )
    return out


async def campaign_summary(
    session: AsyncSession, *, campaign: CampaignModel
) -> dict[str, Any]:
    """The §10 aggregate for one campaign.

    Takes the campaign row rather than an id because the caller has already
    fetched it with the organization filter applied, and re-reading it here
    without that filter is exactly how a tenant-isolation hole gets added by
    accident.
    """
    totals = await _totals(session, campaign_id=campaign.id)

    # Contacts loaded from the source, which is the denominator a customer
    # thinks in: "you were given 10,000 numbers". Distinct from attempted,
    # which counts dials and therefore counts a retried contact twice.
    contacts_total = campaign.total_rows

    return {
        "campaign": {
            "id": campaign.id,
            "name": campaign.name,
            "state": campaign.state,
            "contacts_total": contacts_total,
            "contacts_processed": campaign.processed_rows,
            "contacts_failed": campaign.failed_rows,
            "started_at": campaign.started_at.isoformat()
            if campaign.started_at
            else None,
            "completed_at": campaign.completed_at.isoformat()
            if campaign.completed_at
            else None,
        },
        "totals": {
            **totals,
            "contacts_total": contacts_total,
            # Reach against the list we were given, rather than against the
            # dials we made. This is the number a reach target is written in.
            "contact_connection_rate": _rate(totals["connected"], contacts_total or 0),
        },
        "retries": await _retries(session, campaign_id=campaign.id),
        "languages": await _languages(session, campaign_id=campaign.id),
        "daily": await _daily(session, campaign_id=campaign.id),
        "generated_at": datetime.now().astimezone().isoformat(),
    }
