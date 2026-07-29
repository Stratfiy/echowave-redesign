"""Admin billing dashboard.

**Every route here is staff-only.** The router carries a router-level
``Depends(get_superuser)`` so a new endpoint added to this file is gated by
default rather than by the author remembering to add it — these responses
contain cross-account financial data.

Handlers stay thin: they parse and validate the request, then delegate to
``db/billing_dashboard_client.py`` for reads and ``services/billing/`` for
anything that changes state.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from api.db import billing_dashboard_client as dash
from api.db import db_client
from api.db.models import (
    BillingAuditLogModel,
    CreditLedgerModel,
    OrganizationModel,
    OrganizationRateHistoryModel,
    UserModel,
)
from api.enums import BillingAuditAction, CreditLedgerKind
from api.services.auth.depends import get_superuser
from api.services.billing.costing import current_balance_paise
from api.services.billing.money import DEFAULT_PLATFORM_RATE_MPAISE
from api.services.billing.rollup import IST

router = APIRouter(
    prefix="/admin/billing",
    tags=["admin-billing"],
    dependencies=[Depends(get_superuser)],
)

MAX_RANGE_DAYS = 366


def _resolve_range(start: str | None, end: str | None) -> tuple[date, date]:
    """Parse an inclusive IST day range, defaulting to the last 30 days.

    Bounded so a hand-crafted request cannot ask for an unbounded scan.
    """
    today = datetime.now(IST).date()
    try:
        end_date = date.fromisoformat(end) if end else today
        start_date = (
            date.fromisoformat(start) if start else end_date - timedelta(days=29)
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Dates must be YYYY-MM-DD") from exc

    if start_date > end_date:
        raise HTTPException(status_code=400, detail="start must not be after end")
    if (end_date - start_date).days > MAX_RANGE_DAYS:
        raise HTTPException(
            status_code=400, detail=f"Range must not exceed {MAX_RANGE_DAYS} days"
        )
    return start_date, end_date


class RangeParams:
    def __init__(
        self,
        start: str | None = Query(None, description="Inclusive IST day, YYYY-MM-DD"),
        end: str | None = Query(None, description="Inclusive IST day, YYYY-MM-DD"),
    ):
        self.start, self.end = _resolve_range(start, end)


# ---------------------------------------------------------------------------
# 3.1 Overview
# ---------------------------------------------------------------------------


@router.get("/overview")
async def get_overview(rng: RangeParams = Depends()) -> dict[str, Any]:
    """Headline figures, with the previous equal-length period for comparison."""
    async with db_client.async_session() as session:
        span = (rng.end - rng.start).days + 1
        prev_end = rng.start - timedelta(days=1)
        prev_start = prev_end - timedelta(days=span - 1)

        current = await dash.overview_totals(session, start=rng.start, end=rng.end)
        previous = await dash.overview_totals(session, start=prev_start, end=prev_end)

        return {
            "range": {"start": rng.start.isoformat(), "end": rng.end.isoformat()},
            "current": current,
            "previous": previous,
            "calls_today": await dash.calls_today(session),
            "concurrent_calls": await dash.concurrency_now(session),
            "minutes_per_day": await dash.daily_series(
                session, start=rng.start, end=rng.end
            ),
            "cost_composition": await dash.cost_composition_series(
                session, start=rng.start, end=rng.end
            ),
            "top_accounts": await dash.top_accounts(
                session, start=rng.start, end=rng.end
            ),
            "latency": await dash.latency_series(session, start=rng.start, end=rng.end),
        }


# ---------------------------------------------------------------------------
# 3.2 / 3.3 Accounts
# ---------------------------------------------------------------------------


@router.get("/accounts")
async def list_accounts(
    rng: RangeParams = Depends(),
    account_type: str | None = Query(None),
    status: str | None = Query(None),
) -> dict[str, Any]:
    async with db_client.async_session() as session:
        accounts = await dash.accounts_summary(
            session,
            start=rng.start,
            end=rng.end,
            account_type=account_type,
            status=status,
        )
        return {
            "range": {"start": rng.start.isoformat(), "end": rng.end.isoformat()},
            "accounts": accounts,
        }


@router.get("/accounts/{organization_id}")
async def get_account(
    organization_id: int, rng: RangeParams = Depends()
) -> dict[str, Any]:
    async with db_client.async_session() as session:
        detail = await dash.account_detail(session, organization_id=organization_id)
        if detail is None:
            raise HTTPException(status_code=404, detail="Account not found")

        return {
            "range": {"start": rng.start.isoformat(), "end": rng.end.isoformat()},
            "account": detail,
            "daily": await dash.daily_series(
                session,
                start=rng.start,
                end=rng.end,
                organization_id=organization_id,
            ),
            "cost_composition": await dash.cost_composition_series(
                session,
                start=rng.start,
                end=rng.end,
                organization_id=organization_id,
            ),
            "latency_by_language": await dash.latency_by_language(
                session,
                start=rng.start,
                end=rng.end,
                organization_id=organization_id,
            ),
            "rate_history": await dash.account_rate_history(
                session, organization_id=organization_id
            ),
            "credit_ledger": await dash.credit_ledger(
                session, organization_id=organization_id
            ),
        }


class SetPlatformRateRequest(BaseModel):
    platform_rate_mpaise: int = Field(
        ..., ge=0, le=10_000_000, description="Platform rate in millipaise per minute"
    )
    effective_from: datetime | None = Field(
        None, description="Defaults to now. May be future-dated."
    )
    note: str | None = None


@router.put("/accounts/{organization_id}/platform-rate")
async def set_platform_rate(
    organization_id: int,
    request: SetPlatformRateRequest,
    user: UserModel = Depends(get_superuser),
) -> dict[str, Any]:
    """Set an account's platform rate, effective-dated and audited.

    Never updates a rate in place: the currently-open row is closed and a new
    one inserted, so recomputing an old invoice still reproduces the original
    number.
    """
    async with db_client.async_session() as session:
        org = await session.get(OrganizationModel, organization_id)
        if org is None:
            raise HTTPException(status_code=404, detail="Account not found")

        effective_from = request.effective_from or datetime.now(UTC)
        if effective_from.tzinfo is None:
            effective_from = effective_from.replace(tzinfo=UTC)

        history = await dash.account_rate_history(
            session, organization_id=organization_id
        )
        open_row = next((h for h in history if h["effective_to"] is None), None)
        old_rate = (
            open_row["platform_rate_mpaise"]
            if open_row
            else (org.platform_rate_mpaise or DEFAULT_PLATFORM_RATE_MPAISE)
        )

        if open_row is not None:
            existing = await session.get(OrganizationRateHistoryModel, open_row["id"])
            if existing is not None:
                if existing.effective_from >= effective_from:
                    raise HTTPException(
                        status_code=400,
                        detail=(
                            "effective_from must be after the current rate's "
                            "effective_from"
                        ),
                    )
                existing.effective_to = effective_from

        session.add(
            OrganizationRateHistoryModel(
                organization_id=organization_id,
                platform_rate_mpaise=request.platform_rate_mpaise,
                effective_from=effective_from,
                set_by=user.id,
                note=request.note,
            )
        )
        # Convenience mirror of the current rate; history stays the source of truth.
        org.platform_rate_mpaise = request.platform_rate_mpaise

        session.add(
            BillingAuditLogModel(
                organization_id=organization_id,
                actor_user_id=user.id,
                action=BillingAuditAction.PLATFORM_RATE_CHANGED.value,
                old_value={"platform_rate_mpaise": old_rate},
                new_value={
                    "platform_rate_mpaise": request.platform_rate_mpaise,
                    "effective_from": effective_from.isoformat(),
                },
                note=request.note,
            )
        )
        await session.commit()

        return {
            "organization_id": organization_id,
            "platform_rate_mpaise": request.platform_rate_mpaise,
            "effective_from": effective_from.isoformat(),
        }


class CreditAdjustmentRequest(BaseModel):
    delta_paise: int = Field(..., description="Positive credits, negative debits")
    note: str = Field(..., min_length=1, description="Required: why this was adjusted")


@router.post("/accounts/{organization_id}/credit")
async def adjust_credit(
    organization_id: int,
    request: CreditAdjustmentRequest,
    user: UserModel = Depends(get_superuser),
) -> dict[str, Any]:
    """Manually adjust an account's credit balance. Audited; a note is required."""
    if request.delta_paise == 0:
        raise HTTPException(status_code=400, detail="delta_paise must not be zero")

    async with db_client.async_session() as session:
        org = await session.get(OrganizationModel, organization_id)
        if org is None:
            raise HTTPException(status_code=404, detail="Account not found")

        before = await current_balance_paise(session, organization_id=organization_id)
        after = before + request.delta_paise

        session.add(
            CreditLedgerModel(
                organization_id=organization_id,
                delta_paise=request.delta_paise,
                kind=CreditLedgerKind.ADJUSTMENT.value,
                ref_type="manual",
                ref_id=None,
                balance_after_paise=after,
                note=request.note,
                created_by=user.id,
            )
        )
        session.add(
            BillingAuditLogModel(
                organization_id=organization_id,
                actor_user_id=user.id,
                action=BillingAuditAction.CREDIT_ADJUSTED.value,
                old_value={"balance_paise": before},
                new_value={"balance_paise": after, "delta_paise": request.delta_paise},
                note=request.note,
            )
        )
        await session.commit()

        return {"organization_id": organization_id, "balance_paise": after}


