"""Persist per-turn latency instrumentation for a completed call.

The Latency screen computes p50/p95 in SQL over these raw rows — averaging
pre-aggregated percentiles gives wrong answers — and the call receipt draws its
per-turn breakdown from them. Nothing wrote the table outside the seed script,
so both were empty in production.
"""

from __future__ import annotations

from loguru import logger
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from api.db.models import CallTurnMetricModel

# The cumulative timeline the schema stores. Stage durations are derived from
# the differences between consecutive marks at query time.
_TIMELINE_COLUMNS = (
    "t_user_stopped_ms",
    "t_endpoint_fired_ms",
    "t_stt_final_ms",
    "t_llm_first_token_ms",
    "t_tts_first_byte_ms",
    "t_audio_out_ms",
)


async def persist_turn_metrics(
    session: AsyncSession,
    *,
    workflow_run_id: int,
    turns: list[dict],
) -> int:
    """Replace this run's turn rows with ``turns``. Returns rows written.

    Replace rather than append so a retried teardown cannot double-count a
    call's turns into the percentile queries.
    """
    if not turns:
        return 0

    await session.execute(
        delete(CallTurnMetricModel).where(
            CallTurnMetricModel.workflow_run_id == workflow_run_id
        )
    )

    written = 0
    for turn in turns:
        latency_ms = turn.get("latency_ms")
        if latency_ms is None:
            continue
        session.add(
            CallTurnMetricModel(
                workflow_run_id=workflow_run_id,
                turn_index=turn.get("turn_index", written),
                latency_ms=latency_ms,
                # A mark the pipeline did not report stays NULL. The
                # percentile queries already filter on latency_ms, and a
                # missing mark is honest where a zero would read as instant.
                **{
                    c: turn.get(c)
                    for c in _TIMELINE_COLUMNS
                    if turn.get(c) is not None
                },
            )
        )
        written += 1

    return written


async def record_turn_metrics(workflow_run_id: int, turns: list[dict]) -> None:
    """Persist turn metrics in their own session, swallowing failures.

    Called from pipeline teardown, where losing latency instrumentation must
    never cost the call its recording, transcript or usage.
    """
    if not turns:
        return

    from api.db import db_client

    try:
        async with db_client.async_session() as session:
            written = await persist_turn_metrics(
                session, workflow_run_id=workflow_run_id, turns=turns
            )
            await session.commit()
        logger.debug(
            "Recorded {} turn metric row(s) for workflow run {}",
            written,
            workflow_run_id,
        )
    except Exception as e:
        logger.error(
            "Failed to record turn metrics for workflow run {}: {}",
            workflow_run_id,
            e,
        )
