"""What a share link may spend, and whether it has spent it.

A share link is the agent handed to a stranger: a prospect, a client's boss,
a group chat. Every conversation on it is paid from the owner's credits, so
the link carries two limits the website widget does not: minutes a day and
an expiry. Both default to modest figures the owner can raise, and the link
has a switch. Without those, one forwarded link is a balance gone overnight
and a ticket asking why.

The day is the UTC day. A cap is a ceiling on exposure, not an accounting
period, and one day boundary somewhere in the night is enough for that; the
owner's timezone would make the same link reset at different moments for
different owners of the same agent.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.db.models import EmbedSessionModel, WorkflowRunModel

#: What a fresh share link gets. Thirty minutes is a dozen demo calls; thirty
#: days outlives any sales cycle a link was made for.
DEFAULT_DAILY_MINUTES = 30
DEFAULT_EXPIRY_DAYS = 30
#: The most an owner can set from the dialog. A whole day of calling is the
#: point past which "a cap" stops meaning anything.
MAX_DAILY_MINUTES = 24 * 60

#: A session that has started and not ended is charged its elapsed time, so
#: ten tabs opened at once cannot each see an empty day. A session older than
#: this without a recorded duration is one the pipeline never closed out, and
#: is not still spending.
IN_FLIGHT_HORIZON = timedelta(hours=1)


class LinkCapReached(Exception):
    """The link has used its minutes for today."""

    def __init__(self, cap_minutes: int):
        self.cap_minutes = cap_minutes
        super().__init__(
            "This link has used its minutes for today. Try again tomorrow, or "
            "ask whoever shared it to raise the limit."
        )


@dataclass(frozen=True)
class LinkUsage:
    cap_minutes: int | None
    seconds_used_today: int

    @property
    def minutes_used_today(self) -> int:
        return (self.seconds_used_today + 59) // 60

    @property
    def remaining_seconds(self) -> int | None:
        """Seconds the link may still start today, or None when uncapped."""
        if self.cap_minutes is None:
            return None
        return max(0, self.cap_minutes * 60 - self.seconds_used_today)

    @property
    def is_exhausted(self) -> bool:
        return self.remaining_seconds == 0


def _day_start(now: datetime) -> datetime:
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


async def seconds_used_today(
    session: AsyncSession, *, embed_token_id: int, now: datetime | None = None
) -> int:
    """Seconds of calling this token has started since midnight UTC.

    Ended calls contribute their billable seconds. A call still running
    contributes the time since it started, because the cap exists to bound
    what a stranger can spend and a stranger's call is spending while it
    runs.
    """
    now = now or datetime.now(UTC)
    since = _day_start(now)
    rows = (
        await session.execute(
            select(
                WorkflowRunModel.billable_seconds,
                WorkflowRunModel.ended_at,
                WorkflowRunModel.created_at,
            )
            .join(
                EmbedSessionModel,
                EmbedSessionModel.workflow_run_id == WorkflowRunModel.id,
            )
            .where(
                EmbedSessionModel.embed_token_id == embed_token_id,
                EmbedSessionModel.created_at >= since,
            )
        )
    ).all()
    total = 0
    for billable, ended_at, created_at in rows:
        if billable is not None:
            total += int(billable)
        elif ended_at is None and created_at is not None:
            started = (
                created_at if created_at.tzinfo else created_at.replace(tzinfo=UTC)
            )
            elapsed = now - started
            if elapsed <= IN_FLIGHT_HORIZON:
                total += int(elapsed.total_seconds())
    return total


async def usage_for(
    session: AsyncSession, *, embed_token_id: int, cap_minutes: int | None
) -> LinkUsage:
    if cap_minutes is None:
        return LinkUsage(cap_minutes=None, seconds_used_today=0)
    used = await seconds_used_today(session, embed_token_id=embed_token_id)
    return LinkUsage(cap_minutes=cap_minutes, seconds_used_today=used)


async def assert_within_cap(
    session: AsyncSession, *, embed_token_id: int, cap_minutes: int | None
) -> LinkUsage:
    """Raise :class:`LinkCapReached` if the link has nothing left today."""
    usage = await usage_for(
        session, embed_token_id=embed_token_id, cap_minutes=cap_minutes
    )
    if usage.is_exhausted:
        raise LinkCapReached(usage.cap_minutes or 0)
    return usage
