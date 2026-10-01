"""The other half of the impersonation audit (KAN-82, ADMIN-2 A4).

``POST /superuser/impersonate`` writes ``impersonation_started``. Nothing
wrote the end, so the log could say who borrowed an account and when, but not
for how long.

**Who can report the end.** Starting an impersonation replaces the staffer's
own session in the browser with the customer's (``ui/src/app/impersonate``),
so when "Stop impersonating" is pressed the only credential present is the
*borrowed* one. That request cannot pass ``get_superuser``. It is therefore
authenticated as the impersonated user, and it can only close an
impersonation that actually exists:

* there must be an ``impersonation_started`` row naming this user, inside the
  borrowed session's lifetime (one hour, ``stack_auth.impersonate``);
* it must not already have a stop after it;
* the stop row is attributed to the staffer from the start row, never to the
  caller.

So the worst a customer who found this endpoint could do is record, early,
the end of a session staff opened on their own account -- the start row stays,
and the session still expires on its own an hour after it began.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.db.models import AdminActionLogModel

STARTED = "impersonation_started"
STOPPED = "impersonation_stopped"

#: The borrowed session's life (``expires_in_millis`` in stack_auth.py). A
#: start older than this has already ended on its own.
SESSION_LIFETIME = timedelta(hours=1)


async def record_stop(
    session: AsyncSession,
    *,
    user_id: int,
    provider_id: str | None,
    actor_ip: str | None,
    now: datetime | None = None,
) -> dict:
    """Write ``impersonation_stopped`` for the open impersonation of this
    user, if there is one. Idempotent: a second call finds it closed."""
    now = now or datetime.now(UTC)
    log = AdminActionLogModel
    target = log.target_user_id == user_id
    if provider_id:
        target = or_(target, log.target_provider_id == provider_id)

    start = (
        await session.scalars(
            select(log)
            .where(
                log.action == STARTED,
                target,
                log.created_at >= now - SESSION_LIFETIME,
            )
            .order_by(log.created_at.desc(), log.id.desc())
            .limit(1)
        )
    ).first()
    if start is None:
        return {"recorded": False, "reason": "no_open_impersonation"}

    already = (
        await session.scalars(
            select(log.id)
            .where(
                log.action == STOPPED,
                target,
                log.created_at >= start.created_at,
            )
            .limit(1)
        )
    ).first()
    if already is not None:
        return {"recorded": False, "reason": "already_stopped"}

    minutes = max(0, int((now - start.created_at).total_seconds() // 60))
    session.add(
        AdminActionLogModel(
            actor_user_id=start.actor_user_id,
            action=STOPPED,
            target_user_id=start.target_user_id or user_id,
            target_provider_id=start.target_provider_id or provider_id,
            target_organization_id=start.target_organization_id,
            actor_ip=actor_ip,
            note=(
                f"after {minutes} min; started {start.created_at.isoformat()} "
                f"(start #{start.id})"
            )[:500],
            created_at=now,
        )
    )
    await session.commit()
    return {
        "recorded": True,
        "started_at": start.created_at.isoformat(),
        "stopped_at": now.isoformat(),
        "minutes": minutes,
    }
