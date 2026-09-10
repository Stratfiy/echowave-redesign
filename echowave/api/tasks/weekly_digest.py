"""Monday morning: what the account's agents did last week.

The bell is for now; this is for the founder who does not log in. One
mail an account a week — calls, minutes, spend, and the three calls QA
graded worst, each a link — so the product is present in the week of the
person paying for it even when nobody opens it. Under the bell too.

Only accounts that had at least one call: a digest of nothing is spam.
Safe to re-run: the dedupe key is the week, claimed before the send.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from loguru import logger
from sqlalchemy import func, select

from api.constants import UI_APP_URL
from api.db import db_client
from api.db.models import (
    DailyOrganizationRollupModel,
    WorkflowModel,
    WorkflowRunModel,
)
from api.services.messaging import announce, email
from api.services.messaging.announce import Notice
from api.services.review import verdict as review

KIND = "weekly_digest"
WORST_CALLS = 3


def _week(now: datetime) -> tuple[date, date]:
    """Last Monday to last Sunday, inclusive."""
    today = now.date()
    last_monday = today - timedelta(days=today.weekday() + 7)
    return last_monday, last_monday + timedelta(days=6)


def _credits(paise: int) -> str:
    return f"{paise / 100:,.0f} credits"


def compose(
    *,
    account_name: str,
    week_start: date,
    week_end: date,
    calls: int,
    minutes: int,
    charged_paise: int,
    worst: list[dict],
    app_url: str,
) -> Notice:
    base = (app_url or "").rstrip("/")
    lines = [
        f"{account_name}, the week of {week_start:%d %b} to {week_end:%d %b}:",
        "",
        f"  {calls} calls · {minutes} minutes · {_credits(charged_paise)} spent",
        "",
    ]
    if worst:
        lines.append("Calls worth a listen (lowest scores first):")
        for w in worst:
            score = f"{w['score']}/10" if w.get("score") is not None else "unscored"
            lines.append(
                f"  • {score} — {w.get('agent_name') or 'Agent'}: "
                f"{(w.get('summary') or 'no summary')[:140]}"
            )
            lines.append(f"    {base}/workflow/{w['workflow_id']}/run/{w['run_id']}")
        lines.append("")
    lines.append(f"The full queue is under Review:\n  {base}/review")
    lines.append(f"Spend by day is under Billing:\n  {base}/billing")
    return Notice(
        subject=f"Your Decibyl week: {calls} calls, {_credits(charged_paise)}",
        body="\n".join(lines),
        dedupe_key=f"week:{week_start.isoformat()}",
        link="/review",
    )


async def _totals(session, *, organization_id: int, start: date, end: date):
    row = (
        await session.execute(
            select(
                func.coalesce(func.sum(DailyOrganizationRollupModel.calls), 0),
                func.coalesce(
                    func.sum(DailyOrganizationRollupModel.billable_seconds), 0
                ),
                func.coalesce(func.sum(DailyOrganizationRollupModel.charged_paise), 0),
            ).where(
                DailyOrganizationRollupModel.organization_id == organization_id,
                DailyOrganizationRollupModel.day >= start,
                DailyOrganizationRollupModel.day <= end,
            )
        )
    ).one()
    return int(row[0]), int(row[1]), int(row[2])


async def _active_accounts(session, *, start: date, end: date) -> list[int]:
    rows = (
        await session.execute(
            select(DailyOrganizationRollupModel.organization_id)
            .where(
                DailyOrganizationRollupModel.day >= start,
                DailyOrganizationRollupModel.day <= end,
                DailyOrganizationRollupModel.calls > 0,
            )
            .distinct()
        )
    ).scalars()
    return sorted(set(rows))


async def _worst_calls(
    session, *, organization_id: int, start: date, end: date
) -> list[dict]:
    since = datetime.combine(start, datetime.min.time(), tzinfo=UTC)
    until = datetime.combine(end + timedelta(days=1), datetime.min.time(), tzinfo=UTC)
    rows = (
        await session.execute(
            select(WorkflowRunModel, WorkflowModel.name)
            .join(WorkflowModel, WorkflowModel.id == WorkflowRunModel.workflow_id)
            .where(
                WorkflowModel.organization_id == organization_id,
                WorkflowRunModel.created_at >= since,
                WorkflowRunModel.created_at < until,
                WorkflowRunModel.is_completed.is_(True),
                WorkflowRunModel.annotations.isnot(None),
            )
            .order_by(WorkflowRunModel.created_at.desc())
            .limit(500)
        )
    ).all()
    graded = []
    for run, agent_name in rows:
        v = review.from_annotations(run.annotations)
        if v is None or v.score is None:
            continue
        graded.append(
            {
                "run_id": run.id,
                "workflow_id": run.workflow_id,
                "agent_name": agent_name,
                "score": v.score,
                "summary": v.summary,
            }
        )
    graded.sort(key=lambda g: g["score"])
    return graded[:WORST_CALLS]


async def send_weekly_digests(ctx=None, *, now: datetime | None = None) -> dict:
    """Mail every account that had calls last week. Safe to re-run."""
    now = now or datetime.now(UTC)
    start, end = _week(now)
    counters = {"considered": 0, "sent": 0, "skipped": 0, "failed": 0}
    if not email.email_is_configured():
        logger.info("Weekly digests skipped: no SMTP host configured")
        return counters

    async with db_client.async_session() as session:
        organization_ids = await _active_accounts(session, start=start, end=end)
    counters["considered"] = len(organization_ids)

    for organization_id in organization_ids:
        try:
            async with db_client.async_session() as session:
                calls, seconds, charged = await _totals(
                    session, organization_id=organization_id, start=start, end=end
                )
                worst = await _worst_calls(
                    session, organization_id=organization_id, start=start, end=end
                )
                organization = await db_client.get_organization_by_id(organization_id)
            account_name = (
                getattr(organization, "billing_name", None)
                or getattr(organization, "provider_id", None)
                or "Your account"
            )
            sent = await announce.announce(
                organization_id=organization_id,
                kind=KIND,
                notice=compose(
                    account_name=account_name,
                    week_start=start,
                    week_end=end,
                    calls=calls,
                    minutes=round(seconds / 60),
                    charged_paise=charged,
                    worst=worst,
                    app_url=UI_APP_URL,
                ),
            )
            counters["sent" if sent else "skipped"] += 1
        except Exception:
            logger.exception(
                "Weekly digest failed for organization {}", organization_id
            )
            counters["failed"] += 1

    logger.info(
        "Weekly digest: {considered} considered, {sent} sent, {skipped} skipped, "
        "{failed} failed",
        **counters,
    )
    return counters
