"""Writing catalogue events durably, and sending them on afterwards.

Handoff 35, "Authoritative events and failure handling": a transactional
outbox, so an event describing a state change is written with that change
(pass the caller's ``session``) or right after it, and a separate job sends
it. Analytics delivery can therefore never block a voice turn or a request,
and an analytics outage loses nothing: the rows wait.

``emit`` never raises. A refused event (a bug in the emitter) is logged as
an error, loud, and nothing is written; the business change it describes has
already happened and must not be undone by its telemetry.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from loguru import logger
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from api.db import db_client
from api.db.controls_models import AnalyticsOutboxModel
from api.services import features
from api.services.events import envelope

FLAG = "event_catalogue"

#: How many rows one dispatch pass sends.
BATCH = 200
#: After this many failed sends a row is left for a person to look at.
MAX_ATTEMPTS = 8
#: Delivered rows are kept this long for audit, then deleted; undelivered
#: rows are kept for the same window and then dropped with a log line.
RETENTION_DAYS = 30


def enabled() -> bool:
    return features.is_on(FLAG)


async def emit(
    name: str,
    *,
    session: AsyncSession | None = None,
    **fields: Any,
) -> str | None:
    """Write one event to the outbox. Returns its id, or None when the
    catalogue is off or the event was refused.

    With ``session`` the row joins the caller's transaction and commits or
    rolls back with the change it describes; without, it is committed on
    its own.
    """
    if not enabled():
        return None
    try:
        env = envelope.build(name, **fields)
    except envelope.EventRefused as exc:
        logger.error("Analytics event {} refused: {}", name, exc)
        return None
    except Exception as exc:  # noqa: BLE001 - telemetry must not break the change
        logger.error("Analytics event {} could not be built: {}", name, exc)
        return None
    stmt = (
        insert(AnalyticsOutboxModel)
        .values(
            event_id=env["event_id"],
            name=name,
            envelope=env,
            occurred_at=datetime.fromisoformat(env["occurred_at"]),
            created_at=datetime.now(UTC),
        )
        .on_conflict_do_nothing(index_elements=["event_id"])
    )
    try:
        if session is not None:
            await session.execute(stmt)
        else:
            async with db_client.async_session() as own:
                await own.execute(stmt)
                await own.commit()
    except Exception as exc:  # noqa: BLE001 - see the module docstring
        logger.error("Analytics event {} could not be written: {}", name, exc)
        return None
    return env["event_id"]


def _send(row: AnalyticsOutboxModel) -> None:
    """One event to PostHog, with its id as the event uuid so a resend
    after a crash is the same event there, not a second one."""
    from api.services.posthog_client import get_posthog

    client = get_posthog()
    if client is None:
        raise RuntimeError("not_configured")
    env = dict(row.envelope)
    properties = dict(env.pop("properties", {}) or {})
    # The envelope's own fields travel as properties, prefixed so they never
    # collide with an event's typed ones.
    for key in (
        "schema_version",
        "envelope_version",
        "owner",
        "environment",
        "release",
        "workspace_id",
        "task_id",
        "trace_id",
        "configuration_version",
    ):
        properties[f"env_{key}"] = env.get(key)
    # No person profile for a pseudonym: nothing here should become one.
    properties["$process_person_profile"] = False
    client.capture(
        row.name,
        distinct_id=env.get("user_id") or "system",
        properties=properties,
        timestamp=row.occurred_at,
        uuid=row.event_id,
        disable_geoip=True,
    )


async def deliver_pending(limit: int = BATCH) -> dict[str, int]:
    """Send what is waiting. Rows are locked as they are claimed, so two
    workers never send one row twice in the same pass."""
    sent = failed = 0
    async with db_client.async_session() as session:
        rows = (
            (
                await session.execute(
                    select(AnalyticsOutboxModel)
                    .where(
                        AnalyticsOutboxModel.delivered_at.is_(None),
                        AnalyticsOutboxModel.attempts < MAX_ATTEMPTS,
                    )
                    .order_by(AnalyticsOutboxModel.created_at)
                    .limit(limit)
                    .with_for_update(skip_locked=True)
                )
            )
            .scalars()
            .all()
        )
        for row in rows:
            try:
                _send(row)
            except Exception as exc:  # noqa: BLE001 - recorded on the row
                row.attempts = (row.attempts or 0) + 1
                code = str(exc) if str(exc) == "not_configured" else type(exc).__name__
                row.last_error = code[:64]
                failed += 1
                if code == "not_configured":
                    # Nothing will succeed this pass; keep the rest waiting
                    # without spending their attempts.
                    row.attempts -= 1
                    break
                continue
            row.delivered_at = datetime.now(UTC)
            row.attempts = (row.attempts or 0) + 1
            sent += 1
        await session.commit()
    return {"sent": sent, "failed": failed}


async def prune(now: datetime | None = None) -> int:
    """Drop rows past the retention window. Returns how many."""
    cutoff = (now or datetime.now(UTC)) - timedelta(days=RETENTION_DAYS)
    async with db_client.async_session() as session:
        stale_pending = (
            await session.execute(
                select(AnalyticsOutboxModel.event_id).where(
                    AnalyticsOutboxModel.delivered_at.is_(None),
                    AnalyticsOutboxModel.created_at < cutoff,
                )
            )
        ).all()
        if stale_pending:
            logger.warning(
                "Dropping {} analytics events never delivered in {} days",
                len(stale_pending),
                RETENTION_DAYS,
            )
        result = await session.execute(
            delete(AnalyticsOutboxModel).where(AnalyticsOutboxModel.created_at < cutoff)
        )
        await session.commit()
        return int(result.rowcount or 0)
