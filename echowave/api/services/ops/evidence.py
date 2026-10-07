"""Evidence that an operation happened (handoff 15 H, 34).

"Review backups and deployments" is a read-only console action with one
requirement: show the last successful backup and the **last restore test**,
and link the evidence. A restore drill run from a terminal leaves a line in a
log file on one box; nobody reviewing the console can see it. So the drill
script, the capacity review and a deploy each record a row here, with the
numbers that prove it (durations, counts, percentiles -- never data), and the
console reads the newest per kind.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api import constants
from api.db.ops_models import OpsEvidenceModel
from api.services.ops.redaction import redact_properties, redact_text

KINDS = (
    "restore_drill",
    "capacity_review",
    "backup",
    "deployment",
    "laya_evaluation",
    "credential_rotation",
)
OUTCOMES = ("passed", "failed", "partial")


class EvidenceError(ValueError):
    pass


@dataclass(frozen=True)
class Evidence:
    id: int
    kind: str
    environment: str
    outcome: str
    summary: str
    metrics: dict[str, Any]
    link: str | None
    occurred_at: datetime
    recorded_at: datetime

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "environment": self.environment,
            "outcome": self.outcome,
            "summary": self.summary,
            "metrics": self.metrics,
            "link": self.link,
            "occurred_at": self.occurred_at.isoformat(),
            "recorded_at": self.recorded_at.isoformat(),
        }


def _view(row: OpsEvidenceModel) -> Evidence:
    occurred = row.occurred_at
    if occurred.tzinfo is None:
        occurred = occurred.replace(tzinfo=UTC)
    recorded = row.recorded_at
    if recorded.tzinfo is None:
        recorded = recorded.replace(tzinfo=UTC)
    return Evidence(
        id=row.id,
        kind=row.kind,
        environment=row.environment,
        outcome=row.outcome,
        summary=row.summary,
        metrics=dict(row.metrics or {}),
        link=row.link,
        occurred_at=occurred,
        recorded_at=recorded,
    )


def _numbers_only(metrics: dict[str, Any] | None) -> dict[str, Any]:
    """Evidence carries measurements, not data: scalars that survive
    redaction, nothing nested."""
    return redact_properties(metrics or {})


async def record(
    session: AsyncSession,
    *,
    kind: str,
    outcome: str,
    summary: str,
    metrics: dict[str, Any] | None = None,
    link: str | None = None,
    occurred_at: datetime | None = None,
    recorded_by: int | None = None,
    environment: str | None = None,
) -> Evidence:
    """Add one evidence row; the caller commits."""
    if kind not in KINDS:
        raise EvidenceError(
            f"Unknown evidence kind {kind!r}; one of {', '.join(KINDS)}"
        )
    if outcome not in OUTCOMES:
        raise EvidenceError(f"Outcome must be one of {', '.join(OUTCOMES)}")
    summary = (redact_text((summary or "").strip()) or "")[:500]
    if not summary:
        raise EvidenceError("Say what happened in one line.")
    row = OpsEvidenceModel(
        kind=kind,
        environment=environment or constants.ENVIRONMENT,
        outcome=outcome,
        summary=summary,
        metrics=_numbers_only(metrics),
        link=(link or None) and link[:500],
        occurred_at=occurred_at or datetime.now(UTC),
        recorded_at=datetime.now(UTC),
        recorded_by=recorded_by,
    )
    session.add(row)
    await session.flush()
    return _view(row)


async def latest(session: AsyncSession, kind: str) -> Evidence | None:
    row = await session.scalar(
        select(OpsEvidenceModel)
        .where(OpsEvidenceModel.kind == kind)
        .order_by(OpsEvidenceModel.occurred_at.desc(), OpsEvidenceModel.id.desc())
        .limit(1)
    )
    return _view(row) if row is not None else None


async def recent(
    session: AsyncSession, *, kind: str | None = None, limit: int = 50
) -> list[Evidence]:
    query = select(OpsEvidenceModel).order_by(
        OpsEvidenceModel.occurred_at.desc(), OpsEvidenceModel.id.desc()
    )
    if kind:
        query = query.where(OpsEvidenceModel.kind == kind)
    rows = (await session.scalars(query.limit(max(1, min(limit, 200))))).all()
    return [_view(row) for row in rows]


async def summary(session: AsyncSession) -> dict[str, Any]:
    """The newest row of every kind; a kind with none is listed as None so
    the screen says "never" rather than leaving the row out."""
    return {
        kind: (found.as_dict() if (found := await latest(session, kind)) else None)
        for kind in KINDS
    }
