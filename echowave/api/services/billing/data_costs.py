"""Meter a lookup bought on the platform's key, off a call (D-1b).

A search Decibyl runs from the thread, or an agent runs in a text turn, is
paid for twice over on the exchange table: the tool-call event (the fee) and
the vendor's price for the request, passed through at cost. The event is
``events.charge``; this is the pass-through.

Shaped like ``embedding_ingestion.py``, not ``costing.py``: a direct ledger
debit outside the call receipt, because a lookup from a thread has no run to
hang a line on, plus one row in ``data_lookup_costs`` so the unit-economics
screen sees vendor cost beside charge. A lookup made during a *call* is not
this module's: the pipeline records it under ``usage_info["data"]`` and the
call receipt prices it (``usage.py``).

**Never a standalone customer-facing line.** The customer sees the tool
call; this row is the itemised cost behind it, on the internal screen.

Keyed on ``ref_id`` -- the tool call's own id -- so a retried turn debits
nothing twice. A vendor with no rate on file debits nothing and says so in
the log, the same silence-is-a-defect rule every uncosted line follows.
"""

from __future__ import annotations

from datetime import UTC, datetime

from loguru import logger
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.db.models import CreditLedgerModel, DataLookupCostModel
from api.enums import CostComponent, CreditLedgerKind
from api.services.billing.markup import resolve_line_markup_bps
from api.services.billing.money import cost_paise, round_half_up_div
from api.services.billing.rates import resolve_provider_rate

REF_TYPE = "data_lookup"


async def _balance(session: AsyncSession, *, organization_id: int) -> int:
    return int(
        await session.scalar(
            select(func.coalesce(func.sum(CreditLedgerModel.delta_paise), 0)).where(
                CreditLedgerModel.organization_id == organization_id
            )
        )
        or 0
    )


async def debit_lookup(
    session: AsyncSession,
    *,
    organization_id: int,
    provider: str,
    kind: str,
    requests: int,
    ref_id: str,
    workflow_id: int | None = None,
    at: datetime | None = None,
) -> int:
    """Debit the pass-through cost of ``requests`` lookups. Returns paise
    charged (0 when already done, internal, or no rate on file)."""
    from api.services.billing.internal_accounts import is_internal

    if requests <= 0 or not ref_id:
        return 0
    ref = ref_id[:64]
    existing = await session.scalar(
        select(DataLookupCostModel.id).where(
            DataLookupCostModel.organization_id == organization_id,
            DataLookupCostModel.ref_id == ref,
        )
    )
    if existing is not None:
        return 0
    at = at or datetime.now(UTC)
    rate = await resolve_provider_rate(
        session, provider=provider, component=CostComponent.DATA, at=at, model=kind
    )
    if rate is None:
        logger.warning(
            "No data rate on file for {}/{}: {} lookup(s) for org {} go uncosted",
            provider,
            kind,
            requests,
            organization_id,
        )
        return 0
    vendor = cost_paise(quantity=requests, rate_mpaise=rate.rate_mpaise, unit=rate.unit)
    internal = await is_internal(session, organization_id)
    markup = (
        10_000
        if internal
        else await resolve_line_markup_bps(
            session,
            component=CostComponent.DATA,
            provider=provider,
            at=at,
            model=kind,
            organization_id=organization_id,
        )
    )
    charged = 0 if internal else round_half_up_div(vendor * markup, 10_000)
    session.add(
        DataLookupCostModel(
            organization_id=organization_id,
            workflow_id=workflow_id,
            provider=provider,
            kind=kind,
            requests=requests,
            vendor_cost_paise=vendor,
            charged_paise=charged,
            ref_id=ref,
        )
    )
    if charged > 0:
        from api.services.billing.ledger_lock import lock_organization_ledger

        await lock_organization_ledger(session, organization_id=organization_id)
        balance = await _balance(session, organization_id=organization_id)
        session.add(
            CreditLedgerModel(
                organization_id=organization_id,
                delta_paise=-charged,
                kind=CreditLedgerKind.USAGE.value,
                ref_type=REF_TYPE,
                ref_id=ref,
                balance_after_paise=balance - charged,
                note=f"{provider} {kind} ×{requests} on the platform key",
                created_at=at,
                workflow_id=workflow_id,
            )
        )
    await session.flush()
    return charged


async def debit_lookup_in_own_session(
    *,
    organization_id: int | None,
    provider: str,
    kind: str,
    requests: int,
    ref_id: str,
    workflow_id: int | None = None,
) -> int:
    """``debit_lookup`` from a path that holds no session. Never raises."""
    if not organization_id:
        return 0
    try:
        from api.db import db_client

        async with db_client.async_session() as session:
            charged = await debit_lookup(
                session,
                organization_id=organization_id,
                provider=provider,
                kind=kind,
                requests=requests,
                ref_id=ref_id,
                workflow_id=workflow_id,
            )
            await session.commit()
            return charged
    except Exception as exc:  # noqa: BLE001 - the lookup happened; log it
        logger.error(
            "Could not debit {} {} for org {}: {}", provider, kind, organization_id, exc
        )
        return 0
