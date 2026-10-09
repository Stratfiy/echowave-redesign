"""How calls placed in a window ended, read the way #575 reads one call.

``services/telephony/call_evidence.classify`` is the one rule for what a run
proves about its call; this applies it to every carrier call in a window
instead of re-deriving it in SQL, so the alert and the reminder paths can
never disagree about what "failed" means.

Only carrier calls (``CARRIER_RUN_MODES``) are counted: a browser test call
has no carrier to fail and no outcome to be unknown. A run that has not
finished is in progress and stays out of the shares -- a call that is still
ringing has not failed.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from types import SimpleNamespace
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.db.models import WorkflowModel, WorkflowRunModel
from api.enums import CARRIER_RUN_MODES, NON_VOICE_RUN_MODES
from api.services.telephony import call_evidence


@dataclass
class CallOutcomes:
    answered: int = 0
    not_connected: int = 0
    carrier_failed: int = 0
    #: Finished, with no evidence of how: no answer stamp, no seconds, no
    #: carrier disposition. The "unknown" #575 introduced.
    unknown: int = 0
    in_progress: int = 0
    #: Carrier failures and unknowns, by workspace, for the alert body.
    bad_by_org: dict[int, int] | None = None

    @property
    def finished(self) -> int:
        return self.answered + self.not_connected + self.carrier_failed + self.unknown

    @property
    def bad(self) -> int:
        return self.carrier_failed + self.unknown

    @property
    def bad_share(self) -> float:
        return self.bad / self.finished if self.finished else 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "finished": self.finished,
            "answered": self.answered,
            "not_connected": self.not_connected,
            "carrier_failed": self.carrier_failed,
            "unknown": self.unknown,
            "in_progress": self.in_progress,
        }


async def outcomes(
    session: AsyncSession, start: datetime, end: datetime
) -> CallOutcomes:
    rows = (
        await session.execute(
            select(
                WorkflowModel.organization_id,
                WorkflowRunModel.is_completed,
                WorkflowRunModel.answered_at,
                WorkflowRunModel.ended_at,
                WorkflowRunModel.billable_seconds,
                WorkflowRunModel.gathered_context,
            )
            .join(WorkflowModel, WorkflowModel.id == WorkflowRunModel.workflow_id)
            .where(
                WorkflowRunModel.created_at >= start,
                WorkflowRunModel.created_at < end,
                WorkflowRunModel.mode.in_(CARRIER_RUN_MODES),
            )
        )
    ).all()
    out = CallOutcomes(bad_by_org={})
    for org, completed, answered_at, ended_at, seconds, gathered in rows:
        run = SimpleNamespace(
            is_completed=bool(completed),
            answered_at=answered_at,
            ended_at=ended_at,
            billable_seconds=seconds,
            gathered_context=gathered or {},
        )
        evidence = call_evidence.classify(run)
        if evidence == call_evidence.ANSWERED:
            out.answered += 1
        elif evidence == call_evidence.NOT_CONNECTED:
            out.not_connected += 1
        elif evidence == call_evidence.CARRIER_FAILED:
            out.carrier_failed += 1
            out.bad_by_org[org] = out.bad_by_org.get(org, 0) + 1
        elif not completed:
            out.in_progress += 1
        else:
            out.unknown += 1
            out.bad_by_org[org] = out.bad_by_org.get(org, 0) + 1
    return out


async def run_counts(
    session: AsyncSession, start: datetime, end: datetime
) -> dict[str, int]:
    """Every run in the window by kind: carrier calls, browser calls, text."""
    rows = (
        await session.execute(
            select(WorkflowRunModel.mode, func.count(WorkflowRunModel.id))
            .where(
                WorkflowRunModel.created_at >= start, WorkflowRunModel.created_at < end
            )
            .group_by(WorkflowRunModel.mode)
        )
    ).all()
    out = {"carrier_calls": 0, "browser_calls": 0, "text": 0}
    for mode, count in rows:
        if mode in CARRIER_RUN_MODES:
            out["carrier_calls"] += int(count)
        elif mode in NON_VOICE_RUN_MODES:
            out["text"] += int(count)
        else:
            # Any other voice mode, including one added tomorrow, is a call.
            out["browser_calls"] += int(count)
    return out


async def active_organizations(
    session: AsyncSession, start: datetime, end: datetime
) -> int:
    """Workspaces that ran anything, or used a model, in the window."""
    from api.db.models import ModelUsageModel

    ran = (
        select(WorkflowModel.organization_id.label("org"))
        .join(WorkflowRunModel, WorkflowRunModel.workflow_id == WorkflowModel.id)
        .where(WorkflowRunModel.created_at >= start, WorkflowRunModel.created_at < end)
    )
    used = select(ModelUsageModel.organization_id.label("org")).where(
        ModelUsageModel.created_at >= start,
        ModelUsageModel.created_at < end,
        ModelUsageModel.organization_id.isnot(None),
    )
    both = ran.union(used).subquery()
    return int(
        await session.scalar(
            select(func.count(func.distinct(both.c.org))).where(both.c.org.isnot(None))
        )
        or 0
    )
