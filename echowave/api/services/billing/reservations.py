"""Holding funds while a call is in flight.

This is the other half of prepaid. Taking money in is
``api/services/billing/payments.py``; this module is what stops an account
spending money it does not have.

A balance check on its own does not work, and the reason is worth stating
plainly because it is the entire justification for this file. A call's cost is
not known until it ends. Two calls starting in the same instant both read the
same balance, both find it sufficient, and both proceed — and an account with
₹10 on it can start fifty concurrent calls, each of which passed the check.
Concurrency is exactly what a voice platform sells, so this is not a corner
case.

So a call **reserves** an estimate before it starts. The reservation is an
ordinary negative ledger row, which means it counts against the balance from
the moment it is written — no separate "held" column that the balance query
could forget to subtract. When the call is costed the reservation is released
and replaced by the real usage debit.

Three things follow from that:

1. **Reservations are taken under a per-organization row lock.** Without it two
   concurrent starts can still interleave their read and write, which is the
   bug this module exists to prevent.
2. **A reservation is an estimate, so it is never the charge.** It is released
   in full at costing and the actual cost debited separately. An account is
   billed for what it used, never for what we guessed.
3. **Stale reservations must be swept.** A worker that dies mid-call leaves
   funds held against a call that will never be costed. Without a sweeper that
   is a slow leak that ends with a paying customer unable to place a call.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from loguru import logger
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.constants import (
    BALANCE_ENFORCEMENT_ENABLED,
    MIN_BALANCE_PAISE,
    RESERVATION_MAX_AGE_MINUTES,
    RESERVATION_MINUTES,
)
from api.db.models import (
    CreditLedgerModel,
    DailyOrganizationRollupModel,
    OrganizationModel,
    WorkflowRunModel,
)
from api.enums import CreditLedgerKind
from api.services.billing.money import MPAISE_PER_PAISE, round_half_up_div

REF_TYPE = "workflow_run"

#: How far back to look when working out what a minute costs this account.
#: Long enough to be stable, short enough to follow a stack change.
COST_HISTORY_DAYS = 30

#: Multiplier applied to the platform fee when an account has no history to
#: measure. Provider cost is passed through at cost and on a typical Indian
#: voice stack runs a little under the platform fee itself, so twice the
#: platform rate is a deliberately generous ceiling for a first call. Being
#: generous here is the safe direction: it over-reserves on call one and is
#: corrected by measurement from call two onwards.
NO_HISTORY_MULTIPLIER = 2


@dataclass(frozen=True)
class Reservation:
    """Funds held against one in-flight call."""

    ledger_id: int
    amount_paise: int
    balance_after_paise: int


async def current_balance_paise(session: AsyncSession, *, organization_id: int) -> int:
    """Balance including any funds currently held.

    Re-exported from the costing module so that reservation code has one
    obvious place to ask, and so nobody is tempted to write a balance query
    here that forgets to count reservations.
    """
    from api.services.billing.costing import current_balance_paise as _balance

    return await _balance(session, organization_id=organization_id)


async def _cost_per_minute_paise(
    session: AsyncSession, *, organization_id: int, at: datetime
) -> int | None:
    """What a connected minute has actually cost this account recently.

    Read from the pre-aggregated rollup rather than by scanning receipts: this
    sits on the critical path of every call start, and a table scan there would
    be paid on every single call.

    Returns None when there is not enough history to divide by.
    """
    since = (at - timedelta(days=COST_HISTORY_DAYS)).date()
    row = (
        await session.execute(
            select(
                func.coalesce(func.sum(DailyOrganizationRollupModel.charged_paise), 0),
                func.coalesce(
                    func.sum(DailyOrganizationRollupModel.billable_seconds), 0
                ),
            ).where(
                DailyOrganizationRollupModel.organization_id == organization_id,
                DailyOrganizationRollupModel.day >= since,
            )
        )
    ).one()

    charged_paise, billable_seconds = int(row[0] or 0), int(row[1] or 0)
    if billable_seconds <= 0 or charged_paise <= 0:
        return None
    return round_half_up_div(charged_paise * 60, billable_seconds)


async def estimate_paise(
    session: AsyncSession, *, organization_id: int, at: datetime | None = None
) -> int:
    """What to hold for one call.

    Deliberately an estimate of a *typical* call rather than a worst case. A
    reservation sized for the longest call anyone might make would stop an
    account with real credit from running the calls it is paying for, which is
    a worse failure than briefly under-holding on an unusually long one — the
    ledger still ends up exact either way, because the reservation is released
    and the true cost debited.
    """
    at = at or datetime.now(UTC)

    per_minute = await _cost_per_minute_paise(
        session, organization_id=organization_id, at=at
    )
    if per_minute is None:
        # No history. Price the hold off the account's own platform rate rather
        # than a flat constant, so an enterprise on a negotiated rate is not
        # measured against someone else's.
        from api.services.billing.rates import resolve_platform_rate

        platform = await resolve_platform_rate(
            session, organization_id=organization_id, at=at, period_minutes=0
        )
        platform_paise_per_minute = round_half_up_div(
            platform.rate_mpaise, MPAISE_PER_PAISE
        )
        per_minute = platform_paise_per_minute * NO_HISTORY_MULTIPLIER

    return max(1, per_minute * RESERVATION_MINUTES)


async def _existing_reservation(
    session: AsyncSession, *, organization_id: int, workflow_run_id: int
) -> CreditLedgerModel | None:
    return await session.scalar(
        select(CreditLedgerModel).where(
            CreditLedgerModel.organization_id == organization_id,
            CreditLedgerModel.kind == CreditLedgerKind.RESERVATION.value,
            CreditLedgerModel.ref_type == REF_TYPE,
            CreditLedgerModel.ref_id == str(workflow_run_id),
        )
    )


async def has_credit(
    session: AsyncSession,
    *,
    organization_id: int,
    workflow_run_id: int | None = None,
) -> bool:
    """Cheap pre-check, used to refuse an unfunded account without locking.

    Not authoritative — :func:`reserve` re-checks under a lock. This exists so
    the common refusal costs one indexed sum rather than a row lock and an
    external round-trip we would only throw away.

    **A run that already holds funds passes**, whatever the balance says. Every
    runtime entrypoint can re-authorize the same run — a websocket reconnect, an
    ARI retry — and by then the hold it took has usually driven the balance to
    exactly the point where a naive check refuses it. Refusing a call that is
    already paid for is the worst possible answer: the customer paid and the
    call still dropped.
    """
    if not BALANCE_ENFORCEMENT_ENABLED:
        return True

    if workflow_run_id is not None:
        held = await _existing_reservation(
            session, organization_id=organization_id, workflow_run_id=workflow_run_id
        )
        if held is not None:
            return True

    balance = await current_balance_paise(session, organization_id=organization_id)
    return balance >= MIN_BALANCE_PAISE


async def reserve(
    session: AsyncSession,
    *,
    organization_id: int,
    workflow_run_id: int,
    at: datetime | None = None,
) -> Reservation | None:
    """Hold funds for one call, or return None if the account cannot pay.

    Serialized per organization with ``SELECT ... FOR UPDATE`` on the
    organization row. Two calls starting together would otherwise both read the
    balance before either wrote its reservation, and both would be allowed —
    which is the whole failure this module exists to prevent, so the lock is
    load-bearing rather than defensive.

    The lock is per-organization, so it only ever contends with another call
    starting on the *same* account at the same moment. That is precisely the
    set of operations that must not interleave.

    **Holds ``min(estimate, balance)``.** An account above the floor gets to
    make a call even when its balance is less than a full estimate's worth;
    refusing at "less than a full estimate" would strand credit customers have
    paid for.

    **Refuses below ``MIN_BALANCE_PAISE`` rather than below zero.** A call's
    cost is not known until it ends, so an account allowed to start on its last
    rupee finishes overdrawn — and the floor is what makes the last call one it
    could always afford. The floor is a gate, not a reserve: a call already
    running may take the balance below it, and the account simply cannot start
    another until it tops up.
    """
    if not BALANCE_ENFORCEMENT_ENABLED:
        return None

    at = at or datetime.now(UTC)

    # Idempotent: a retried start must not stack a second hold on one call.
    existing = await _existing_reservation(
        session, organization_id=organization_id, workflow_run_id=workflow_run_id
    )
    if existing is not None:
        return Reservation(
            ledger_id=existing.id,
            amount_paise=-int(existing.delta_paise),
            balance_after_paise=int(existing.balance_after_paise),
        )

    # Serialize against any other call starting on this account right now.
    await session.execute(
        select(OrganizationModel.id)
        .where(OrganizationModel.id == organization_id)
        .with_for_update()
    )

    balance = await current_balance_paise(session, organization_id=organization_id)
    if balance < MIN_BALANCE_PAISE:
        logger.info(
            "Refusing workflow run {} for org {}: balance is {} paise, floor is {}",
            workflow_run_id,
            organization_id,
            balance,
            MIN_BALANCE_PAISE,
        )
        return None

    amount = min(
        await estimate_paise(session, organization_id=organization_id, at=at),
        balance,
    )

    entry = CreditLedgerModel(
        organization_id=organization_id,
        delta_paise=-amount,
        kind=CreditLedgerKind.RESERVATION.value,
        ref_type=REF_TYPE,
        ref_id=str(workflow_run_id),
        balance_after_paise=balance - amount,
        note=f"Held for run {workflow_run_id}",
    )
    session.add(entry)
    await session.flush()

    logger.debug(
        "Reserved {} paise for run {} on org {} (balance now {})",
        amount,
        workflow_run_id,
        organization_id,
        balance - amount,
    )
    return Reservation(
        ledger_id=entry.id,
        amount_paise=amount,
        balance_after_paise=balance - amount,
    )


async def release(
    session: AsyncSession, *, organization_id: int, workflow_run_id: int
) -> int:
    """Give back whatever was held for a run. Returns the amount released.

    Deletes the row rather than writing a compensating credit. A reservation is
    not a transaction the customer should ever see — showing a hold and its
    reversal on a statement invites "why was I charged twice?" for something
    that never charged them once.

    Safe to call for a run that has no reservation, which is the normal case
    for anything costed before enforcement was switched on.
    """
    row = await _existing_reservation(
        session, organization_id=organization_id, workflow_run_id=workflow_run_id
    )
    if row is None:
        return 0

    amount = -int(row.delta_paise)
    await session.delete(row)
    await session.flush()
    return amount


async def sweep_stale(
    session: AsyncSession, *, now: datetime | None = None
) -> tuple[int, int]:
    """Release holds against calls that will never be costed.

    Two ways a reservation is stranded, and both leak real spending power:

    * the run finished and was costed, but the release did not happen — a
      crash between the two writes, or a run costed before enforcement existed;
    * the worker handling the call died, so the run never completed at all.

    The age cut-off handles the second. It has to be longer than the longest
    call anyone plausibly makes, because sweeping a *live* call's hold would
    put the account back over its balance while it is still spending.

    Returns (rows released, paise released).
    """
    now = now or datetime.now(UTC)
    cutoff = now - timedelta(minutes=RESERVATION_MAX_AGE_MINUTES)

    reservations = (
        await session.scalars(
            select(CreditLedgerModel).where(
                CreditLedgerModel.kind == CreditLedgerKind.RESERVATION.value,
                CreditLedgerModel.ref_type == REF_TYPE,
                CreditLedgerModel.ref_id.is_not(None),
            )
        )
    ).all()
    if not reservations:
        return (0, 0)

    run_ids = {int(r.ref_id) for r in reservations if str(r.ref_id).isdigit()}
    costed: set[int] = set()
    if run_ids:
        costed = {
            row[0]
            for row in (
                await session.execute(
                    select(WorkflowRunModel.id).where(
                        WorkflowRunModel.id.in_(run_ids),
                        WorkflowRunModel.costed_at.is_not(None),
                    )
                )
            ).all()
        }

    stale_ids: list[int] = []
    released_paise = 0
    for row in reservations:
        run_id = int(row.ref_id) if str(row.ref_id).isdigit() else None
        created_at = row.created_at
        too_old = created_at is not None and created_at < cutoff
        if (run_id in costed) or too_old:
            stale_ids.append(row.id)
            released_paise += -int(row.delta_paise)

    if not stale_ids:
        return (0, 0)

    await session.execute(
        delete(CreditLedgerModel).where(CreditLedgerModel.id.in_(stale_ids))
    )
    await session.flush()

    logger.info(
        "Released {} stale reservation(s) worth {} paise",
        len(stale_ids),
        released_paise,
    )
    return (len(stale_ids), released_paise)
