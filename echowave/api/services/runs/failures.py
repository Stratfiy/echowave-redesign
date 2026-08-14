"""Recording why a call did not happen, or did not finish.

The value of this is entirely in it being written *everywhere* a call goes
wrong. A field populated on three of eight failure paths is worse than no
field, because the breakdown then reads as though the two loudest causes are
the only ones and the rest of the fleet looks healthy.

So the writing is one function, deliberately forgiving:

**It never raises.** A call that already failed must not fail differently
because recording why it failed went wrong. Every caller is on an error path,
and an exception there replaces a diagnosable failure with an undiagnosable
one.

**It never overwrites.** The first reason is the true one. A call refused for
no credit that then also loses its websocket should read as "no credit" — the
second failure is a consequence of the first, and letting it win would blame
the symptom.
"""

from __future__ import annotations

from loguru import logger
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from api.db.models import WorkflowRunModel
from api.enums import RunFailureReason

#: The external error codes we already emit, mapped onto the vocabulary. Kept
#: here rather than at each call site so a code that gains a better home moves
#: in one place, and an unmapped code lands in UNKNOWN rather than being
#: silently dropped.
_FROM_ERROR_CODE = {
    "insufficient_credit": RunFailureReason.INSUFFICIENT_CREDIT,
    "quota_check_failed": RunFailureReason.PLATFORM_ERROR,
    "concurrency_limit": RunFailureReason.CONCURRENCY_LIMIT,
    "dnd_listed": RunFailureReason.DND_LISTED,
    "outside_calling_hours": RunFailureReason.OUTSIDE_CALLING_HOURS,
    "no_verified_number": RunFailureReason.NO_VERIFIED_NUMBER,
}


def reason_for_error_code(code: str | None) -> RunFailureReason:
    """Map an existing error code onto the vocabulary.

    Unknown codes become UNKNOWN rather than PLATFORM_ERROR: claiming a fault
    is ours when we have not classified it is how a breakdown stops being
    trusted.
    """
    return _FROM_ERROR_CODE.get((code or "").strip(), RunFailureReason.UNKNOWN)


async def record(
    session: AsyncSession,
    *,
    workflow_run_id: int,
    reason: RunFailureReason | str,
    detail: str | None = None,
) -> None:
    """Record why a run failed, if it does not already have a reason.

    Never raises, and never overwrites. See the module docstring for why both
    of those are the point rather than laziness.
    """
    value = reason.value if isinstance(reason, RunFailureReason) else str(reason)
    try:
        await session.execute(
            update(WorkflowRunModel)
            .where(
                WorkflowRunModel.id == workflow_run_id,
                # The first reason wins. A second failure on an already-failed
                # call is a consequence, and letting it overwrite blames the
                # symptom instead of the cause.
                WorkflowRunModel.failure_reason.is_(None),
            )
            .values(
                failure_reason=value,
                # Truncated rather than refused: a provider stack trace is not
                # worth failing a write that exists to explain a failure.
                failure_detail=(detail or None) and str(detail)[:2000],
            )
        )
    except Exception:
        logger.warning(
            "Could not record failure reason {} for run {}",
            value,
            workflow_run_id,
            exc_info=True,
        )
