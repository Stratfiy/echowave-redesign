"""From "a detector says something is wrong" to at most a few emails.

A detector returns its findings each time it runs: one per thing that is
wrong right now, each under a stable ``key`` (``calls:failure_spike``,
``spend:org:12``). This module turns that stream into incidents:

* **Opened** -- a key appears that is not open. One mail.
* **Still happening** -- an open key keeps appearing. Nothing, until
  ``ALERT_COOLDOWN_MINUTES`` after the last mail for it; then one reminder.
* **Resolved** -- an open key has been absent for
  ``RESOLVE_AFTER_CLEAR_EVALUATIONS`` runs in a row. One mail, if the
  incident was ever mailed.

**One mail per key per cooldown.** Sending claims a Redis key with the
cooldown as its expiry (``SET NX EX``) before the mail goes out, so two
processes, a flapping condition or a restarted worker cannot send the same
alert twice inside the window. A key that reopens inside its cooldown is open
on the staff page without a second mail; a resolved mail is under its own
claim, so flapping costs at most two mails a window, not one per flap.

**A detector that could not measure resolves nothing.** ``Evaluation.ok``
false (the query failed, the worker is down so its ticks cannot be judged)
leaves that detector's incidents exactly as they were. Unknown is not
healthy.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from api import constants
from api.services.ops_alerts import notify, store
from api.services.ops_alerts.thresholds import (
    ALERT_COOLDOWN_MINUTES,
    HISTORY_LENGTH,
    RESOLVE_AFTER_CLEAR_EVALUATIONS,
)

IST = ZoneInfo("Asia/Kolkata")
CRITICAL = "critical"
WARNING = "warning"


@dataclass
class Finding:
    key: str
    title: str
    detail: str
    severity: str = WARNING
    metrics: dict[str, Any] = field(default_factory=dict)


@dataclass
class Evaluation:
    detector: str
    findings: list[Finding] = field(default_factory=list)
    #: False when the detector could not measure. Its incidents stay as
    #: they are; nothing resolves on an absence of evidence.
    ok: bool = True
    note: str | None = None


def when(at: datetime | str | None) -> str:
    """A time as an operator reads it, in IST. No digit run a phone mask
    would mistake for a number."""
    if at is None:
        return "unknown"
    if isinstance(at, str):
        at = datetime.fromisoformat(at)
    return at.astimezone(IST).strftime("%a %d %b, %H:%M IST")


def _page() -> str:
    base = (constants.UI_APP_URL or "").rstrip("/")
    return f"{base}/superadmin/operations?tab=alerts"


def _body(incident: dict[str, Any], *, lead: str) -> str:
    return (
        f"{lead}\n\n{incident['detail']}\n\n"
        f"Opened: {when(incident['opened_at'])}\n"
        f"Open alerts: {_page()}\n\n"
        "Internal operations mail. Thresholds are in "
        "api/services/ops_alerts/thresholds.py.\n"
    )


def compose_open(incident: dict[str, Any]) -> tuple[str, str]:
    tag = (
        "[Decibyl ops]" if incident["severity"] != CRITICAL else "[Decibyl ops, urgent]"
    )
    return f"{tag} {incident['title']}", _body(incident, lead=incident["title"] + ".")


def compose_reminder(incident: dict[str, Any], now: datetime) -> tuple[str, str]:
    opened = datetime.fromisoformat(incident["opened_at"])
    minutes = int((now - opened).total_seconds() // 60)
    return (
        f"[Decibyl ops] Still happening: {incident['title']}",
        _body(
            incident,
            lead=f"{incident['title']}, still true {minutes} minutes after it opened.",
        ),
    )


def compose_resolved(incident: dict[str, Any], now: datetime) -> tuple[str, str]:
    opened = datetime.fromisoformat(incident["opened_at"])
    minutes = int((now - opened).total_seconds() // 60)
    return (
        f"[Decibyl ops] Resolved: {incident['title']}",
        (
            f"Resolved after {minutes} minutes: {incident['title']}.\n\n"
            f"Last reading while open:\n{incident['detail']}\n\n"
            f"Opened: {when(incident['opened_at'])}\n"
            f"Resolved: {when(now)}\n"
        ),
    )


#: Claim ``KEYS[1]`` unless it was claimed less than ``ARGV[2]`` seconds
#: before ``ARGV[1]`` (both epoch seconds). Atomic, and judged against the
#: caller's clock as well as the key's expiry, so the cooldown means the same
#: thing to every process.
_CLAIM = """
local held = redis.call('GET', KEYS[1])
if held and tonumber(held) > tonumber(ARGV[1]) - tonumber(ARGV[2]) then
  return 0