# ---------------------------------------------------------------------------
# 3.4 Calls
# ---------------------------------------------------------------------------


@router.get("/calls")
async def list_calls(
    rng: RangeParams = Depends(),
    organization_id: int | None = Query(None),
    language: str | None = Query(None),
    direction: str | None = Query(None, pattern="^(inbound|outbound)$"),
    search: str | None = Query(None, max_length=128),
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
) -> dict[str, Any]:
    from api.services.billing.rollup import ist_day_bounds_utc

    start_utc, _ = ist_day_bounds_utc(rng.start)
    _, end_utc = ist_day_bounds_utc(rng.end)

    async with db_client.async_session() as session:
        calls, total = await dash.calls_page(
            session,
            start=start_utc,
            end=end_utc,
            organization_id=organization_id,
            language=language,
            direction=direction,
            search=search,
            limit=limit,
            offset=(page - 1) * limit,
        )
        return {
            "calls": calls,
            "total": total,
            "page": page,
            "limit": limit,
            "total_pages": (total + limit - 1) // limit,
        }


@router.get("/calls/{workflow_run_id}")
async def get_call(workflow_run_id: int) -> dict[str, Any]:
    """A call receipt: metadata, itemised cost, and per-turn latency."""
    async with db_client.async_session() as session:
        detail = await dash.call_detail(session, workflow_run_id=workflow_run_id)
        if detail is None:
            raise HTTPException(status_code=404, detail="Call not found")
        return detail


