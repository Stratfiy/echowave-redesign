"""Voice latency per turn, measured the way handoff 12 defines it.

**Two clocks, never subtracted from each other.** The person's device
measures what only it can hear: from the end of the person's speech to the
first meaningful audible reply (``response_ms``), and from a genuine
interruption to playback stopping (``interruption_ms``). The server measures
its own stages on its own monotonic clock: transcript final, Decibyl's first
words, the voice's first audio. Each is a duration on one clock; a device
time is never subtracted from a server time.

**Missing is unknown, not zero.** A stage nobody measured is absent from the
row and from the percentiles (design screen 40: "missing stages remain
unknown").

**Filler is not a response.** A turn that called a tool is flagged and
reported apart; its first audio may be an acknowledgement, which handoff 12
says does not count as meaningful response.

Off (``voice_latency``), nothing is recorded and the routes are 404s.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta
from typing import Any, Iterable

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from api.db import db_client
from api.db.voice_models import VoiceSessionModel, VoiceTurnModel
from api.services import features

FLAG = "voice_latency"
STAGES = ("stt_final", "brain_first_text", "tts_first_audio")
#: Anything past a minute is a broken measurement, not a slow turn.
MAX_MS = 60_000
#: Below this many samples a p95 is said to be uncertain (handoff 12: "report
#: uncertainty for small p95 samples").
SMALL_SAMPLE = 20
#: The targets handoff 12 proposes, shown beside the measurement -- proposed
#: acceptance targets, not achieved numbers.
TARGETS = {
    "response_p50_ms": 800,
    "response_p95_ms": 1500,
    "interruption_p95_ms": 250,
}


def enabled(organization_id: int | None = None) -> bool:
    return features.is_on(FLAG, organization_id)


class LatencyInvalid(ValueError):
    pass


def _ms(value: Any, name: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise LatencyInvalid(f"{name} must be a number of milliseconds")
    if math.isnan(value) or value < 0 or value > MAX_MS:
        raise LatencyInvalid(f"{name} is out of range")
    return int(round(value))


async def record_client(
    *,
    organization_id: int,
    user_id: int,
    session_id: int,
    turn_index: int,
    response_ms: Any = None,
    interruption_ms: Any = None,
    interrupted: bool = False,
) -> dict[str, Any] | None:
    """What the device measured for one turn of the person's own session.

    Returns None while off. Raises LatencyInvalid; a session that is not the
    caller's is LookupError, answered as not found.
    """
    if not enabled(organization_id):
        return None
    if turn_index < 0 or turn_index > 10_000:
        raise LatencyInvalid("turn_index is out of range")
    values = {
        "response_ms": _ms(response_ms, "response_ms"),
        "interruption_ms": _ms(interruption_ms, "interruption_ms"),
        "interrupted": bool(interrupted),
    }
    async with db_client.async_session() as session:
        owner = (
            await session.scalars(
                select(VoiceSessionModel).where(
                    VoiceSessionModel.id == session_id,
                    VoiceSessionModel.organization_id == organization_id,
                    VoiceSessionModel.user_id == user_id,
                )
            )
        ).first()
        if owner is None:
            raise LookupError("No such session.")
        statement = insert(VoiceTurnModel).values(
            session_id=session_id,
            organization_id=organization_id,
            user_id=user_id,
            turn_index=turn_index,
            language=owner.language,
            channel="web",
            stages={},
            created_at=datetime.now(UTC),
            **values,
        )
        # Only the device's own fields: a late report never erases the
        # server's stages, and a repeat report replaces the earlier one.
        statement = statement.on_conflict_do_update(
            constraint="uq_voice_turn_index", set_=values
        )
        await session.execute(statement)
        await session.commit()
    return {"session_id": session_id, "turn_index": turn_index, **values}


async def record_server(
    *,
    session_id: int,
    turn_index: int,
    stages: dict[str, float | None],
    tool_turn: bool = False,
    stt_provider: str | None = None,
    tts_provider: str | None = None,
) -> None:
    """The server's stage durations for one turn, from the pipeline. Never
    raises: a measurement must never break the conversation it measures."""
    from loguru import logger

    try:
        async with db_client.async_session() as session:
            owner = await session.get(VoiceSessionModel, session_id)
            if owner is None or not enabled(owner.organization_id):
                return
            clean = {
                name: int(round(value))
                for name, value in stages.items()
                if name in STAGES
                and isinstance(value, (int, float))
                and 0 <= value <= MAX_MS
            }
            values = {
                "stages": clean,
                "tool_turn": bool(tool_turn),
                "stt_provider": stt_provider,
                "tts_provider": tts_provider,
            }
            statement = insert(VoiceTurnModel).values(
                session_id=session_id,
                organization_id=owner.organization_id,
                user_id=owner.user_id,
                turn_index=turn_index,
                language=owner.language,
                channel="web",
                created_at=datetime.now(UTC),
                **values,
            )
            statement = statement.on_conflict_do_update(
                constraint="uq_voice_turn_index", set_=values
            )
            await session.execute(statement)
            await session.commit()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Voice turn latency not recorded: {}", exc)


def percentile(values: list[int], p: float) -> int | None:
    """Nearest-rank percentile; None for no samples."""
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, math.ceil(p / 100 * len(ordered)))
    return ordered[rank - 1]


def _group(rows: Iterable[VoiceTurnModel]) -> dict[tuple[str, str, str], list]:
    groups: dict[tuple[str, str, str], list] = {}
    for row in rows:
        key = (
            row.language or "unknown",
            row.channel or "web",
            row.tts_provider or "unknown",
        )
        groups.setdefault(key, []).append(row)
    return groups


def summarise(rows: list[VoiceTurnModel]) -> list[dict[str, Any]]:
    """p50/p95 and sample sizes by language, channel and voice provider.

    Simple turns (no tool) only for response latency; tool turns are counted
    apart. Interruption latency only over turns that were interrupted.
    """
    out = []
    for (language, channel, provider), group in sorted(_group(rows).items()):
        simple = [
            r.response_ms
            for r in group
            if not r.tool_turn and r.response_ms is not None
        ]
        interrupts = [
            r.interruption_ms
            for r in group
            if r.interrupted and r.interruption_ms is not None
        ]
        stage_values = {
            stage: [
                int((r.stages or {})[stage])
                for r in group
                if isinstance((r.stages or {}).get(stage), (int, float))
            ]
            for stage in STAGES
        }
        out.append(
            {
                "language": language,
                "channel": channel,
                "tts_provider": provider,
                "turns": len(group),
                "tool_turns": sum(1 for r in group if r.tool_turn),
                "response": {
                    "samples": len(simple),
                    "p50_ms": percentile(simple, 50),
                    "p95_ms": percentile(simple, 95),
                    "small_sample": len(simple) < SMALL_SAMPLE,
                },
                "interruption": {
                    "samples": len(interrupts),
                    "p95_ms": percentile(interrupts, 95),
                    "small_sample": len(interrupts) < SMALL_SAMPLE,
                },
                "stages": {
                    stage: {
                        "samples": len(values),
                        "p50_ms": percentile(values, 50),
                        "p95_ms": percentile(values, 95),
                    }
                    for stage, values in stage_values.items()
                },
                "missing_response": sum(
                    1 for r in group if not r.tool_turn and r.response_ms is None
                ),
            }
        )
    return out


async def summary(
    *, days: int = 7, organization_id: int | None = None
) -> dict[str, Any]:
    """The staff view (design screen 40's voice comparison)."""
    since = datetime.now(UTC) - timedelta(days=max(1, min(days, 90)))
    async with db_client.async_session() as session:
        query = select(VoiceTurnModel).where(VoiceTurnModel.created_at >= since)
        if organization_id is not None:
            query = query.where(VoiceTurnModel.organization_id == organization_id)
        rows = list((await session.scalars(query)).all())
    return {
        "since": since.isoformat(),
        "targets": TARGETS,
        "definition": (
            "Response: the person's speech end to Decibyl's first meaningful "
            "audible reply, measured on the person's device. Interruption: a "
            "genuine interruption to playback stopping, on the device. Stages "
            "are server durations. Tool turns are reported apart; filler is "
            "not a response. Targets are proposed, not achieved."
        ),
        "groups": summarise(rows),
    }
