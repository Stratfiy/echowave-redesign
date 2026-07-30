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

from datetime import date, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from api.db import billing_dashboard_client as dash
from api.db import db_client
from api.db.models import (
    BillingAuditLogModel,
    CreditLedgerModel,
    OrganizationModel,
    UserModel,
)
from api.enums import BillingAuditAction, CreditLedgerKind
from api.services.auth.depends import get_superuser
from api.services.billing import kpis, rate_card
from api.services.billing.costing import current_balance_paise
from api.services.billing.rate_card import RateCardError
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


# ---------------------------------------------------------------------------
# 3.7 Unit economics
#
# The screen behind the pricing decision rather than behind an invoice: what a
# minute costs, what it earns, and what the 15-second pulse gives away.
# ---------------------------------------------------------------------------


@router.get("/unit-economics")
async def get_unit_economics(rng: RangeParams = Depends()) -> dict[str, Any]:
    async with db_client.async_session() as session:
        report = await kpis.unit_economics_report(session, start=rng.start, end=rng.end)
    return {
        "range": {"start": rng.start.isoformat(), "end": rng.end.isoformat()},
        **report,
    }


# ---------------------------------------------------------------------------
# 3.8 Rate card
#
# Setting prices, rather than reading what they produced. Every write here goes
# through services/billing/rate_card.py, which closes the outgoing row instead
# of overwriting it — so changing a price never rewrites an invoice already
# sent.
# ---------------------------------------------------------------------------


def _rate_card_error(exc: RateCardError) -> HTTPException:
    return HTTPException(status_code=400, detail=str(exc))


class PlatformPriceRequest(BaseModel):
    """A platform price in exactly one currency, with an optional pulse."""

    platform_rate_micros_usd: int | None = Field(
        None,
        ge=0,
        le=10_000_000,
        description="Micro-dollars per minute: $0.02 is 20000",
    )
    platform_rate_mpaise: int | None = Field(
        None,
        ge=0,
        le=10_000_000,
        description="Millipaise per minute, for a contract written in rupees",
    )
    pulse_seconds: int | None = Field(
        None, ge=1, le=60, description="Billing granularity. Omit for the default."
    )
    effective_from: datetime | None = Field(
        None, description="Defaults to now. May be future-dated."
    )
    note: str | None = None


class VolumeTierRequest(PlatformPriceRequest):
    name: str = Field(..., min_length=1, max_length=64)
    min_period_minutes: int = Field(
        ..., ge=1, description="Minutes in the period at which this price starts"
    )


class ProviderRateRequest(BaseModel):
    provider: str = Field(..., min_length=1, max_length=64)
    component: str = Field(..., description="stt | llm | tts | telephony")
    unit: str = Field(..., description="minute | 1k_chars | 1k_tokens")
    rate_mpaise: int = Field(..., ge=0, le=100_000_000)
    model: str = Field("", max_length=128, description="Empty = provider-wide fallback")
    effective_from: datetime | None = None
    note: str | None = None


class ExchangeRateRequest(BaseModel):
    paise_per_usd: int = Field(
        ..., gt=0, le=100_000, description="Rupees per dollar in paise: ₹96.00 is 9600"
    )
    source: str | None = Field(None, max_length=64)
    note: str | None = None


@router.get("/rate-card")
async def get_rate_card() -> dict[str, Any]:
    """Every price currently in force, and whether any is still a fallback."""
    async with db_client.async_session() as session:
        card = await rate_card.get_rate_card(session)
    return {
        "global_tier": card.global_tier,
        "volume_tiers": card.volume_tiers,
        "provider_rates": card.provider_rates,
        "exchange_rate": card.exchange_rate,
        "using_fallback_platform_rate": card.using_fallback_platform_rate,
        "fallback": card.fallback,
    }


@router.put("/rate-card/platform")
async def set_global_platform_rate(
    request: PlatformPriceRequest, user: UserModel = Depends(get_superuser)
) -> dict[str, Any]:
    """Set the price every account pays without an override.

    Stored as a volume tier with a threshold of zero — a tier every account has
    already reached. See services/billing/rate_card.py.
    """
    async with db_client.async_session() as session:
        try:
            await rate_card.set_volume_tier(
                session,
                actor_user_id=user.id,
                min_period_minutes=rate_card.GLOBAL_TIER_MIN_MINUTES,
                name=rate_card.GLOBAL_TIER_NAME,
                platform_rate_micros_usd=request.platform_rate_micros_usd,
                platform_rate_mpaise=request.platform_rate_mpaise,
                pulse_seconds=request.pulse_seconds,
                effective_from=request.effective_from,
                note=request.note,
            )
        except RateCardError as exc:
            raise _rate_card_error(exc) from exc
        await session.commit()
        card = await rate_card.get_rate_card(session)
    return {"global_tier": card.global_tier}


