"""Recording who reached what.

Two questions this answers, both of which arrive at the worst possible moment.
A data principal asks who has listened to their call — DPDP s11(1)(c) entitles
them to know. A breach is suspected and somebody has to say what was actually
reached, which GDPR Art 33 requires within 72 hours of becoming aware.

Neither question can be answered retrospectively. Either the log was being
written or it was not.

**Logs the act of access, not the outcome.** A row is written when a signed URL
is issued, because that is the moment access becomes possible. Whether the
browser then played the audio is not something a server can know, and recording
"played" when it means "was allowed to play" would make this a worse record than
an honest one.

**Never blocks the thing it observes.** A failure to write an audit row must not
stop a customer opening their own recording; it is logged loudly and the request
proceeds. An audit trail that can take down the product it audits gets switched
off the first time it does.
"""

from __future__ import annotations

from datetime import datetime

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.db.models import DataAccessLogModel

RECORDING = "recording"
TRANSCRIPT = "transcript"
KYC_DOCUMENT = "kyc_document"
EXPORT = "export"


async def record_access(
    session: AsyncSession,
    *,
    organization_id: int | None,
    user_id: int | None,
    resource_type: str,
    resource_id: str | None = None,
    workflow_run_id: int | None = None,
    action: str = "signed_url",
    actor_kind: str | None = None,
    ip_address: str | None = None,
) -> None:
    """Write one access row. Never raises."""
    try:
        session.add(
            DataAccessLogModel(
                organization_id=organization_id,
                user_id=user_id,
                resource_type=resource_type,
                resource_id=str(resource_id) if resource_id is not None else None,
                workflow_run_id=workflow_run_id,
                action=action,
                actor_kind=actor_kind,
                ip_address=ip_address,
            )
        )
        await session.flush()
    except Exception as exc:  # noqa: BLE001 - auditing must not break access
        logger.error(
            "Could not record {} access to {} {}: {}",
            action,
            resource_type,
            resource_id,
            exc,
        )


async def access_for_run(
    session: AsyncSession, *, organization_id: int, workflow_run_id: int
) -> list[dict]:
    """Who reached one call's data. Answers a data principal's question."""
    rows = (
        await session.scalars(
            select(DataAccessLogModel)
            .where(
                DataAccessLogModel.organization_id == organization_id,
                DataAccessLogModel.workflow_run_id == workflow_run_id,
            )
            .order_by(DataAccessLogModel.created_at.desc())
        )
    ).all()
    return [
        {
            "at": r.created_at.isoformat() if r.created_at else None,
            "user_id": r.user_id,
            "resource_type": r.resource_type,
            "action": r.action,
            "actor_kind": r.actor_kind,
        }
        for r in rows
    ]


async def recent_access(
    session: AsyncSession, *, organization_id: int, limit: int = 200
) -> list[dict]:
    """An account's recent access history, newest first."""
    rows = (
        await session.scalars(
            select(DataAccessLogModel)
            .where(DataAccessLogModel.organization_id == organization_id)
            .order_by(DataAccessLogModel.created_at.desc())
            .limit(limit)
        )
    ).all()
    return [
        {
            "at": r.created_at.isoformat() if r.created_at else None,
            "user_id": r.user_id,
            "resource_type": r.resource_type,
            "resource_id": r.resource_id,
            "workflow_run_id": r.workflow_run_id,
            "action": r.action,
            "actor_kind": r.actor_kind,
            "ip_address": r.ip_address,
        }
        for r in rows
    ]


async def window_report(
    session: AsyncSession,
    *,
    organization_id: int,
    start: datetime,
    end: datetime,
) -> dict:
    """What was reached between two moments.

    GDPR Art 33 allows 72 hours from becoming aware of a breach to describe its
    scope: what categories of data, roughly how many records, and who touched
    them. That is a report over this log, and it is only answerable because the
    log was being written before anyone suspected anything.

    Returns counts and identifiers, never content. A breach report that itself
    contains the compromised data is a second incident.
    """
    rows = (
        await session.scalars(
            select(DataAccessLogModel)
            .where(
                DataAccessLogModel.organization_id == organization_id,
                DataAccessLogModel.created_at >= start,
                DataAccessLogModel.created_at <= end,
            )
            .order_by(DataAccessLogModel.created_at)
        )
    ).all()

    by_type: dict[str, int] = {}
    by_actor: dict[str, int] = {}
    runs: set[int] = set()
    resources: set[str] = set()

    for row in rows:
        by_type[row.resource_type] = by_type.get(row.resource_type, 0) + 1
        actor = str(row.user_id) if row.user_id else f"unauthenticated:{row.actor_kind}"
        by_actor[actor] = by_actor.get(actor, 0) + 1
        if row.workflow_run_id:
            runs.add(row.workflow_run_id)
        if row.resource_id:
            resources.add(row.resource_id)

    return {
        "window_start": start.isoformat(),
        "window_end": end.isoformat(),
        "total_accesses": len(rows),
        "by_resource_type": by_type,
        "by_actor": by_actor,
        "distinct_calls_touched": len(runs),
        "distinct_objects_touched": len(resources),
        "affected_call_ids": sorted(runs),
        "first_access": rows[0].created_at.isoformat() if rows else None,
        "last_access": rows[-1].created_at.isoformat() if rows else None,
    }
