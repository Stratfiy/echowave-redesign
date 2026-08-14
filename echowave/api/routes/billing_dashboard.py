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
from sqlalchemy import select

from api.db import billing_dashboard_client as dash
from api.db import db_client
from api.db.models import (
    BillingAuditLogModel,
    CreditLedgerModel,
    ManagedMarkupHistoryModel,
    MarkupChangeChallengeModel,
    OrganizationModel,
    UserModel,
)
from api.enums import BillingAuditAction, CreditLedgerKind
from api.services.auth.depends import get_superuser
from api.services.billing import (
    default_rates,
    fx_source,
    kpis,
    markup,
    rate_card,
    readiness,
    realized_rates,
)
from api.services.billing.costing import current_balance_paise
from api.services.billing.rate_card import RateCardError
from api.services.billing.rollup import IST
from api.services.messaging.email import send_email
from api.services.readiness import as_dict

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
        # The turns were already being returned raw. Nothing summarised them,
        # so answering "was this call slow" meant reading a table by eye.
        detail["latency_summary"] = await dash.call_latency_summary(
            session, workflow_run_id=workflow_run_id
        )
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
            # TTFT and TTFB as their own series rather than folded into the
            # stage bar, because "is it the model or the voice" is the first
            # question anyone asks and a median-of-stages cannot answer it.
            "percentiles": {
                measure: await dash.latency_percentile_series(
                    session,
                    start=rng.start,
                    end=rng.end,
                    measure=measure,
                    organization_id=organization_id,
                    language=language,
                )
                for measure in dash.LATENCY_MEASURES
            },
            # Computed over the whole window, not summarised from the series:
            # the p95 of a month is not the mean of its daily p95s.
            "headline": await dash.latency_headline(
                session,
                start=rng.start,
                end=rng.end,
                organization_id=organization_id,
            ),
            "tools": await dash.tool_call_stats(
                session,
                start=rng.start,
                end=rng.end,
                organization_id=organization_id,
            ),
        }


# ---------------------------------------------------------------------------
# 3.6b Tokens
#
# Token counts have always been stored — `call_cost_items.units` for the LLM
# component is the raw count — and only ever rendered as money. Tokens per
# minute of conversation is the number that predicts what a prompt change will
# cost, and it is comparable across accounts, models and months in a way that
# rupees are not.
# ---------------------------------------------------------------------------