# ---------------------------------------------------------------------------
# 3.5 Campaigns
# ---------------------------------------------------------------------------


@router.get("/campaigns")
async def list_campaigns() -> dict[str, Any]:
    async with db_client.async_session() as session:
        return {"campaigns": await dash.campaigns_summary(session)}


@router.get("/campaigns/{campaign_id}/concurrency")
async def get_campaign_concurrency(campaign_id: int) -> dict[str, Any]:
    async with db_client.async_session() as session:
        return {
            "campaign_id": campaign_id,
            **await dash.campaign_concurrency(session, campaign_id=campaign_id),
        }


# ---------------------------------------------------------------------------
# 3.6 Latency
# ---------------------------------------------------------------------------


@router.get("/latency")
async def get_latency(
    rng: RangeParams = Depends(),
    organization_id: int | None = Query(None),
    language: str | None = Query(None),
) -> dict[str, Any]:
    async with db_client.async_session() as session:
        return {
            "range": {"start": rng.start.isoformat(), "end": rng.end.isoformat()},
            "series": await dash.latency_series(
                session,
                start=rng.start,
                end=rng.end,
                organization_id=organization_id,
                language=language,
            ),
            "by_language": await dash.latency_by_language(
                session, start=rng.start, end=rng.end, organization_id=organization_id
            ),
            "stage_medians": await dash.pipeline_stage_medians(
                session, start=rng.start, end=rng.end
            ),
            "slowest_turns": await dash.slowest_turns(
                session, start=rng.start, end=rng.end
            ),
            "languages": await dash.distinct_languages(session),
        }
