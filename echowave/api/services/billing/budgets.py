"""Spend caps a customer sets on the workspace or on one agent (S-1).

The shape is paperclip's budget policy, in credits: a **scope** (the whole
workspace, or one agent), a **window** (this calendar month, or ever), an
**amount**, a **warning** at a share of it, and a **hard stop** at it. A cap is
the customer's own decision about their own money, which is why it is theirs
to set from the product and not an operator's rate to be effective-dated.

Two rules carry this module.

**Spend is read from the ledger, never counted beside the policy.** The usage
rows are what the balance is derived from; a counter kept on the policy would
drift from them the first time a recost, a retry or a manual adjustment touched
one and not the other, and a cap that disagrees with the statement is a cap
nobody trusts. ``credit_ledger.workflow_id`` is what makes an agent's spend one
indexed sum.

**A crossed threshold is a row, once per window.** ``budget_incidents`` is the
record a screen lists and a notification reads from; keyed on the policy, the
window and the threshold, so a warning fires once a month and not once a
charge, and a stop is a fact rather than a line in a log.

The check sits in the one place every run starts -- ``quota_service`` -- so a
call, a routine, a trigger, a channel reply and a task all meet the same cap
before any work is done. A charge that lands afterwards re-evaluates and opens
the incident, which is what makes the *next* start refuse.

Nothing here reads a row until ``BUDGET_POLICIES_ENABLED`` is on.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from loguru import logger
from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from api import constants
from api.db.models import (
    BudgetIncidentModel,
    BudgetPolicyModel,
    CreditLedgerModel,
    WorkflowModel,
)
from api.enums import CreditLedgerKind
from api.services.billing.credits import PAISE_PER_CREDIT

WINDOW_CALENDAR_MONTH = "calendar_month"
WINDOW_LIFETIME = "lifetime"
WINDOW_KINDS = (WINDOW_CALENDAR_MONTH, WINDOW_LIFETIME)

THRESHOLD_WARN = "warn"
THRESHOLD_HARD = "hard"

STATUS_OPEN = "open"
STATUS_RESOLVED = "resolved"
STATUS_DISMISSED = "dismissed"

#: Plan cycles and the builder's allowance turn over on Indian calendar
#: months; a cap that turned over on a different midnight would be the one
#: thing on the statement that did.
IST = ZoneInfo("Asia/Kolkata")

#: The refusal a run gets. Named so the runners' "could not run" event and the
#: start screen can tell it from an empty balance.
ERROR_CODE = "budget_exhausted"


class BudgetError(ValueError):
    """A policy could not be written as asked."""


def enabled() -> bool:
    return constants.BUDGET_POLICIES_ENABLED


def window_bounds(
    kind: str, *, at: datetime, opened_at: datetime | None = None
) -> tuple[datetime, datetime | None]:
    """The window ``at`` falls in: ``[start, end)``, ``end`` None for ever.

    A lifetime window starts when the policy did, so a cap set today is not
    already spent by last year.
    """
    if kind == WINDOW_LIFETIME:
        return (opened_at or datetime(1970, 1, 1, tzinfo=UTC)), None
    if kind != WINDOW_CALENDAR_MONTH:
        raise BudgetError(f"unknown window kind {kind!r}")
    local = at.astimezone(IST)
    start = local.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    end = (start + timedelta(days=32)).replace(day=1)
    return start.astimezone(UTC), end.astimezone(UTC)


async def spend_paise(
    session: AsyncSession,
    *,
    organization_id: int,
    workflow_id: int | None,
    start: datetime,
    end: datetime | None,
) -> int:
    """Usage debited in the window, for the workspace or for one agent."""
    query = select(func.coalesce(func.sum(-CreditLedgerModel.delta_paise), 0)).where(
        CreditLedgerModel.organization_id == organization_id,
        CreditLedgerModel.kind == CreditLedgerKind.USAGE.value,
        CreditLedgerModel.created_at >= start,
    )
    if end is not None:
        query = query.where(CreditLedgerModel.created_at < end)
    if workflow_id is not None:
        query = query.where(CreditLedgerModel.workflow_id == workflow_id)
    return int(await session.scalar(query) or 0)


@dataclass(frozen=True)
class Standing:
    """One policy measured against its window."""

    policy: BudgetPolicyModel
    window_start: datetime
    window_end: datetime | None
    observed_paise: int

    @property
    def limit_paise(self) -> int:
        return int(self.policy.amount_paise)

    @property
    def warn_at_paise(self) -> int:
        return (self.limit_paise * int(self.policy.warn_percent)) // 100

    @property
    def exhausted(self) -> bool:
        return self.observed_paise >= self.limit_paise

    @property
    def warned(self) -> bool:
        return self.observed_paise >= self.warn_at_paise

    @property
    def stops(self) -> bool:
        return self.exhausted and bool(self.policy.hard_stop)


@dataclass(frozen=True)
class Verdict:
    allowed: bool = True
    #: The policy that refused, when one did.
    stopped_by: Standing | None = None
    standings: tuple[Standing, ...] = field(default_factory=tuple)

    @property
    def message(self) -> str:
        if self.stopped_by is None:
            return ""
        s = self.stopped_by
        scope = "this agent" if s.policy.workflow_id is not None else "the workspace"
        window = (
            "this month" if s.policy.window_kind == WINDOW_CALENDAR_MONTH else "in all"
        )
        return (
            f"The spend cap on {scope} is used up: {s.observed_paise // PAISE_PER_CREDIT}"
            f" of {s.limit_paise // PAISE_PER_CREDIT} credits {window}. "
            "Raise the cap or wait for the next window."
        )


async def policies_for(
    session: AsyncSession, *, organization_id: int, workflow_id: int | None
) -> list[BudgetPolicyModel]:
    """The live policies a run in this scope is subject to: the workspace's,
    and the agent's own."""
    scope = BudgetPolicyModel.workflow_id.is_(None)
    if workflow_id is not None:
        scope = or_(scope, BudgetPolicyModel.workflow_id == workflow_id)
    return list(
        (
            await session.scalars(
                select(BudgetPolicyModel)
                .where(
                    BudgetPolicyModel.organization_id == organization_id,
                    BudgetPolicyModel.is_active.is_(True),
                    scope,
                )
                .order_by(
                    BudgetPolicyModel.workflow_id.nulls_first(), BudgetPolicyModel.id
                )
            )
        ).all()
    )