@router.get("/tokens")
async def get_tokens(
    rng: RangeParams = Depends(),
    granularity: str = Query("day", pattern="^(day|week|month)$"),
    organization_id: int | None = Query(None),
) -> dict[str, Any]:
    async with db_client.async_session() as session:
        return {
            "range": {"start": rng.start.isoformat(), "end": rng.end.isoformat()},
            "granularity": granularity,
            "series": await dash.token_usage_series(
                session,
                start=rng.start,
                end=rng.end,
                granularity=granularity,
                organization_id=organization_id,
            ),
            "by_model": await dash.token_usage_by_model(
                session,
                start=rng.start,
                end=rng.end,
                organization_id=organization_id,
            ),
            # The shape a call-wide total cannot show: a voice agent resends the
            # whole conversation every turn, so language-model spend grows with
            # the square of call length. The fixes are structural and none of
            # them appears as a line item.
            "context_growth": await dash.context_growth_by_turn(
                session,
                start=rng.start,
                end=rng.end,
                organization_id=organization_id,
            ),
            # The blend assumption, measured instead of assumed. Every LLM
            # margin figure on every screen rests on LLM_INPUT_SHARE, and the
            # data to check it has been recorded per turn all along.
            "input_share": await dash.observed_input_share(
                session,
                start=rng.start,
                end=rng.end,
                organization_id=organization_id,
            ),
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
    """Every price in force, what we actually paid, and where the two disagree.

    Three things an operator cannot get from the stored rate alone:

    * **Both currencies.** Vendors quote in dollars, we invoice in rupees.
      Showing one leaves somebody converting in their head at a guessed rate.
    * **The realized rate**, measured from calls already made. List prices are
      the number nobody pays; this is the number we paid.
    * **Divergence**, where the two disagree by more than a rounding error.
      That check is what would have caught the seeded Sarvam TTS rate sitting
      at 1.56x under its published price, and what will catch a telephony rate
      still set to a US list when the traffic is Indian.

    Reported, never auto-applied. The realized figure is derived from costs that
    were themselves computed from the configured rate, so writing it back would
    be circular; it earns its keep by disagreeing, and a human decides why.
    """
    async with db_client.async_session() as session:
        card = await rate_card.get_rate_card(session)
        realized = await realized_rates.measure(session)

    configured = {
        (r["provider"], r["component"]): float(r["rate_mpaise"])
        for r in card.provider_rates
        # Provider-wide rows only: a model-specific rate cannot be compared
        # against a blend measured across every model from that vendor.
        if r["model"] is None
    }
    divergences = realized_rates.divergence(realized, configured)

    return {
        "global_tier": card.global_tier,
        "volume_tiers": card.volume_tiers,
        "provider_rates": card.provider_rates,
        "exchange_rate": card.exchange_rate,
        "using_fallback_platform_rate": card.using_fallback_platform_rate,
        "fallback": card.fallback,
        "realized": [
            {
                "provider": r.provider,
                "component": r.component,
                "rate_mpaise": round(r.mpaise_per_unit, 4),
                "rate_inr": r.mpaise_per_unit / 100_000,
                "units": r.units,
                "calls": r.calls,
                "cost_paise": r.cost_paise,
                # Below the significance floor a blend is one atypical call away
                # from nonsense, so it is shown as unmeasured rather than as a
                # small number that looks authoritative.
                "significant": r.is_significant,
            }
            for r in realized
        ],
        "divergence": [
            {
                "provider": d.provider,
                "component": d.component,
                "configured_mpaise": d.configured_mpaise,
                "realized_mpaise": round(d.realized_mpaise, 4),
                "ratio": round(d.ratio, 4),
                "units": d.units,
                "note": d.note,
            }
            for d in divergences
        ],
        "list_prices_as_of": default_rates.AS_OF,
        "window_days": realized_rates.DEFAULT_WINDOW_DAYS,
    }


@router.post("/rate-card/exchange-rate/refresh")
async def refresh_exchange_rate(
    user: UserModel = Depends(get_superuser),
) -> dict[str, Any]:
    """Fetch USD→INR now and record it if it moved.

    The one price in this system with a real feed behind it. Provider prices are
    published as marketing HTML with no API, so they stay operator-set — see
    ``services/billing/fx_source``.
    """
    async with db_client.async_session() as session:
        try:
            changed = await fx_source.refresh(session)
        except fx_source.FxFetchError as exc:
            # 502 rather than 500: the failure is upstream, and nothing was
            # written. A stale rate is survivable; an invented one is not.
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        await session.commit()
        card = await rate_card.get_rate_card(session)

    return {"changed": changed, "exchange_rate": card.exchange_rate}


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


@router.get("/readiness")
async def billing_readiness() -> dict[str, Any]:
    """What would silently cost money or break GST compliance, and how to fix it.

    The mirror of ``/privacy/readiness``, for the money path. Deliberately not
    account-scoped: these are properties of the deployment — whether the
    supplier identity is set, whether there is a price book — and the answer is
    the same for every account on it.

    The check to watch is ``payments_have_vouchers``. It is designed to read
    zero missing, and any other value is an accrued tax liability rather than a
    statistic.
    """
    async with db_client.async_session() as session:
        assessment = await readiness.assess(session)
    return as_dict(assessment)


# ---------------------------------------------------------------------------
# Activation
#
# The one question none of the screens above can answer: who arrived and never
# made a call. Everything else here measures calls that happened.
# ---------------------------------------------------------------------------


@router.get("/activation")
async def get_activation(rng: RangeParams = Depends()) -> dict[str, Any]:
    """Signup → agent → first call → first top-up, for the signup cohort.

    The cohort is fixed at signup and each step asks "ever", not "in the
    window". Counting steps inside the window would make a wide range look
    worse simply by admitting recent signups who have not had time yet.
    """
    return await db_client.activation_funnel(rng.start, rng.end)


# ---------------------------------------------------------------------------
# Model economics
#
# Both halves of every cost line have always been stored — what the customer
# was charged and what the vendor charged us — but nothing grouped them by the
# model that incurred them. This is the screen behind "which model is eating
# the margin", and it covers the whole receipt rather than only the LLM.
# ---------------------------------------------------------------------------


@router.get("/model-usage")
async def get_model_usage(
    rng: RangeParams = Depends(),
    component: str | None = Query(None, pattern="^(stt|llm|tts|telephony|platform)$"),
    organization_id: int | None = Query(None),
) -> dict[str, Any]:
    """Per-model usage, revenue, our cost and the margin between them."""
    async with db_client.async_session() as session:
        return await dash.model_usage(
            session,
            start=rng.start,
            end=rng.end,
            component=component,
            organization_id=organization_id,
        )


# ---------------------------------------------------------------------------
# Payments and top-ups
#
# Conversion is a question about the orders *started* in a window; money is a
# question about what *settled* in it. Answering both off one timestamp is how
# a payments screen ends up disagreeing with itself — see the client.
# ---------------------------------------------------------------------------


@router.get("/payments")
async def get_payments(
    rng: RangeParams = Depends(),
    granularity: str = Query("day", pattern="^(day|week|month)$"),
    organization_id: int | None = Query(None),
) -> dict[str, Any]:
    """Orders started, orders paid, money collected, credit granted."""
    async with db_client.async_session() as session:
        return await dash.payments_summary(
            session,
            start=rng.start,
            end=rng.end,
            granularity=granularity,
            organization_id=organization_id,
        )


# ---------------------------------------------------------------------------
# Managed markup
#
# One number sets the price of every managed call on every account. It used to
# be an environment variable, which made changing it a deploy; it is now an
# effective-dated history, which makes changing it a form — and that is exactly
# why it is the only setting on this router that needs a code from the company
# inbox before it takes effect.
# ---------------------------------------------------------------------------


class MarkupChangeRequest(BaseModel):
    markup_bps: int = Field(
        ...,
        ge=markup.MIN_MARKUP_BPS,
        le=markup.MAX_MARKUP_BPS,
        description="Basis points. 10000 is at cost; 14000 charges 1.4x.",
    )
    note: str | None = Field(None, max_length=500)


class MarkupConfirmRequest(BaseModel):
    #: Only the code. The value being applied was staged server-side, so a
    #: tampered confirmation cannot substitute a different multiple.
    code: str = Field(..., min_length=4, max_length=12)


@router.get("/rate-card/markup")
async def get_managed_markup() -> dict[str, Any]:
    """The multiple in force, and whether a change is waiting on a code."""
    async with db_client.async_session() as session:
        current = await markup.resolve_markup_bps(session)
        pending = (
            await session.execute(select(MarkupChangeChallengeModel).limit(1))
        ).scalar_one_or_none()
        history = (
            (
                await session.execute(
                    select(ManagedMarkupHistoryModel)
                    .order_by(ManagedMarkupHistoryModel.effective_from.desc())
                    .limit(10)
                )
            )
            .scalars()
            .all()
        )

    return {
        "markup_bps": current,
        "min_bps": markup.MIN_MARKUP_BPS,
        "max_bps": markup.MAX_MARKUP_BPS,
        "notice_address": markup.MARKUP_NOTICE_ADDRESS,
        # Never the code or its hash — only that something is waiting, and for
        # what. Enough to render the confirmation step, useless to an attacker.
        "pending": (
            {
                "markup_bps": pending.markup_bps,
                "previous_markup_bps": pending.previous_markup_bps,
                "expires_at": pending.expires_at.isoformat(),
                "attempts_remaining": max(
                    0, markup.MAX_ATTEMPTS - (pending.attempts or 0)
                ),
            }
            if pending is not None
            else None
        ),
        "history": [
            {
                "markup_bps": row.markup_bps,
                "effective_from": row.effective_from.isoformat(),
                "effective_to": (
                    row.effective_to.isoformat() if row.effective_to else None
                ),
                "note": row.note,
            }
            for row in history
        ],
    }


@router.post("/rate-card/markup/request")
async def request_managed_markup_change(
    request: MarkupChangeRequest, user: UserModel = Depends(get_superuser)
) -> dict[str, Any]:
    """Stage a change and email a code. Applies nothing."""
    async with db_client.async_session() as session:
        try:
            started = await markup.start_change(
                session,
                markup_bps=request.markup_bps,
                actor_user_id=user.id,
                note=request.note,
            )
        except markup.MarkupError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        await session.commit()

    result = await send_email(
        to=started.address,
        subject=markup.notice_subject(),
        body_text=markup.notice_body(started),
    )
    # The change stays staged even if the mail failed. Rolling it back would
    # mean a delivery problem silently discards a deliberate action; leaving it
    # means the operator can retry the send or let it expire.
    return {
        "requested": True,
        "markup_bps": started.markup_bps,
        "previous_markup_bps": started.previous_markup_bps,
        "expires_at": started.expires_at.isoformat(),
        "sent_to": started.address,
        "email_sent": result.ok,
        "email_error": None if result.ok else result.error,
    }


@router.put("/rate-card/markup")
async def confirm_managed_markup_change(
    request: MarkupConfirmRequest, user: UserModel = Depends(get_superuser)
) -> dict[str, Any]:
    """Apply the staged change, given the code from the inbox."""
    async with db_client.async_session() as session:
        try:
            applied = await markup.confirm_change(
                session, code=request.code, actor_user_id=user.id
            )
        except markup.MarkupError as exc:
            # 400 rather than 403: a wrong code is a failed step in a flow the
            # caller is authorised for, not a permission problem.
            await session.commit()  # persist the attempt counter
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        session.add(
            BillingAuditLogModel(
                organization_id=None,
                actor_user_id=user.id,
                action=BillingAuditAction.MANAGED_MARKUP_CHANGED.value,
                old_value={"markup_bps": applied.previous_markup_bps},
                new_value={"markup_bps": applied.markup_bps},
                note=applied.note,
            )
        )
        await session.commit()

    return {
        "markup_bps": applied.markup_bps,
        "previous_markup_bps": applied.previous_markup_bps,
        "applied": True,
    }
