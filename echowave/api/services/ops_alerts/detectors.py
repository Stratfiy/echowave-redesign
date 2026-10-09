"""The detectors. Each looks at one kind of trouble and says what is wrong now.

A detector returns an ``Evaluation``: zero or more findings, each under a
stable key, and whether it could measure at all. It never mails, never
writes incident state and never decides cooldowns -- ``incidents.apply``
does that, the same way for all of them. Every threshold is read from
``thresholds``.

What each one reads:

* ``calls`` -- carrier calls placed in the last 15 minutes, classified by
  ``telephony.call_evidence`` (#575). Fires on the share that failed at the
  carrier or finished with no outcome.
* ``providers`` -- the per-minute counters ``signals`` keeps at the error
  handling points: pipeline error frames by component, builder-client model
  turns, outbound dial requests.
* ``jobs`` -- the worker heartbeat (``services/worker_health``), then, only
  while the worker is alive, each watched tick's last completion and the
  oldest due job in the ARQ queue. With the worker down those would all go
  stale at once; that is one incident, not five.
* ``spend`` -- today's metered provider cost (IST day so far) against the
  average day of the seven before, for the platform and each workspace.
* ``invites`` -- invite requests pending longer than twelve hours.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.services.ops_alerts import calls, metering, signals, store
from api.services.ops_alerts import thresholds as t
from api.services.ops_alerts.incidents import CRITICAL, WARNING, Evaluation, Finding


def rupees(paise: float) -> str:
    return f"Rs {paise / 100:,.2f}"


def _pct(share: float) -> str:
    return f"{share * 100:.0f}%"


# --- calls ---------------------------------------------------------------------


async def detect_calls(session: AsyncSession, *, now: datetime) -> Evaluation:
    start = now - timedelta(minutes=t.CALL_WINDOW_MINUTES)
    o = await calls.outcomes(session, start, now)
    metrics = {**o.as_dict(), "share": round(o.bad_share, 3)}
    if o.finished < t.CALL_FAILURE_MIN_CALLS or o.bad_share < t.CALL_FAILURE_SHARE:
        return Evaluation("calls")
    worst = sorted((o.bad_by_org or {}).items(), key=lambda kv: -kv[1])[:3]
    org_names, _ = await metering.names(
        session, orgs=[org for org, _ in worst if org is not None], agents=[]
    )
    lines = [
        f"In the last {t.CALL_WINDOW_MINUTES} minutes, {o.bad} of {o.finished} "
        f"finished carrier calls ({_pct(o.bad_share)}) failed at the carrier or "
        f"ended with no outcome. The alert fires at {_pct(t.CALL_FAILURE_SHARE)} "
        f"of at least {t.CALL_FAILURE_MIN_CALLS} calls.",
        "",
        f"  answered {o.answered}, not picked up {o.not_connected}, "
        f"failed at the carrier {o.carrier_failed}, no outcome {o.unknown}, "
        f"still in progress {o.in_progress}",
    ]
    if worst:
        lines += ["", "Most affected workspaces:"]
        lines += [
            f"  {org_names.get(org, f'Workspace {org}')} (id {org}): {count}"
            for org, count in worst
        ]
    return Evaluation(
        "calls",
        [
            Finding(
                key="calls:failure_spike",
                title=f"Call failures at {_pct(o.bad_share)} of the last "
                f"{o.finished} calls",
                detail="\n".join(lines),
                severity=CRITICAL,
                metrics=metrics,
            )
        ],
    )


# --- providers -----------------------------------------------------------------

_COMPONENT_NAMES = {
    "stt": "Speech to text",
    "tts": "Text to speech",
    "llm": "Language model",
    "telephony": "Telephony transport",
    "other": "Unclassified pipeline",
}


def provider_series(counts: dict[str, int]) -> list[dict[str, Any]]:
    """The error series in a window's counters. Pure.

    Built from the counters present, not from a fixed list: a component
    nobody anticipated still becomes a series (``api/AGENTS.md``,
    "Silent Absence").
    """
    out: list[dict[str, Any]] = []
    calls_started = int(counts.get("call:n", 0))
    for name, errors in sorted(counts.items()):
        parts = name.split(":")
        if len(parts) == 3 and parts[0] == "call" and parts[2] == "err":
            component = parts[1]
            out.append(
                {
                    "key": f"provider:{component}:calls",
                    "label": f"{_COMPONENT_NAMES.get(component, component)} errors on calls",
                    "errors": int(errors),
                    "attempts": max(calls_started, int(errors)),
                    "of": "calls started",
                }
            )
    for prefix, key, label, of in (
        (
            "llm_direct",
            "provider:llm:direct",
            "Language model errors outside calls",
            "model turns",
        ),
        (
            "dial",
            "provider:telephony:dial",
            "Carrier refused outbound dials",
            "dial requests",
        ),
    ):
        errors = int(counts.get(f"{prefix}:err", 0))
        attempts = max(int(counts.get(f"{prefix}:n", 0)), errors)
        if attempts:
            out.append(
                {
                    "key": key,
                    "label": label,
                    "errors": errors,
                    "attempts": attempts,
                    "of": of,
                }
            )
    return out


async def detect_providers(*, now: datetime, client=None) -> Evaluation:
    counts = await signals.window_counts(
        t.PROVIDER_WINDOW_MINUTES, now=now, client=client
    )
    findings = []
    for series in provider_series(counts):
        errors, attempts = series["errors"], series["attempts"]
        share = errors / attempts if attempts else 0.0
        if errors < t.PROVIDER_ERROR_MIN_COUNT or share < t.PROVIDER_ERROR_SHARE:
            continue
        findings.append(
            Finding(
                key=series["key"],
                title=f"{series['label']}: {errors} in {t.PROVIDER_WINDOW_MINUTES} minutes",
                detail=(
                    f"{errors} errors against {attempts} {series['of']} in the last "
                    f"{t.PROVIDER_WINDOW_MINUTES} minutes ({_pct(share)}). The alert "
                    f"fires at {_pct(t.PROVIDER_ERROR_SHARE)} with at least "
                    f"{t.PROVIDER_ERROR_MIN_COUNT} errors.\n\n"
                    "Which vendor: the run's pipeline error on the call timeline, or "
                    "the provider keys screen for a key or balance problem."
                ),
                severity=WARNING,
                metrics={
                    "errors": errors,
                    "attempts": attempts,
                    "share": round(share, 3),
                },
            )
        )
    return Evaluation("providers", findings)


# --- background jobs -----------------------------------------------------------


async def _monitoring_since(now: datetime, client=None) -> datetime:
    """When this run of monitoring started watching. Expires when nothing has
    evaluated for a while, so switching the flag back on starts a new grace."""
    name = store.key("monitoring_since")
    ttl = (t.TICK_NEVER_SEEN_GRACE_MINUTES + 10) * 60
    async with store.connection(client) as redis:
        await redis.set(name, now.isoformat(), nx=True, ex=ttl)
        await redis.expire(name, ttl)
        raw = store.text(await redis.get(name))
    return datetime.fromisoformat(raw) if raw else now


async def _queue_oldest_due_seconds() -> float | None:
    from api.services.ops import infra_health

    signal = await infra_health._queue()
    return signal.metrics.get("oldest_due_age_seconds")


async def detect_jobs(*, now: datetime, client=None) -> list[Evaluation]:
    from api.services.worker_health import worker_health

    since = await _monitoring_since(now, client)
    watching_minutes = (now - since).total_seconds() / 60
    health = await worker_health(now=now)
    alive = health.get("alive")

    worker = Evaluation("jobs.worker")
    if alive is False or (
        alive is None
        and health.get("detail", "").startswith("No heartbeat on record")
        and watching_minutes >= t.TICK_NEVER_SEEN_GRACE_MINUTES
    ):
        worker.findings.append(
            Finding(
                key="jobs:worker",
                title="Background worker is not running",
                detail=(
                    f"{health.get('detail')}\n\nLast heartbeat: "
                    f"{health.get('last_seen') or 'none on record'}. Calls are not "
                    "being costed, routines and reminders are not firing, and the "
                    "API keeps answering normally."
                ),
                severity=CRITICAL,
                metrics={"age_seconds": health.get("age_seconds")},
            )
        )
    elif alive is None:
        worker.ok = False
        worker.note = str(health.get("detail") or "")

    ticks = Evaluation("jobs.ticks")
    if alive is not True:
        # Every tick goes stale when the worker stops; that is the worker's
        # incident. Tick incidents are left as they were, not resolved.
        ticks.ok = False
        ticks.note = "Worker not alive; scheduled ticks not judged."
        return [worker, ticks]

    last = await signals.last_ticks(client)
    for name, label in signals.TICKS.items():
        stamp = last.get(name)
        if stamp is None:
            if watching_minutes < t.TICK_NEVER_SEEN_GRACE_MINUTES:
                continue
            detail = (
                f"{label} ({name}) has not completed once in the "
                f"{int(watching_minutes)} minutes monitoring has been watching, "
                "while the worker is alive. It is raising every run, or it is not "
                "scheduled."
            )
        else:
            age = (now - stamp).total_seconds() / 60
            if age <= t.TICK_STALE_MINUTES:
                continue
            detail = (
                f"{label} ({name}) last completed {int(age)} minutes ago; it runs "
                f"every minute. The worker is alive, so the tick itself is failing "
                f"or stuck. Alert after {t.TICK_STALE_MINUTES} minutes."
            )
        ticks.findings.append(
            Finding(
                key=f"jobs:tick:{name}",
                title=f"{label} are not running",
                detail=detail,
                severity=CRITICAL,
                metrics={"last_completed": stamp.isoformat() if stamp else None},
            )
        )

    oldest = await _queue_oldest_due_seconds()
    if oldest is not None and oldest > t.QUEUE_OLDEST_DUE_MINUTES * 60:
        ticks.findings.append(
            Finding(
                key="jobs:queue",
                title=f"Background jobs waiting {int(oldest // 60)} minutes",
                detail=(
                    f"The oldest due job in the queue has waited {int(oldest // 60)} "
                    "minutes for a worker. The worker is alive but behind: post-call "
                    "costing, webhooks and routine runs are late. Alert after "
                    f"{t.QUEUE_OLDEST_DUE_MINUTES} minutes."
                ),
                severity=WARNING,
                metrics={"oldest_due_age_seconds": oldest},
            )
        )
    return [worker, ticks]


# --- spend ---------------------------------------------------------------------


async def baseline(session: AsyncSession, *, today, client=None) -> dict[str, Any]:
    """Average provider cost per day over the ``SPEND_BASELINE_DAYS`` before
    ``today``, for the platform and per workspace. Computed once a day."""
    name = store.key("spend_baseline", today.isoformat())
    async with store.connection(client) as redis:
        cached = store.text(await redis.get(name))
        if cached:
            data = json.loads(cached)
            return {
                "platform": data["platform"],
                "orgs": {int(k): v for k, v in data["orgs"].items()},
            }
        start, _ = metering.day_window(today - timedelta(days=t.SPEND_BASELINE_DAYS))
        end, _ = metering.day_window(today)
        window = await metering.meter(session, start, end)
        data = {
            "platform": window.total_paise / t.SPEND_BASELINE_DAYS,
            "orgs": {
                org: paise / t.SPEND_BASELINE_DAYS
                for org, paise in window.by_org.items()
                if org is not None
            },
        }
        await redis.set(name, json.dumps(data), ex=2 * 86_400)
    return data


def is_anomalous(today_paise: float, average_paise: float, minimum: int) -> bool:
    """At least the floor, and at least the multiple of an ordinary day. Pure."""
    return (
        today_paise >= minimum
        and today_paise >= t.SPEND_ANOMALY_MULTIPLE * average_paise
    )


async def detect_spend(
    session: AsyncSession, *, now: datetime, client=None
) -> Evaluation:
    today = metering.ist_day(now)
    start, _ = metering.day_window(today)
    current = await metering.meter(session, start, now)
    base = await baseline(session, today=today, client=client)
    findings = []

    def _finding(key: str, who: str, spent: float, avg: float, floor: int) -> Finding:
        multiple = f"{spent / avg:.1f}x" if avg else "no spend on record before today"
        return Finding(
            key=key,
            title=f"Spend anomaly: {who} at {rupees(spent)} today",
            detail=(
                f"{who} has used {rupees(spent)} of metered provider cost so far today "
                f"(IST), against an average day of {rupees(avg)} over the previous "
                f"{t.SPEND_BASELINE_DAYS} days ({multiple}). The alert fires at "
                f"{t.SPEND_ANOMALY_MULTIPLE:g}x the average and at least {rupees(floor)}.\n\n"
                "These are provider costs as billing metered them, not customer prices."
            ),
            severity=WARNING,
            metrics={"today_paise": int(spent), "average_paise": int(avg)},
        )

    total = current.total_paise
    if is_anomalous(total, base["platform"], t.SPEND_ANOMALY_MIN_PLATFORM_PAISE):
        findings.append(
            _finding(
                "spend:platform",
                "The platform",
                total,
                base["platform"],
                t.SPEND_ANOMALY_MIN_PLATFORM_PAISE,
            )
        )
    hot = [
        (org, spent)
        for org, spent in current.by_org.items()
        if org is not None
        and is_anomalous(
            spent, base["orgs"].get(org, 0.0), t.SPEND_ANOMALY_MIN_ORG_PAISE
        )
    ]
    if hot:
        org_names, _ = await metering.names(
            session, orgs=[org for org, _ in hot], agents=[]
        )
        for org, spent in hot:
            who = f"{org_names.get(org, f'Workspace {org}')} (workspace {org})"
            findings.append(
                _finding(
                    f"spend:org:{org}",
                    who,
                    spent,
                    base["orgs"].get(org, 0.0),
                    t.SPEND_ANOMALY_MIN_ORG_PAISE,
                )
            )
    return Evaluation("spend", findings)


# --- invite requests -------------------------------------------------------------


async def detect_invites(session: AsyncSession, *, now: datetime) -> Evaluation:
    from api.db.shell_models import WaitlistRequestModel
    from api.services.auth.invite_requests import PENDING

    cutoff = now - timedelta(hours=t.INVITE_WAIT_HOURS)
    count, oldest = (
        await session.execute(
            select(
                func.count(WaitlistRequestModel.id),
                func.min(WaitlistRequestModel.created_at),
            ).where(
                WaitlistRequestModel.status == PENDING,
                WaitlistRequestModel.created_at < cutoff,
            )
        )
    ).one()
    if not count:
        return Evaluation("invites")
    hours = (
        int((now - oldest).total_seconds() // 3600) if oldest else t.INVITE_WAIT_HOURS
    )
    from api import constants

    base = (constants.UI_APP_URL or "").rstrip("/")
    return Evaluation(
        "invites",
        [
            Finding(
                key="invites:waiting",
                title=f"{count} invite request{'s' if count != 1 else ''} waiting over "
                f"{t.INVITE_WAIT_HOURS} hours",
                detail=(
                    f"{count} invite request{'s have' if count != 1 else ' has'} been "
                    f"pending for more than {t.INVITE_WAIT_HOURS} hours; the oldest for "
                    f"{hours} hours. Each person is waiting on an Approve or Reject.\n\n"
                    f"  {base}/superadmin/invites"
                ),
                severity=WARNING,
                metrics={"count": int(count), "oldest_hours": hours},
            )
        ],
    )