async def evaluate(
    session: AsyncSession,
    *,
    organization_id: int,
    workflow_id: int | None,
    at: datetime | None = None,
    record: bool = True,
) -> Verdict:
    """Measure every policy the scope is under; open or resolve incidents.

    ``allowed`` is False when a hard-stop policy is at its cap. A warning
    never refuses. With the flag off nothing is read and the run is allowed.
    """
    if not enabled():
        return Verdict()
    at = at or datetime.now(UTC)
    standings: list[Standing] = []
    stopped: Standing | None = None
    for policy in await policies_for(
        session, organization_id=organization_id, workflow_id=workflow_id
    ):
        start, end = window_bounds(
            policy.window_kind, at=at, opened_at=policy.created_at
        )
        observed = await spend_paise(
            session,
            organization_id=organization_id,
            workflow_id=policy.workflow_id,
            start=start,
            end=end,
        )
        standing = Standing(policy, start, end, observed)
        standings.append(standing)
        if record:
            await _reconcile_incidents(session, standing)
        if standing.stops and stopped is None:
            stopped = standing
    return Verdict(
        allowed=stopped is None, stopped_by=stopped, standings=tuple(standings)
    )


async def _reconcile_incidents(session: AsyncSession, standing: Standing) -> None:
    """Open the incident a crossed threshold earns; resolve one it no longer
    does (a raised cap, a reversed charge)."""
    for threshold, crossed in (
        (THRESHOLD_WARN, standing.warned),
        (THRESHOLD_HARD, standing.exhausted),
    ):
        existing = await session.scalar(
            select(BudgetIncidentModel)
            .where(
                BudgetIncidentModel.policy_id == standing.policy.id,
                BudgetIncidentModel.window_start == standing.window_start,
                BudgetIncidentModel.threshold == threshold,
            )
            .order_by(BudgetIncidentModel.id.desc())
        )
        if existing is not None and existing.status == STATUS_DISMISSED:
            # A person waved this window's warning away; the next charge in
            # the same window is not news. The next window is.
            continue
        if crossed and existing is None:
            session.add(
                BudgetIncidentModel(
                    organization_id=standing.policy.organization_id,
                    policy_id=standing.policy.id,
                    workflow_id=standing.policy.workflow_id,
                    threshold=threshold,
                    window_start=standing.window_start,
                    window_end=standing.window_end,
                    amount_limit_paise=standing.limit_paise,
                    amount_observed_paise=standing.observed_paise,
                )
            )
            logger.info(
                "Budget {} on policy {} (org {}, agent {}): {} of {} paise",
                threshold,
                standing.policy.id,
                standing.policy.organization_id,
                standing.policy.workflow_id,
                standing.observed_paise,
                standing.limit_paise,
            )
        elif crossed and existing is not None and existing.status == STATUS_RESOLVED:
            existing.status = STATUS_OPEN
            existing.resolved_at = None
            existing.amount_observed_paise = standing.observed_paise
        elif crossed and existing is not None:
            existing.amount_observed_paise = standing.observed_paise
        elif not crossed and existing is not None and existing.status == STATUS_OPEN:
            existing.status = STATUS_RESOLVED
            existing.resolved_at = datetime.now(UTC)
    await session.flush()