end
redis.call('SET', KEYS[1], ARGV[1], 'EX', ARGV[2])
return 1
"""


async def _claim(redis, name: str, now: datetime) -> bool:
    return bool(
        await redis.eval(
            _CLAIM,
            1,
            store.key(name),
            int(now.timestamp()),
            ALERT_COOLDOWN_MINUTES * 60,
        )
    )


async def _claimed_at(redis, name: str) -> datetime | None:
    raw = store.text(await redis.get(store.key(name)))
    return datetime.fromtimestamp(int(raw), UTC) if raw else None


async def _mail(redis, claim: str, subject: str, body: str, now: datetime) -> bool:
    """Claim, then send. A send that fails gives the claim back so the next
    evaluation tries again, rather than a window of silence."""
    if not await _claim(redis, claim, now):
        return False
    sent = await notify.send(subject, body)
    if not sent:
        await redis.delete(store.key(claim))
    return sent


async def open_incidents(client=None) -> list[dict[str, Any]]:
    async with store.connection(client) as redis:
        raw = await redis.hgetall(store.key("incidents"))
    rows = [json.loads(store.text(v)) for v in (raw or {}).values()]
    return sorted(rows, key=lambda r: r["opened_at"], reverse=True)


async def history(limit: int = 20, client=None) -> list[dict[str, Any]]:
    async with store.connection(client) as redis:
        raw = await redis.lrange(store.key("history"), 0, max(limit - 1, 0))
    return [json.loads(store.text(v)) for v in raw or []]


async def apply(
    evaluation: Evaluation, *, now: datetime | None = None, client=None
) -> list[dict[str, Any]]:
    """Fold one evaluation into the open incidents. Returns what happened,
    one entry per action: ``{"key", "action", "mailed"}``."""
    now = now or datetime.now(UTC)
    actions: list[dict[str, Any]] = []
    table = store.key("incidents")
    async with store.connection(client) as redis:
        current = {
            store.text(k): json.loads(store.text(v))
            for k, v in ((await redis.hgetall(table)) or {}).items()
        }
        seen: set[str] = set()
        for finding in evaluation.findings:
            seen.add(finding.key)
            incident = current.get(finding.key)
            if incident is None:
                incident = {
                    "key": finding.key,
                    "detector": evaluation.detector,
                    "opened_at": now.isoformat(),
                    "last_notified_at": None,
                    "notified": False,
                }
                fresh = True
            else:
                fresh = False
            incident.update(
                title=finding.title,
                detail=finding.detail,
                severity=finding.severity,
                metrics=finding.metrics,
                last_seen_at=now.isoformat(),
                clear_count=0,
            )
            mailed = False
            if fresh:
                subject, body = compose_open(incident)
                mailed = await _mail(redis, f"notify:{finding.key}", subject, body, now)
                if not mailed:
                    # Reopened inside its cooldown: the operator was told
                    # about this key minutes ago. Remember when, so the
                    # reminder and the resolved mail still follow.
                    claimed = await _claimed_at(redis, f"notify:{finding.key}")
                    if claimed is not None:
                        incident["notified"] = True
                        incident["last_notified_at"] = claimed.isoformat()
                actions.append(
                    {"key": finding.key, "action": "opened", "mailed": mailed}
                )
            else:
                last = incident.get("last_notified_at")
                due = last is None or now - datetime.fromisoformat(last) >= timedelta(
                    minutes=ALERT_COOLDOWN_MINUTES
                )
                if due:
                    subject, body = compose_reminder(incident, now)
                    mailed = await _mail(
                        redis, f"notify:{finding.key}", subject, body, now
                    )
                    if mailed:
                        actions.append(
                            {"key": finding.key, "action": "reminded", "mailed": True}
                        )
            if mailed:
                incident["notified"] = True
                incident["last_notified_at"] = now.isoformat()
            await redis.hset(table, finding.key, json.dumps(incident))

        if not evaluation.ok:
            return actions
        for key, incident in current.items():
            if incident.get("detector") != evaluation.detector or key in seen:
                continue
            incident["clear_count"] = int(incident.get("clear_count") or 0) + 1
            if incident["clear_count"] < RESOLVE_AFTER_CLEAR_EVALUATIONS:
                await redis.hset(table, key, json.dumps(incident))
                continue
            await redis.hdel(table, key)
            incident["resolved_at"] = now.isoformat()
            await redis.lpush(store.key("history"), json.dumps(incident))
            await redis.ltrim(store.key("history"), 0, HISTORY_LENGTH - 1)
            mailed = False
            if incident.get("notified"):
                subject, body = compose_resolved(incident, now)
                mailed = await _mail(redis, f"resolved:{key}", subject, body, now)
            actions.append({"key": key, "action": "resolved", "mailed": mailed})
    return actions


def as_public(incident: dict[str, Any]) -> dict[str, Any]:
    """An incident for the staff page: the stored fields, detail scrubbed."""
    out = dict(incident)
    out["detail"] = notify.scrub(str(out.get("detail") or ""))
    out["title"] = notify.scrub(str(out.get("title") or ""))
    return out
