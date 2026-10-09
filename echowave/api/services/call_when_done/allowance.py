"""The daily call cap, reserved before the dial rather than counted after it.

At most ``CALL_WHEN_DONE_DAILY_CAP`` (the founder's 5) "call me when it's
done" calls ring one person on one of their local days, from every
workspace they belong to together: the cap protects the person, not a
workspace's budget.

It used to be a count of dialled rows read just before the dial. Two
workspaces placing a call for the same person at once could both read
"one left" and both ring. Now it is a reservation:

* ``reserve`` is one conditional upsert on ``person_call_allowances``
  (``used < cap``), in the same transaction that marks the call as holding
  the slot (``done_calls.allowance_day``). Postgres serialises the two
  writers on the allowance row, so at most ``cap`` reservations exist for a
  person's day however many workers race. A call that already holds a slot
  is not charged twice.
* A reservation counts from the moment it is made: a call being dialled, a
  call whose outcome is ``unknown`` and a call that rang all hold one.
* ``release`` gives the slot back **only on a verified non-dispatch**: the
  dial was refused before the provider was asked (no line, no concurrency
  slot, no quota), or the call has no run recorded, which ``dial_workflow``
  writes before it asks the provider (so nothing can have rung). An unknown
  outcome never releases: if it rang, it counted.

The day is the person's own (``person_timezone``: theirs, else the
workspace's), from midnight to midnight there. That is the founder's rule
for this cap; ``services/quotas`` deliberately uses the UTC day instead,
because a local day can be restarted by changing one's timezone. Here that
would buy at most one more day's calls to oneself, so the local day stands;
the reservation records the day it was taken in, so a timezone change
mid-day never frees a slot already held.

The provider's concurrency limit is separate and unchanged: it lives in
``dial_workflow`` (``call_concurrency``) and is per workspace, per moment;
this is per person, per day. A refusal for a busy line releases the slot.

Care's medicine reminder calls do not reserve here. Whether they share the
person's 5 is the founder's open decision (D3 in
docs/plans/reminder-calls.md); until then their behaviour is unchanged:
they are rung at the times the person confirmed on the card, uncapped.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

from sqlalchemy import select, text, update

from api import constants
from api.db import db_client
from api.db.call_when_done_models import DoneCallModel
from api.services.compliance import dnd


def cap() -> int:
    return max(0, constants.CALL_WHEN_DONE_DAILY_CAP)


def local_day(timezone_name: str | None, now: datetime) -> date:
    """The person's calendar day at ``now``."""
    return now.astimezone(dnd.resolve_zone(timezone_name)).date()


async def used(user_id: int, timezone_name: str | None, now: datetime) -> int:
    """Slots reserved for this person on their day at ``now``."""
    async with db_client.async_session() as session:
        value = await session.scalar(
            text(
                "SELECT used FROM person_call_allowances "
                "WHERE user_id = :u AND local_day = :d"
            ),
            {"u": user_id, "d": local_day(timezone_name, now)},
        )
    return int(value or 0)


async def remaining(user_id: int, timezone_name: str | None, now: datetime) -> int:
    return max(0, cap() - await used(user_id, timezone_name, now))


async def reserve(call_id: int, timezone_name: str | None, now: datetime) -> bool:
    """Take one of the person's slots for this call, atomically. True when
    the call holds a slot (already, or now); False when the day is full."""
    limit = cap()
    async with db_client.async_session() as session:
        call = (
            await session.execute(
                select(DoneCallModel.user_id, DoneCallModel.allowance_day)
                .where(DoneCallModel.id == call_id)
                .with_for_update()
            )
        ).first()
        if call is None:
            return False
        if call.allowance_day is not None:
            return True
        if limit <= 0:
            return False
        day = local_day(timezone_name, now)
        took = (
            await session.execute(
                text(
                    "INSERT INTO person_call_allowances (user_id, local_day, used, updated_at) "
                    "VALUES (:u, :d, 1, :now) "
                    "ON CONFLICT (user_id, local_day) DO UPDATE "
                    "SET used = person_call_allowances.used + 1, updated_at = :now "
                    "WHERE person_call_allowances.used < :cap "
                    "RETURNING used"
                ),
                {"u": call.user_id, "d": day, "now": now, "cap": limit},
            )
        ).first()
        if took is None:
            await session.rollback()
            return False
        await session.execute(
            update(DoneCallModel)
            .where(DoneCallModel.id == call_id)
            .values(allowance_day=day)
        )
        await session.commit()
    return True


async def release(call_id: int) -> bool:
    """Give back the slot this call holds, once. Only for a verified
    non-dispatch (see the module docstring). True if one was given back.

    Locks the call row first, then the allowance row, the same order as
    ``reserve``, so the two cannot deadlock."""
    async with db_client.async_session() as session:
        call = (
            await session.execute(
                select(DoneCallModel.user_id, DoneCallModel.allowance_day)
                .where(DoneCallModel.id == call_id)
                .with_for_update()
            )
        ).first()
        if call is None or call.allowance_day is None:
            return False
        await session.execute(
            text(
                "UPDATE person_call_allowances SET used = GREATEST(used - 1, 0), "
                "updated_at = :now WHERE user_id = :u AND local_day = :d"
            ),
            {"u": call.user_id, "d": call.allowance_day, "now": datetime.now(UTC)},
        )
        await session.execute(
            update(DoneCallModel)
            .where(DoneCallModel.id == call_id)
            .values(allowance_day=None)
        )
        await session.commit()
    return True