async def evaluate_in_own_session(
    *, organization_id: int | None, workflow_id: int | None
) -> Verdict:
    """The start-of-run check, from code that holds no session.

    Fails **open**: a database error here is "we could not read the cap",
    and refusing every run on the platform because one query failed is a
    worse outcome than one run past a cap. The charge that follows still
    lands and opens the incident.
    """
    if not enabled() or not organization_id:
        return Verdict()
    try:
        from api.db import db_client

        async with db_client.async_session() as session:
            verdict = await evaluate(
                session, organization_id=organization_id, workflow_id=workflow_id
            )
            await session.commit()
            return verdict
    except Exception as exc:  # noqa: BLE001 - see the docstring
        logger.error(
            "Could not read budget policies for org {}; allowing the run: {}",
            organization_id,
            exc,
        )
        return Verdict()


async def observe_charge(
    session: AsyncSession, *, organization_id: int, workflow_id: int | None
) -> None:
    """After a debit: re-measure and open whatever incident it earned. Never
    raises -- a cap is bookkeeping, and the charge it follows stands."""
    if not enabled():
        return
    try:
        await evaluate(
            session, organization_id=organization_id, workflow_id=workflow_id
        )
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "Could not evaluate budget policies for org {} after a charge: {}",
            organization_id,
            exc,
        )


# ---------------------------------------------------------------------------
# What the product edits
# ---------------------------------------------------------------------------


async def _agent_in_org(
    session: AsyncSession, *, organization_id: int, workflow_id: int
) -> None:
    owner = await session.scalar(
        select(WorkflowModel.organization_id).where(WorkflowModel.id == workflow_id)
    )
    if owner != organization_id:
        raise BudgetError("That agent is not in this workspace")


async def list_policies(
    session: AsyncSession, *, organization_id: int
) -> list[BudgetPolicyModel]:
    return list(
        (
            await session.scalars(
                select(BudgetPolicyModel)
                .where(
                    BudgetPolicyModel.organization_id == organization_id,
                    BudgetPolicyModel.is_active.is_(True),
                )
                .order_by(
                    BudgetPolicyModel.workflow_id.nulls_first(), BudgetPolicyModel.id
                )
            )
        ).all()
    )