@router.put("/rate-card/tiers")
async def set_volume_tier(
    request: VolumeTierRequest, user: UserModel = Depends(get_superuser)
) -> dict[str, Any]:
    async with db_client.async_session() as session:
        try:
            await rate_card.set_volume_tier(
                session,
                actor_user_id=user.id,
                min_period_minutes=request.min_period_minutes,
                name=request.name,
                platform_rate_micros_usd=request.platform_rate_micros_usd,
                platform_rate_mpaise=request.platform_rate_mpaise,
                pulse_seconds=request.pulse_seconds,
                effective_from=request.effective_from,
                note=request.note,
            )
        except RateCardError as exc:
            raise _rate_card_error(exc) from exc
        await session.commit()
        card = await rate_card.get_rate_card(session)
    return {"volume_tiers": card.volume_tiers}


@router.delete("/rate-card/tiers/{min_period_minutes}")
async def retire_volume_tier(
    min_period_minutes: int, user: UserModel = Depends(get_superuser)
) -> dict[str, Any]:
    async with db_client.async_session() as session:
        try:
            await rate_card.retire_volume_tier(
                session,
                actor_user_id=user.id,
                min_period_minutes=min_period_minutes,
            )
        except RateCardError as exc:
            raise _rate_card_error(exc) from exc
        await session.commit()
        card = await rate_card.get_rate_card(session)
    return {"volume_tiers": card.volume_tiers}


@router.put("/rate-card/providers")
async def set_provider_rate(
    request: ProviderRateRequest, user: UserModel = Depends(get_superuser)
) -> dict[str, Any]:
    """Set what a provider costs us. Passed through to customers at cost."""
    async with db_client.async_session() as session:
        try:
            await rate_card.set_provider_rate(
                session,
                actor_user_id=user.id,
                provider=request.provider,
                component=request.component,
                unit=request.unit,
                rate_mpaise=request.rate_mpaise,
                model=request.model,
                effective_from=request.effective_from,
                note=request.note,
            )
        except RateCardError as exc:
            raise _rate_card_error(exc) from exc
        await session.commit()
        card = await rate_card.get_rate_card(session)
    return {"provider_rates": card.provider_rates}


@router.delete("/rate-card/providers")
async def retire_provider_rate(
    provider: str = Query(...),
    component: str = Query(...),
    model: str = Query(""),
    user: UserModel = Depends(get_superuser),
) -> dict[str, Any]:
    """Stop pricing a provider or model.

    The usage becomes **uncosted**, not free — it is reported on the
    unit-economics screen rather than silently priced at zero.
    """
    async with db_client.async_session() as session:
        try:
            await rate_card.retire_provider_rate(
                session,
                actor_user_id=user.id,
                provider=provider,
                component=component,
                model=model,
            )
        except RateCardError as exc:
            raise _rate_card_error(exc) from exc
        await session.commit()
        card = await rate_card.get_rate_card(session)
    return {"provider_rates": card.provider_rates}


@router.put("/rate-card/exchange-rate")
async def set_exchange_rate(
    request: ExchangeRateRequest, user: UserModel = Depends(get_superuser)
) -> dict[str, Any]:
    """Set the USD→INR rate. Takes effect now, never retroactively."""
    async with db_client.async_session() as session:
        try:
            await rate_card.set_exchange_rate(
                session,
                actor_user_id=user.id,
                paise_per_usd=request.paise_per_usd,
                source=request.source,
                note=request.note,
            )
        except RateCardError as exc:
            raise _rate_card_error(exc) from exc
        await session.commit()
        card = await rate_card.get_rate_card(session)
    return {"exchange_rate": card.exchange_rate}


@router.put("/accounts/{organization_id}/platform-rate")
async def set_account_platform_rate(
    organization_id: int,
    request: PlatformPriceRequest,
    user: UserModel = Depends(get_superuser),
) -> dict[str, Any]:
    """Override one account's price, in either currency, with its own pulse."""
    async with db_client.async_session() as session:
        try:
            await rate_card.set_account_rate(
                session,
                actor_user_id=user.id,
                organization_id=organization_id,
                platform_rate_micros_usd=request.platform_rate_micros_usd,
                platform_rate_mpaise=request.platform_rate_mpaise,
                pulse_seconds=request.pulse_seconds,
                effective_from=request.effective_from,
                note=request.note,
            )
        except RateCardError as exc:
            raise _rate_card_error(exc) from exc
        await session.commit()
        history = await dash.account_rate_history(
            session, organization_id=organization_id
        )
    return {"organization_id": organization_id, "rate_history": history}
