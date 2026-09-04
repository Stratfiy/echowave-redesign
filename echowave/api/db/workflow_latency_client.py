"""What one agent's turns actually cost in time, stage by stage.

The model row on the agent screen names three vendors and claims a latency for
each. Every competitor prints the vendor's own datasheet figure there. We do
not have to: ``call_turn_metrics`` already records, on every real call, when
the transcript landed, when the model's first token arrived and when the first
byte of audio came back. The difference between those marks *is* the number the
card wants, measured on this account's own traffic over Indian telephony.

Three properties this has to hold to be worth printing:

**Stage arithmetic matches the receipt.** The same three subtractions
``billing_dashboard_client.call_latency_summary`` makes for one call, so a
figure here and a figure on a call receipt cannot disagree.

**The first turn is excluded.** It has no conversation context, a cold
connection, and often a pre-recorded greeting, so averaging it in makes a
healthy agent look slow and hides a genuinely slow opening. The existing
per-call summary separates it for the same reason.

**A thin sample says so rather than rounding confidently.** A p50 over four
turns is not a measurement, and a card that prints one as though it were is
worse than a card that prints nothing.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import Integer, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.db.models import CallTurnMetricModel, WorkflowModel, WorkflowRunModel

#: Below this many usable turns the answer is withheld. Chosen to be obviously
#: a sample rather than a statistic: nobody should read a median of six turns
#: as "what this agent does".
MIN_TURNS = 20

#: How far back to look. Long enough to gather turns on a low-volume agent,
#: short enough that a model changed last month is not still being reported on.
WINDOW_DAYS = 30


def _stage(end, start):
    """A stage's duration, or NULL when either mark is missing.

    Marks are clamped monotonic upstream (see ``pipeline_metrics_aggregator``),
    so a stage can read as instant but never as negative.
    """
    return func.cast(end - start, Integer)


async def stage_latency(
    session: AsyncSession,
    *,
    workflow_id: int,
    organization_id: int,
    window_days: int = WINDOW_DAYS,
    min_turns: int = MIN_TURNS,
) -> dict | None:
    """Median milliseconds for transcribe, think and speak on this agent.

    ``None`` when the agent has not run enough turns to say. Scoped through
    ``workflows.organization_id`` rather than trusting the id in the path --
    turn metrics are not themselves org-scoped, so the join is what stops one
    account reading another's latency.
    """
    since = datetime.now(UTC) - timedelta(days=window_days)

    transcribe = _stage(
        CallTurnMetricModel.t_stt_final_ms, CallTurnMetricModel.t_endpoint_fired_ms
    )
    think = _stage(
        CallTurnMetricModel.t_llm_first_token_ms, CallTurnMetricModel.t_stt_final_ms
    )
    speak = _stage(
        CallTurnMetricModel.t_tts_first_byte_ms, CallTurnMetricModel.t_llm_first_token_ms
    )

    def median(expr):
        return func.percentile_cont(0.5).within_group(expr.asc())

    row = (
        await session.execute(
            select(
                func.count().label("turns"),
                median(transcribe).label("transcribe_ms"),
                median(think).label("think_ms"),
                median(speak).label("speak_ms"),
                median(CallTurnMetricModel.latency_ms).label("total_ms"),
            )
            .select_from(CallTurnMetricModel)
            .join(
                WorkflowRunModel,
                WorkflowRunModel.id == CallTurnMetricModel.workflow_run_id,
            )
            .join(WorkflowModel, WorkflowModel.id == WorkflowRunModel.workflow_id)
            .where(
                WorkflowModel.id == workflow_id,
                WorkflowModel.organization_id == organization_id,
                CallTurnMetricModel.created_at >= since,
                # The opening turn is not comparable to the rest.
                CallTurnMetricModel.turn_index > 0,
                CallTurnMetricModel.latency_ms.isnot(None),
            )
        )
    ).one()

    if not row.turns or row.turns < min_turns:
        return None

    def ms(value) -> int | None:
        return None if value is None else int(round(value))

    return {
        "turns": int(row.turns),
        "window_days": window_days,
        "transcribe_ms": ms(row.transcribe_ms),
        "think_ms": ms(row.think_ms),
        "speak_ms": ms(row.speak_ms),
        "total_ms": ms(row.total_ms),
    }