async def set_policy(
    session: AsyncSession,
    *,
    organization_id: int,
    workflow_id: int | None,
    window_kind: str,
    amount_credits: int,
    warn_percent: int = 80,
    hard_stop: bool = True,
    created_by: int | None = None,
) -> BudgetPolicyModel:
    """Create the policy for a scope and window, or edit the live one.

    One live policy per scope and window (the unique indexes hold it), so
    "set" is the only verb the product needs: a second call on the same
    scope edits rather than duplicates.
    """
    if window_kind not in WINDOW_KINDS:
        raise BudgetError(f"window_kind must be one of {', '.join(WINDOW_KINDS)}")
    if amount_credits < 0:
        raise BudgetError("amount_credits must not be negative")
    if not 0 <= warn_percent <= 100:
        raise BudgetError("warn_percent must be between 0 and 100")
    if workflow_id is not None:
        await _agent_in_org(
            session, organization_id=organization_id, workflow_id=workflow_id
        )
    existing = await session.scalar(
        select(BudgetPolicyModel).where(
            BudgetPolicyModel.organization_id == organization_id,
            BudgetPolicyModel.workflow_id.is_(None)
            if workflow_id is None
            else BudgetPolicyModel.workflow_id == workflow_id,
            BudgetPolicyModel.window_kind == window_kind,
            BudgetPolicyModel.is_active.is_(True),
        )
    )
    amount_paise = amount_credits * PAISE_PER_CREDIT
    if existing is not None:
        existing.amount_paise = amount_paise
        existing.warn_percent = warn_percent
        existing.hard_stop = hard_stop
        existing.updated_at = datetime.now(UTC)
        policy = existing
    else:
        policy = BudgetPolicyModel(
            organization_id=organization_id,
            workflow_id=workflow_id,
            window_kind=window_kind,
            amount_paise=amount_paise,
            warn_percent=warn_percent,
            hard_stop=hard_stop,
            created_by=created_by,
        )
        session.add(policy)
    await session.flush()
    # A raised cap resolves the stop it lifted; a lowered one opens it now
    # rather than at the next charge.
    await evaluate(session, organization_id=organization_id, workflow_id=workflow_id)
    return policy


async def remove_policy(
    session: AsyncSession, *, organization_id: int, policy_id: int
) -> bool:
    """Retire a policy. Its incidents stay: they happened."""
    policy = await session.scalar(
        select(BudgetPolicyModel).where(
            BudgetPolicyModel.id == policy_id,
            BudgetPolicyModel.organization_id == organization_id,
            BudgetPolicyModel.is_active.is_(True),
        )
    )
    if policy is None:
        return False
    policy.is_active = False
    policy.updated_at = datetime.now(UTC)
    await session.execute(
        update(BudgetIncidentModel)
        .where(
            BudgetIncidentModel.policy_id == policy.id,
            BudgetIncidentModel.status == STATUS_OPEN,
        )
        .values(status=STATUS_RESOLVED, resolved_at=datetime.now(UTC))
    )
    await session.flush()
    return True


async def list_incidents(
    session: AsyncSession, *, organization_id: int, open_only: bool = True
) -> list[BudgetIncidentModel]:
    query = select(BudgetIncidentModel).where(
        BudgetIncidentModel.organization_id == organization_id
    )
    if open_only:
        query = query.where(BudgetIncidentModel.status == STATUS_OPEN)
    return list(
        (await session.scalars(query.order_by(BudgetIncidentModel.id.desc()))).all()
    )


async def dismiss_incident(
    session: AsyncSession, *, organization_id: int, incident_id: int
) -> bool:
    incident = await session.scalar(
        select(BudgetIncidentModel).where(
            BudgetIncidentModel.id == incident_id,
            BudgetIncidentModel.organization_id == organization_id,
        )
    )
    if incident is None:
        return False
    incident.status = STATUS_DISMISSED
    incident.resolved_at = datetime.now(UTC)
    await session.flush()
    return True
