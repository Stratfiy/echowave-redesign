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
