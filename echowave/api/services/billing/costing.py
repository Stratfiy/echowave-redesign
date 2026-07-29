"""Cost a completed call and persist its receipt.

Called after a run completes. Resolves the rates that were in force at call
time, computes the itemised cost, writes the line items, snapshots the totals
onto the run, and debits the credit ledger.

Costing is **idempotent**: a run that already carries ``costed_at`` is left
alone unless explicitly recosted. Post-call work is retried, and a retry must
not double-charge.
"""

from __future__ import annotations

from datetime import UTC, datetime

from loguru import logger
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.db.models import (
    CallCostItemModel,
    CreditLedgerModel,
    WorkflowModel,
    WorkflowRunModel,
)
from api.enums import CreditLedgerKind
from api.services.billing.cost_engine import CallCost, RateSpec, compute_call_cost
from api.services.billing.rates import resolve_platform_rate, resolve_provider_rate
from api.services.billing.usage import (
    billable_seconds_from_usage_info,
    usage_items_from_usage_info,
)


async def _period_minutes(
    session: AsyncSession, *, organization_id: int, at: datetime
) -> int:
    """Billable minutes for the account so far in ``at``'s calendar month.

    Used only for volume-tier matching.
    """
    period_start = at.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    total = await session.scalar(
        select(func.coalesce(func.sum(WorkflowRunModel.billable_seconds), 0))
        .join(WorkflowModel, WorkflowRunModel.workflow_id == WorkflowModel.id)
        .where(
            WorkflowModel.organization_id == organization_id,
            WorkflowRunModel.created_at >= period_start,
            WorkflowRunModel.created_at < at,
        )
    )
    return -(-int(total or 0) // 60)


async def cost_workflow_run(
    session: AsyncSession,
    workflow_run_id: int,
    *,
    recost: bool = False,
) -> CallCost | None:
    """Cost one completed run and persist the result.

    Returns the computed cost, or None if the run is missing or already costed.
    """
    run = await session.get(WorkflowRunModel, workflow_run_id)
    if run is None:
        logger.warning("Cannot cost workflow run {}: not found", workflow_run_id)
        return None

    if run.costed_at is not None and not recost:
        logger.debug("Workflow run {} already costed; skipping", workflow_run_id)
        return None

    workflow = await session.get(WorkflowModel, run.workflow_id)
    organization_id = getattr(workflow, "organization_id", None)
    if organization_id is None:
        logger.warning(
            "Cannot cost workflow run {}: workflow has no organization", workflow_run_id
        )
        return None

    # Cost as of when the call happened, not now, so a rate changed since then
    # does not rewrite this receipt.
    at = run.ended_at or run.created_at or datetime.now(UTC)

    billable_seconds = run.billable_seconds
    if billable_seconds is None:
        billable_seconds = billable_seconds_from_usage_info(run.usage_info)

    usage = usage_items_from_usage_info(run.usage_info)

    platform = await resolve_platform_rate(
        session,
        organization_id=organization_id,
        at=at,
        period_minutes=await _period_minutes(
            session, organization_id=organization_id, at=at
        ),
    )

    # Keyed by (component, provider, model). The engine looks up the exact
    # model first and falls back to the "" provider-wide entry.
    provider_rates: dict[tuple[str, str, str], RateSpec] = {}
    for item in usage:
        key = (item.component.value, item.provider, item.model)
        if key in provider_rates:
            continue
        resolved = await resolve_provider_rate(
            session,
            provider=item.provider,
            component=item.component,
            at=at,
            model=item.model,
        )
        if resolved is not None:
            provider_rates[key] = RateSpec(
                rate_mpaise=resolved.rate_mpaise, unit=resolved.unit
            )

    cost = compute_call_cost(
        billable_seconds=billable_seconds,
        platform_rate_mpaise=platform.rate_mpaise,
        usage=usage,
        provider_rates=provider_rates,
    )

    if cost.uncosted:
        # Loud, because it means reported provider cost is understated and
        # margin correspondingly overstated for this call.
        logger.warning(
            "Workflow run {} has {} usage item(s) with no rate on file: {}",
            workflow_run_id,
            len(cost.uncosted),
            ", ".join(
                f"{u.component.value}:{u.provider}" + (f"/{u.model}" if u.model else "")
                for u in cost.uncosted
            ),
        )

    # Recosting replaces the old receipt rather than appending to it.
    await session.execute(
        delete(CallCostItemModel).where(
            CallCostItemModel.workflow_run_id == workflow_run_id
        )
    )
    for line in cost.line_items:
        session.add(
            CallCostItemModel(
                workflow_run_id=workflow_run_id,
                component=line.component,
                provider=line.provider,
                model=line.model,
                units=line.units,
                unit_rate_mpaise=line.unit_rate_mpaise,
                cost_paise=line.cost_paise,
            )
        )

    run.billable_seconds = billable_seconds
    run.platform_rate_mpaise_applied = cost.platform_rate_mpaise
    run.total_provider_cost_paise = cost.total_provider_cost_paise
    run.total_charged_paise = cost.total_charged_paise
    run.costed_at = datetime.now(UTC)

    await _debit_ledger(
        session,
        organization_id=organization_id,
        workflow_run_id=workflow_run_id,
        amount_paise=cost.total_charged_paise,
        recost=recost,
    )

    await session.flush()
    return cost


async def _debit_ledger(
    session: AsyncSession,
    *,
    organization_id: int,
    workflow_run_id: int,
    amount_paise: int,
    recost: bool,
) -> None:
    """Record the usage debit for a run, exactly once.

    A partial unique index on (organization_id, ref_type, ref_id) for usage
    rows backs this up at the database level, so a concurrent retry cannot slip
    a second debit through.
    """
    if amount_paise <= 0:
        return

    ref_id = str(workflow_run_id)
    existing = await session.scalar(
        select(CreditLedgerModel).where(
            CreditLedgerModel.organization_id == organization_id,
            CreditLedgerModel.kind == CreditLedgerKind.USAGE.value,
            CreditLedgerModel.ref_type == "workflow_run",
            CreditLedgerModel.ref_id == ref_id,
        )
    )
    if existing is not None:
        if not recost:
            return
        # Recosting: undo the previous debit before writing the new one, so the
        # balance reflects the corrected figure rather than both attempts.
        await session.delete(existing)
        await session.flush()

    balance = await current_balance_paise(session, organization_id=organization_id)
    session.add(
        CreditLedgerModel(
            organization_id=organization_id,
            delta_paise=-amount_paise,
            kind=CreditLedgerKind.USAGE.value,
            ref_type="workflow_run",
            ref_id=ref_id,
            balance_after_paise=balance - amount_paise,
        )
    )


async def current_balance_paise(session: AsyncSession, *, organization_id: int) -> int:
    """Balance derived from the ledger. Never read from a cached column."""
    total = await session.scalar(
        select(func.coalesce(func.sum(CreditLedgerModel.delta_paise), 0)).where(
            CreditLedgerModel.organization_id == organization_id
        )
    )
    return int(total or 0)
