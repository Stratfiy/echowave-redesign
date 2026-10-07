"""Product analytics from Decibyl's own records (handoff 37; screen 36).

Activation analytics did not exist before this module. The definitions are
the handoff's, computed from the application database -- which owns tasks,
approvals and feedback -- rather than from PostHog, which is linked for
deeper analysis but is not the authority on a business outcome.

**A useful outcome** (one row of ``useful_outcomes``) is either:

* a task on the ledger that completed (``ledger_state = completed``, which
  the ledger only allows with evidence), or a board task a person marked
  done (``status = done`` on a row the ledger never touched); credited to
  the person who created it; or
* an approval card that ran (``state = done``); credited to the person who
  confirmed it.

A chat reply on its own is not a useful outcome (handoff: "not merely a
chat response"). Times are when the outcome happened (``finished_at``, the
card's confirmation), never when a row was read, so a late event cannot
move a person between cohorts.

Every figure carries its sample size. A ratio with no denominator is
``None`` with the reason, never zero.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.db.controls_models import OutputFeedbackModel
from api.db.models import AgentEventModel, AgentTaskModel, UserModel
from api.db.shell_models import UserOnboardingModel, WaitlistRequestModel
from api.db.signup_invite_models import SignupInviteRedemptionModel
from api.enums import AgentEventActor, AgentEventKind

#: Card states, as ``services/workflow/actions.py`` writes them.
CARD_DONE = "done"
CARD_FAILED = "failed"
CARD_UNKNOWN = "outcome_unknown"
CARD_EXCLUDED = ("declined", "undone", "cancelled")

DEFINITIONS: dict[str, str] = {
    "useful_outcome": (
        "A completed ledger task (completion needs evidence), a board task a "
        "person marked done, or an approval card that ran. A chat reply alone "
        "does not count."
    ),
    "activation": (
        "A person's first useful outcome after signing up. Counted for people "
        "who signed up in the period."
    ),
    "weekly_useful_users": (
        "Distinct people with at least one useful outcome in the seven days "
        "ending at the period's end."
    ),
    "seven_day_repeat": (
        "Activated people with another useful outcome in days 1-7 after "
        "activation, divided by activated people whose seven days have fully "
        "passed. People activated in the last seven days are not counted yet."
    ),
    "task_success": (
        "Completed / (completed + failed + outcome unknown), over tasks and "
        "cards that finished in the period. Cancelled, declined and undone "
        "ones are excluded (a person chose to stop) and shown separately."
    ),
    "usefulness": (
        "Yes answers / answered prompts. Exposure is the number of Decibyl "
        "replies in the period: an upper bound on prompts shown, because the "
        "prompt appears only where reply_feedback is on."
    ),
}


@dataclass(frozen=True)
class Outcome:
    user_id: int
    at: datetime
    source: str  # "task" | "card"
    kind: str


def _parse(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _task_success_clause():
    return or_(
        AgentTaskModel.ledger_state == "completed",
        and_(AgentTaskModel.ledger_state.is_(None), AgentTaskModel.status == "done"),
    )


def _task_kind(row) -> str:
    return "agent_task" if row.assignee_workflow_id else "person_task"


async def useful_outcomes(
    session: AsyncSession,
    *,
    since: datetime | None = None,
    until: datetime | None = None,
    user_ids: list[int] | None = None,
) -> list[Outcome]:
    out: list[Outcome] = []
    task_q = select(
        AgentTaskModel.created_by,
        AgentTaskModel.finished_at,
        AgentTaskModel.assignee_workflow_id,
    ).where(
        _task_success_clause(),
        AgentTaskModel.created_by.isnot(None),
        AgentTaskModel.finished_at.isnot(None),
    )
    if since:
        task_q = task_q.where(AgentTaskModel.finished_at >= since)
    if until:
        task_q = task_q.where(AgentTaskModel.finished_at < until)
    if user_ids is not None:
        task_q = task_q.where(AgentTaskModel.created_by.in_(user_ids))
    for row in (await session.execute(task_q)).all():
        out.append(Outcome(row.created_by, row.finished_at, "task", _task_kind(row)))

    card_q = select(AgentEventModel.payload, AgentEventModel.at).where(
        AgentEventModel.kind == AgentEventKind.ACTION_PROPOSED.value,
        AgentEventModel.payload["state"].as_string() == CARD_DONE,
    )
    if since:
        # A card is confirmed after it is proposed, so its row time bounds it.
        card_q = card_q.where(AgentEventModel.at >= since - timedelta(days=2))
    for payload, created in (await session.execute(card_q)).all():
        confirmed = (payload or {}).get("confirmed") or {}
        by = confirmed.get("by")
        at = _parse(confirmed.get("at")) or created
        if not isinstance(by, int) or at is None:
            continue
        if since and at < since or until and at >= until:
            continue
        if user_ids is not None and by not in user_ids:
            continue
        out.append(
            Outcome(by, at, "card", str((payload or {}).get("action") or "other"))
        )
    out.sort(key=lambda o: o.at)
    return out


def _ratio(num: int, den: int) -> float | None:
    return round(num / den, 4) if den else None


async def _first_outcomes(session: AsyncSession) -> dict[int, datetime]:
    first: dict[int, datetime] = {}
    for o in await useful_outcomes(session):
        if o.user_id not in first:
            first[o.user_id] = o.at
    return first


async def last_useful_outcome(
    session: AsyncSession, user_ids: list[int]
) -> dict[int, str]:
    last: dict[int, datetime] = {}
    for o in await useful_outcomes(session, user_ids=user_ids):
        last[o.user_id] = o.at
    return {uid: at.isoformat() for uid, at in last.items()}


async def report(
    session: AsyncSession, *, days: int, now: datetime | None = None
) -> dict:
    now = now or datetime.now(UTC)
    start = now - timedelta(days=days)
    all_outcomes = await useful_outcomes(session)
    first: dict[int, datetime] = {}
    by_user: dict[int, list[datetime]] = defaultdict(list)
    for o in all_outcomes:
        first.setdefault(o.user_id, o.at)
        by_user[o.user_id].append(o.at)

    # --- Funnel: acquisition, activity, verified outcome, kept distinct.
    waitlisted = await session.scalar(
        select(func.count(WaitlistRequestModel.id)).where(
            WaitlistRequestModel.created_at >= start,
            WaitlistRequestModel.created_at < now,
        )
    )
    redeemed = await session.scalar(
        select(func.count(SignupInviteRedemptionModel.id)).where(
            SignupInviteRedemptionModel.redeemed_at >= start,
            SignupInviteRedemptionModel.redeemed_at < now,
        )
    )
    signups = (
        await session.execute(
            select(UserModel.id, UserModel.created_at).where(
                UserModel.created_at >= start,
                UserModel.created_at < now,
                UserModel.staff_role.is_(None),
            )
        )
    ).all()
    signup_ids = [r.id for r in signups]
    onboarded = 0
    if signup_ids:
        onboarded = await session.scalar(
            select(func.count(UserOnboardingModel.user_id)).where(
                UserOnboardingModel.user_id.in_(signup_ids),
                UserOnboardingModel.completed_at.isnot(None),
            )
        )
    activated = [uid for uid in signup_ids if uid in first]
    funnel = [
        {
            "step": "waitlist_joined",
            "stage": "acquisition",
            "count": int(waitlisted or 0),
        },
        {
            "step": "invite_redeemed",
            "stage": "acquisition",
            "count": int(redeemed or 0),
        },
        {"step": "signed_up", "stage": "activity", "count": len(signup_ids)},
        {
            "step": "onboarding_completed",
            "stage": "activity",
            "count": int(onboarded or 0),
        },
        {"step": "activated", "stage": "outcome", "count": len(activated)},
    ]

    # --- Weekly useful users.
    week_start = now - timedelta(days=7)
    wau = len({o.user_id for o in all_outcomes if week_start <= o.at < now})

    # --- Seven-day repeat over people activated in the period whose window
    # has fully passed.
    mature, repeated, immature = 0, 0, 0
    for uid, at in first.items():
        if not (start <= at < now):
            continue
        if at + timedelta(days=7) > now:
            immature += 1
            continue
        mature += 1
        if any(at < t <= at + timedelta(days=7) for t in by_user[uid][1:]):
            repeated += 1

    # --- Retention: weekly activation cohorts, last six weeks.
    cohorts = []
    for week in range(6, 0, -1):
        c_start = now - timedelta(days=7 * week)
        c_end = c_start + timedelta(days=7)
        members = [u for u, at in first.items() if c_start <= at < c_end]
        matured = c_end + timedelta(days=7) <= now
        returned = sum(
            1
            for u in members
            if any(first[u] < t <= first[u] + timedelta(days=7) for t in by_user[u][1:])
        )
        cohorts.append(
            {
                "week_start": c_start.date().isoformat(),
                "activated": len(members),
                "returned_in_7_days": returned if matured else None,
                "rate": _ratio(returned, len(members)) if matured else None,
                "state": "mature" if matured else "insufficient_window",
            }
        )

    success = await task_success(session, start=start, end=now)
    useful = await usefulness(session, start=start, end=now)
    return {
        "period": {"start": start.isoformat(), "end": now.isoformat(), "days": days},
        "definitions": DEFINITIONS,
        "funnel": funnel,
        "weekly_useful_users": {"value": wau, "window_days": 7},
        "seven_day_repeat": {
            "value": _ratio(repeated, mature),
            "numerator": repeated,
            "denominator": mature,
            "not_yet_eligible": immature,
            "state": "ok" if mature else "insufficient_window",
        },
        "retention": cohorts,
        "task_success": success,
        "usefulness": useful,
    }


async def task_success(
    session: AsyncSession, *, start: datetime, end: datetime
) -> dict:
    """Successful / eligible attempts by task type, with the excluded and
    unknown counts beside, never folded in."""
    rows: dict[str, dict[str, int]] = defaultdict(
        lambda: {"completed": 0, "failed": 0, "unknown": 0, "excluded": 0}
    )
    tasks = (
        await session.execute(
            select(
                AgentTaskModel.ledger_state,
                AgentTaskModel.status,
                AgentTaskModel.assignee_workflow_id,
            ).where(
                AgentTaskModel.finished_at >= start, AgentTaskModel.finished_at < end
            )
        )
    ).all()
    for t in tasks:
        bucket = rows[_task_kind(t)]
        state = t.ledger_state or {"done": "completed", "could_not": "failed"}.get(
            t.status
        )
        if state == "completed":
            bucket["completed"] += 1
        elif state == "failed":
            bucket["failed"] += 1
        elif state == "outcome_unknown":
            bucket["unknown"] += 1
        elif state == "cancelled":
            bucket["excluded"] += 1
    cards = (
        (
            await session.execute(
                select(AgentEventModel.payload).where(
                    AgentEventModel.kind == AgentEventKind.ACTION_PROPOSED.value,
                    AgentEventModel.at >= start,
                    AgentEventModel.at < end,
                )
            )
        )
        .scalars()
        .all()
    )
    for payload in cards:
        payload = payload or {}
        state = payload.get("state")
        bucket = rows[f"card:{payload.get('action') or 'other'}"]
        if state == CARD_DONE:
            bucket["completed"] += 1
        elif state == CARD_FAILED:
            bucket["failed"] += 1
        elif state == CARD_UNKNOWN:
            bucket["unknown"] += 1
        elif state in CARD_EXCLUDED:
            bucket["excluded"] += 1
    breakdown = []
    totals = {"completed": 0, "failed": 0, "unknown": 0, "excluded": 0}
    for kind, b in sorted(rows.items()):
        eligible = b["completed"] + b["failed"] + b["unknown"]
        breakdown.append(
            {
                "type": kind,
                **b,
                "eligible": eligible,
                "rate": _ratio(b["completed"], eligible),
            }
        )
        for k in totals:
            totals[k] += b[k]
    eligible = totals["completed"] + totals["failed"] + totals["unknown"]
    return {
        **totals,
        "eligible": eligible,
        "rate": _ratio(totals["completed"], eligible),
        "breakdown": breakdown,
        "cancellation_policy": "Cancelled, declined and undone are excluded from the denominator.",
    }


async def usefulness(session: AsyncSession, *, start: datetime, end: datetime) -> dict:
    verdicts = dict(
        (
            await session.execute(
                select(OutputFeedbackModel.verdict, func.count(OutputFeedbackModel.id))
                .where(
                    OutputFeedbackModel.created_at >= start,
                    OutputFeedbackModel.created_at < end,
                )
                .group_by(OutputFeedbackModel.verdict)
            )
        ).all()
    )
    answered = sum(verdicts.values())
    yes = int(verdicts.get("yes", 0))
    exposure = await session.scalar(
        select(func.count(AgentEventModel.id)).where(
            AgentEventModel.kind == AgentEventKind.MESSAGE.value,
            AgentEventModel.actor == AgentEventActor.AGENT.value,
            AgentEventModel.at >= start,
            AgentEventModel.at < end,
        )
    )
    exposure = int(exposure or 0)
    return {
        "answered": answered,
        "yes": yes,
        "not_quite": int(verdicts.get("not_quite", 0)),
        "rate": _ratio(yes, answered),
        "exposure_upper_bound": exposure,
        "response_rate": _ratio(answered, exposure),
    }


async def records(
    session: AsyncSession, *, days: int, limit: int = 100, now: datetime | None = None
) -> dict:
    """The drill-down behind the aggregates: pseudonymous people with their
    outcome counts. No email, name or content; ids are keyed pseudonyms, so
    a record here cannot be looked up by reading it."""
    from api.services.events.envelope import EventRefused, pseudonym

    now = now or datetime.now(UTC)
    start = now - timedelta(days=days)
    counts: dict[int, dict[str, Any]] = {}
    for o in await useful_outcomes(session, since=start, until=now):
        row = counts.setdefault(
            o.user_id, {"outcomes": 0, "last_at": o.at, "kinds": set()}
        )
        row["outcomes"] += 1
        row["last_at"] = max(row["last_at"], o.at)
        row["kinds"].add(o.kind)
    try:
        rows = [
            {
                "person": pseudonym("u", uid),
                "outcomes": r["outcomes"],
                "last_at": r["last_at"].isoformat(),
                "kinds": sorted(r["kinds"]),
            }
            for uid, r in sorted(counts.items(), key=lambda kv: -kv[1]["outcomes"])[
                :limit
            ]
        ]
    except EventRefused:
        return {
            "state": "needs_setup",
            "reason": "ANALYTICS_PSEUDONYM_KEY is not set.",
            "records": [],
        }
    return {"state": "ok", "records": rows}
