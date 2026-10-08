"""Operations, delivery, task trace and voice latency (screens 39-40).

Jobs, delivery and infrastructure are read from Decibyl's own records and
the existing system probes; actions on them (pause, retry, drain) are the
ops stream's typed commands and are not repeated here. Monitoring that did
not answer is ``unknown``, never healthy.

The trace of one task shows its ledger transitions, its run's stage timings
and the approval card's state -- states, codes and times, no titles or
messages. A stage whose timestamp is missing is reported missing, never
drawn; durations are only computed between timestamps from the same clock
(one pipeline's monotonic offsets), never across machines.

Voice latency reuses the billing dashboard's turn metrics. ``perceived`` is
user-stopped-speaking to first audio out, measured in the server's pipeline;
the handoff's target is measured at the client, which is not instrumented
yet (the ``voice`` stream), and the screen says so.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.db import billing_dashboard_client as dash
from api.db.controls_models import AgentTaskTransitionModel
from api.db.models import (
    AgentEventModel,
    AgentTaskModel,
    CallTurnMetricModel,
    WebhookDeliveryModel,
    WorkflowModel,
    WorkflowRunModel,
)
from api.enums import AgentEventKind

PROBLEM_STATES = ("failed", "outcome_unknown", "needs_input")


def health_from_probes(snapshot: dict[str, Any]) -> dict[str, Any]:
    """The system snapshot in the console's words. A probe that failed to
    answer is ``unknown``; ``healthy`` only when every core probe said ok."""
    signals = []
    for name in ("database", "redis", "queue", "worker", "provider_balances"):
        probe = snapshot.get(name) or {}
        if "ok" not in probe:
            state = "unknown"
        elif probe["ok"] is True:
            state = "healthy"
        elif probe.get("latency_ms") is None:
            state = "unknown"
        else:
            state = "degraded"
        if name == "worker" and probe.get("alive") is None:
            state = "unknown"
        signals.append({"name": name, "state": state, "detail": probe.get("detail")})
    core = [s for s in signals if s["name"] in ("database", "redis", "worker")]
    if all(s["state"] == "healthy" for s in core):
        overall = "healthy"
    elif any(s["state"] == "unknown" for s in core):
        overall = "unknown"
    else:
        overall = "degraded"
    return {"state": overall, "signals": signals, "build": snapshot.get("build")}


async def jobs(
    session: AsyncSession, *, hours: int = 24, limit: int = 50
) -> dict[str, Any]:
    since = datetime.now(UTC) - timedelta(hours=hours)
    failing = (
        await session.execute(
            select(
                AgentTaskModel.id,
                AgentTaskModel.organization_id,
                AgentTaskModel.ledger_state,
                AgentTaskModel.status,
                AgentTaskModel.assignee_workflow_id,
                AgentTaskModel.created_at,
                AgentTaskModel.finished_at,
            )
            .where(
                AgentTaskModel.ledger_state.in_(PROBLEM_STATES)
                | (
                    (AgentTaskModel.ledger_state.is_(None))
                    & (AgentTaskModel.status == "could_not")
                ),
                func.coalesce(AgentTaskModel.finished_at, AgentTaskModel.created_at)
                >= since,
            )
            .order_by(AgentTaskModel.created_at.desc())
            .limit(limit)
        )
    ).all()
    counts = dict(
        (
            await session.execute(
                select(AgentTaskModel.ledger_state, func.count(AgentTaskModel.id))
                .where(
                    AgentTaskModel.ledger_state.isnot(None),
                    AgentTaskModel.created_at >= since,
                )
                .group_by(AgentTaskModel.ledger_state)
            )
        ).all()
    )
    return {
        "window_hours": hours,
        "by_state": counts,
        "failing": [
            {
                "task_id": r.id,
                "organization_id": r.organization_id,
                "state": r.ledger_state or "failed",
                "kind": "agent_task" if r.assignee_workflow_id else "person_task",
                "created_at": r.created_at.isoformat() if r.created_at else None,
                "finished_at": r.finished_at.isoformat() if r.finished_at else None,
            }
            for r in failing
        ],
    }


async def delivery(
    session: AsyncSession, *, hours: int = 24, limit: int = 50
) -> dict[str, Any]:
    since = datetime.now(UTC) - timedelta(hours=hours)
    counts = dict(
        (
            await session.execute(
                select(WebhookDeliveryModel.status, func.count(WebhookDeliveryModel.id))
                .where(WebhookDeliveryModel.created_at >= since)
                .group_by(WebhookDeliveryModel.status)
            )
        ).all()
    )
    dead = (
        await session.execute(
            select(
                WebhookDeliveryModel.id,
                WebhookDeliveryModel.organization_id,
                WebhookDeliveryModel.workflow_run_id,
                WebhookDeliveryModel.attempt_count,
                WebhookDeliveryModel.last_status_code,
                WebhookDeliveryModel.updated_at,
            )
            .where(
                WebhookDeliveryModel.status == "dead_letter",
                WebhookDeliveryModel.created_at >= since,
            )
            .order_by(WebhookDeliveryModel.updated_at.desc())
            .limit(limit)
        )
    ).all()
    return {
        "window_hours": hours,
        "by_status": counts,
        # The endpoint and the last error text are customer configuration
        # and provider output; the status code says enough to triage.
        "dead_letter": [
            {
                "delivery_id": d.id,
                "organization_id": d.organization_id,
                "workflow_run_id": d.workflow_run_id,
                "attempts": d.attempt_count,
                "last_status_code": d.last_status_code,
                "at": d.updated_at.isoformat() if d.updated_at else None,
            }
            for d in dead
        ],
        "retry": "delivery.retry is an ops command (needs the ops console).",
    }


_STAGES = (
    ("t_user_stopped_ms", "user_stopped"),
    ("t_endpoint_fired_ms", "endpoint"),
    ("t_stt_final_ms", "stt_final"),
    ("t_llm_first_token_ms", "llm_first_token"),
    ("t_tts_first_byte_ms", "tts_first_byte"),
    ("t_audio_out_ms", "audio_out"),
)


async def trace(session: AsyncSession, task_id: int) -> dict[str, Any] | None:
    task = await session.get(AgentTaskModel, task_id)
    if task is None:
        return None
    transitions = (
        (
            await session.execute(
                select(AgentTaskTransitionModel)
                .where(
                    AgentTaskTransitionModel.task_id == task.id,
                    AgentTaskTransitionModel.organization_id == task.organization_id,
                )
                .order_by(AgentTaskTransitionModel.sequence)
            )
        )
        .scalars()
        .all()
    )
    stages = [
        {
            "stage": "created",
            "at": task.created_at.isoformat() if task.created_at else None,
            "state": "queued",
        },
        *[
            {
                "stage": f"{t.from_state or 'start'} → {t.to_state}",
                "at": t.occurred_at.isoformat() if t.occurred_at else None,
                "state": t.to_state,
                "reason_code": t.reason_code,
                "sequence": t.sequence,
            }
            for t in transitions
        ],
    ]
    if not transitions:
        stages.append(
            {
                "stage": "ledger",
                "at": None,
                "state": task.ledger_state or task.status,
                "missing": "No ledger transitions recorded for this task.",
            }
        )
    if task.finished_at:
        stages.append(
            {
                "stage": "finished",
                "at": task.finished_at.isoformat(),
                "state": task.ledger_state or task.status,
            }
        )
    run = None
    turns: list[dict] = []
    if task.workflow_run_id:
        # Scoped: the run must belong to an agent in the task's workspace.
        r = (
            await session.execute(
                select(WorkflowRunModel)
                .join(WorkflowModel, WorkflowModel.id == WorkflowRunModel.workflow_id)
                .where(
                    WorkflowRunModel.id == task.workflow_run_id,
                    WorkflowModel.organization_id == task.organization_id,
                )
            )
        ).scalar_one_or_none()
        if r is None:
            stages.append(
                {
                    "stage": "run",
                    "at": None,
                    "state": None,
                    "missing": "The run is not in this workspace or no longer exists.",
                }
            )
        else:
            run = {
                "id": r.id,
                "mode": r.mode,
                "language": r.language,
                "created_at": r.created_at.isoformat() if r.created_at else None,
                "ended_at": r.ended_at.isoformat() if r.ended_at else None,
                "completed": bool(r.is_completed),
                "costed": r.costed_at is not None,
            }
            metrics = (
                (
                    await session.execute(
                        select(CallTurnMetricModel)
                        .where(CallTurnMetricModel.workflow_run_id == r.id)
                        .order_by(CallTurnMetricModel.turn_index)
                        .limit(50)
                    )
                )
                .scalars()
                .all()
            )
            for m in metrics:
                marks = {label: getattr(m, col) for col, label in _STAGES}
                turns.append(
                    {
                        "turn": m.turn_index,
                        "marks_ms": marks,
                        "missing": [label for label, v in marks.items() if v is None],
                        "perceived_ms": m.latency_ms,
                        "tool": m.tool_called,
                        "tool_ms": m.tool_ms,
                    }
                )
    card = None
    if task.approval_event_id:
        ev = await session.get(AgentEventModel, task.approval_event_id)
        if (
            ev is not None
            and ev.organization_id == task.organization_id
            and ev.kind == AgentEventKind.ACTION_PROPOSED.value
        ):
            p = ev.payload or {}
            card = {
                "event_id": ev.id,
                "state": p.get("state"),
                "ledger_state": p.get("ledger_state"),
                "version": p.get("version"),
                "action": p.get("action"),
                "proposed_at": ev.at.isoformat() if ev.at else None,
                "confirmed_at": (p.get("confirmed") or {}).get("at"),
            }
    return {
        "task": {
            "id": task.id,
            "organization_id": task.organization_id,
            "number": task.number,
            "state": task.ledger_state or task.status,
            "state_version": task.state_version,
            "payload_version": task.payload_version,
            "has_evidence": bool(task.outcome_evidence),
        },
        "stages": stages,
        "run": run,
        "turns": turns,
        "card": card,
        "content": "Titles, briefs and messages are not shown in staff traces.",
    }


async def voice_latency(
    session: AsyncSession,
    *,
    days: int,
    language: str | None = None,
    organization_id: int | None = None,
) -> dict[str, Any]:
    from api.services.billing.rollup import IST

    end = datetime.now(IST).date()
    start = end - timedelta(days=days - 1)
    return {
        "period": {"start": start.isoformat(), "end": end.isoformat()},
        "headline": await dash.latency_headline(
            session, start=start, end=end, organization_id=organization_id
        ),
        "by_language": await dash.latency_by_language(
            session, start=start, end=end, organization_id=organization_id
        ),
        "stage_medians": await dash.pipeline_stage_medians(
            session, start=start, end=end
        ),
        "languages": await dash.distinct_languages(session),
        "filters": {
            "language": language,
            "channel": "not_recorded",
            "noise": "not_recorded",
            "concurrency": "not_recorded",
        },
        "measured_at": "server",
        "client_measure": {
            "state": "needs_setup",
            "reason": "Speech end to first audible response at the client is not instrumented yet (voice stream).",
        },
        "definition": "perceived: user stopped speaking to first audio out, one pipeline clock; turns missing either mark are excluded, not zero.",
    }


#: A call connected for longer than this is shown as possibly stuck.
LONG_CALL_MINUTES = 30
#: A run created but never connected after this long did not start.
NOT_CONNECTED_MINUTES = 5


async def active_calls(session: AsyncSession, *, now: datetime | None = None) -> dict:
    """Calls live right now (screen 39, "active-call health"): how many,
    on which channel, how long the longest has run, and the ones that look
    stuck -- connected far longer than a call should be, or created and
    never connected. Ids, workspaces, states and times only.

    Voice runs (every mode but ``NON_VOICE_RUN_MODES``, so a new carrier is
    counted, not dropped) are calls; chat runs are excluded so a busy chat
    day does not read as a phone outage."""
    from api.enums import NON_VOICE_RUN_MODES

    now = now or datetime.now(UTC)
    r = WorkflowRunModel
    text_modes = tuple(NON_VOICE_RUN_MODES)
    rows = (
        await session.execute(
            select(
                r.id,
                r.mode,
                r.call_type,
                r.state,
                r.created_at,
                r.answered_at,
                r.campaign_id,
                WorkflowModel.organization_id,
            )
            .join(WorkflowModel, WorkflowModel.id == r.workflow_id)
            .where(
                r.state.in_(("initialized", "running")),
                r.is_completed.is_not(True),
                r.created_at >= now - timedelta(hours=12),
                r.mode.not_in(text_modes),
            )
            .order_by(r.created_at)
            .limit(500)
        )
    ).all()

    def _age_minutes(at: datetime | None) -> float | None:
        if at is None:
            return None
        if at.tzinfo is None:
            at = at.replace(tzinfo=UTC)
        return round((now - at).total_seconds() / 60, 1)

    running = [row for row in rows if row.state == "running"]
    by_mode: dict[str, int] = {}
    for row in running:
        by_mode[row.mode or "unknown"] = by_mode.get(row.mode or "unknown", 0) + 1
    stuck = []
    for row in rows:
        age = _age_minutes(row.created_at)
        if row.state == "running" and age is not None and age > LONG_CALL_MINUTES:
            reason = "long_running"
        elif (
            row.state == "initialized"
            and age is not None
            and age > NOT_CONNECTED_MINUTES
        ):
            reason = "never_connected"
        else:
            continue
        stuck.append(
            {
                "workflow_run_id": row.id,
                "organization_id": row.organization_id,
                "campaign_id": row.campaign_id,
                "mode": row.mode,
                "direction": row.call_type,
                "state": row.state,
                "age_minutes": age,
                "reason": reason,
            }
        )
    longest = max((_age_minutes(row.created_at) or 0 for row in running), default=None)
    return {
        "observed_at": now.isoformat(),
        "live": len(running),
        "connecting": sum(1 for row in rows if row.state == "initialized"),
        "by_channel": [{"mode": k, "live": v} for k, v in sorted(by_mode.items())],
        "longest_minutes": longest,
        "possibly_stuck": stuck[:50],
        "thresholds": {
            "long_call_minutes": LONG_CALL_MINUTES,
            "not_connected_minutes": NOT_CONNECTED_MINUTES,
        },
    }
