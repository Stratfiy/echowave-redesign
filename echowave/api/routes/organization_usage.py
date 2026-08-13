import json
from datetime import date as date_cls
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from api.db import db_client
from api.db.models import UserModel
from api.services.auth.depends import get_user
from api.services.reports import (
    answer_seizure_ratio,
    cost_by_outcome,
    generate_usage_runs_report_csv,
)
from api.utils.artifacts import artifact_url
from api.utils.recording_artifacts import has_recording_track

router = APIRouter(prefix="/organizations")


class CurrentUsageResponse(BaseModel):
    period_start: str
    period_end: str
    used_decibyl_tokens: float
    total_duration_seconds: int
    used_amount_usd: Optional[float] = None
    currency: Optional[str] = None
    price_per_second_usd: Optional[float] = None


class WorkflowRunUsageResponse(BaseModel):
    id: int
    workflow_id: int
    workflow_name: Optional[str]
    name: str
    created_at: str
    decibyl_token_usage: float
    call_duration_seconds: int
    recording_url: Optional[str] = None
    transcript_url: Optional[str] = None
    user_recording_url: Optional[str] = None
    bot_recording_url: Optional[str] = None
    recording_public_url: Optional[str] = None
    transcript_public_url: Optional[str] = None
    user_recording_public_url: Optional[str] = None
    bot_recording_public_url: Optional[str] = None
    public_access_token: Optional[str] = None
    phone_number: Optional[str] = Field(
        default=None,
        deprecated=True,
        description="Deprecated. Use caller_number and called_number instead.",
    )
    caller_number: Optional[str] = None
    called_number: Optional[str] = None
    call_type: Optional[str] = None
    mode: Optional[str] = None
    disposition: Optional[str] = None
    initial_context: Optional[Dict[str, Any]] = None
    gathered_context: Optional[Dict[str, Any]] = None
    # New USD field
    charge_usd: Optional[float] = None


class UsageHistoryResponse(BaseModel):
    runs: List[WorkflowRunUsageResponse]
    total_decibyl_tokens: float
    total_duration_seconds: int
    total_count: int
    page: int
    limit: int
    total_pages: int


class DailyUsageItem(BaseModel):
    date: str
    minutes: float
    cost_usd: Optional[float] = None
    decibyl_tokens: float
    call_count: int


class DailyUsageBreakdownResponse(BaseModel):
    breakdown: List[DailyUsageItem]
    total_minutes: float
    total_cost_usd: Optional[float] = None
    total_decibyl_tokens: float
    currency: Optional[str] = None
    # False when the account has no per-second price set, which is the normal
    # state of a fresh account rather than an error. The endpoint used to answer
    # 400 here, so a dashboard tile rendered a failure where it should render
    # "no data yet"; the flag lets the caller tell "not priced yet" apart from
    # "priced, but nobody has called".
    pricing_configured: bool = True


class AnswerSeizureRatioResponse(BaseModel):
    attempted: int
    connected: int
    # None rather than 0.0 when nothing was attempted — a fresh account with
    # no calls yet should not read as "every call went unanswered".
    asr: Optional[float] = None


class CostByOutcomeItem(BaseModel):
    disposition: str
    calls: int
    total_charged_paise: int
    cost_per_call_paise: Optional[int] = None


class OutcomesResponse(BaseModel):
    answer_seizure_ratio: AnswerSeizureRatioResponse
    cost_by_outcome: List[CostByOutcomeItem]


@router.get("/usage/outcomes", response_model=OutcomesResponse)
async def get_usage_outcomes(
    days: int = Query(7, ge=1, le=90, description="Number of days to include"),
    user: UserModel = Depends(get_user),
):
    """Answer rate, and cost per call for every outcome the account's calls
    actually produced.

    There is no fixed "booking" taxonomy across accounts — each workflow's
    own disposition strings show up in ``cost_by_outcome``, and a customer
    reads whichever row is their own success outcome. See
    ``services/reports/org_metrics.py`` for the definitions.
    """
    if not user.selected_organization_id:
        raise HTTPException(status_code=400, detail="No organization selected")

    async with db_client.async_session() as session:
        asr = await answer_seizure_ratio(
            session, organization_id=user.selected_organization_id, days=days
        )
        outcomes = await cost_by_outcome(
            session, organization_id=user.selected_organization_id, days=days
        )

    return OutcomesResponse(
        answer_seizure_ratio=AnswerSeizureRatioResponse(**asr),
        cost_by_outcome=[CostByOutcomeItem(**o) for o in outcomes],
    )


@router.get("/usage/current-period", response_model=CurrentUsageResponse)
async def get_current_period_usage(user: UserModel = Depends(get_user)):
    """Get current reporting-period usage for the user's organization."""
    if not user.selected_organization_id:
        raise HTTPException(status_code=400, detail="No organization selected")

    try:
        usage = await db_client.get_current_usage(user.selected_organization_id)
        return usage
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


FILTERS_DESCRIPTION = """\
JSON-encoded array of filter objects. Each object has the shape:

```json
{ "attribute": "<name>", "type": "<type>", "value": <value> }
```

Supported `attribute` / `type` / `value` combinations:

| attribute       | type          | value shape                                  | matches                                              |
|-----------------|---------------|----------------------------------------------|------------------------------------------------------|
| `runId`         | `number`      | `{ "value": 12345 }`                         | exact run id                                         |
| `workflowId`    | `number`      | `{ "value": 42 }`                            | exact agent (workflow) id                            |
| `campaignId`    | `number`      | `{ "value": 7 }`                             | exact campaign id                                    |
| `callerNumber`  | `text`        | `{ "value": "415555" }`                      | substring match on `initial_context.caller_number`   |
| `calledNumber`  | `text`        | `{ "value": "9911848" }`                     | substring match on `initial_context.called_number`   |
| `dispositionCode` | `multiSelect` | `{ "codes": ["XFER", "DNC"] }`             | any of the codes in `gathered_context.mapped_call_disposition` |
| `duration`      | `numberRange` | `{ "min": 60, "max": 300 }`                  | call duration (seconds), inclusive bounds            |

Unknown attributes and unsupported `type` values are silently ignored.

Date filtering on this endpoint is done via the dedicated `start_date` / `end_date` query params, not via a `dateRange` filter object.
"""


@router.get("/usage/runs", response_model=UsageHistoryResponse)
async def get_usage_history(
    start_date: Optional[str] = Query(
        None,
        description="ISO 8601 date-time string (UTC). Lower bound (inclusive) on `created_at`.",
        examples=["2026-04-01T00:00:00Z"],
    ),
    end_date: Optional[str] = Query(
        None,
        description="ISO 8601 date-time string (UTC). Upper bound (inclusive) on `created_at`.",
        examples=["2026-05-01T00:00:00Z"],
    ),
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=100),
    filters: Optional[str] = Query(
        None,
        description=FILTERS_DESCRIPTION,
        examples=[
            '[{"attribute":"callerNumber","type":"text","value":{"value":"415555"}}]',
            '[{"attribute":"campaignId","type":"number","value":{"value":7}},'
            '{"attribute":"duration","type":"numberRange","value":{"min":60,"max":300}}]',
            '[{"attribute":"dispositionCode","type":"multiSelect","value":{"codes":["XFER","DNC"]}}]',
        ],
    ),
    user: UserModel = Depends(get_user),
):
    """Get paginated workflow runs with usage for the organization."""
    if not user.selected_organization_id:
        raise HTTPException(status_code=400, detail="No organization selected")

    # Parse dates if provided
    start_dt = datetime.fromisoformat(start_date) if start_date else None
    end_dt = datetime.fromisoformat(end_date) if end_date else None

    # Parse filters if provided
    parsed_filters = None
    if filters:
        try:
            parsed_filters = json.loads(filters)
        except json.JSONDecodeError:
            raise HTTPException(status_code=400, detail="Invalid filters format")

    try:
        offset = (page - 1) * limit
        (
            runs,
            total_count,
            total_tokens,
            total_duration,
        ) = await db_client.get_usage_history(
            user.selected_organization_id,
            start_date=start_dt,
            end_date=end_dt,
            limit=limit,
            offset=offset,
            filters=parsed_filters,
        )

        total_pages = (total_count + limit - 1) // limit

        for run in runs:
            public_access_token = run.get("public_access_token")
            run["transcript_public_url"] = artifact_url(
                public_access_token, "transcript"
            )
            run["recording_public_url"] = artifact_url(public_access_token, "recording")
            run["user_recording_public_url"] = (
                artifact_url(public_access_token, "user_recording")
                if has_recording_track(run.get("extra"), "user")
                else None
            )
            run["bot_recording_public_url"] = (
                artifact_url(public_access_token, "bot_recording")
                if has_recording_track(run.get("extra"), "bot")
                else None
            )
            run.pop("extra", None)

        return {
            "runs": runs,
            "total_decibyl_tokens": total_tokens,
            "total_duration_seconds": total_duration,
            "total_count": total_count,
            "page": page,
            "limit": limit,
            "total_pages": total_pages,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/usage/runs/report")
async def download_usage_runs_report(
    start_date: Optional[str] = Query(
        None,
        description="ISO 8601 date-time string (UTC). Lower bound (inclusive) on `created_at`.",
    ),
    end_date: Optional[str] = Query(
        None,
        description="ISO 8601 date-time string (UTC). Upper bound (inclusive) on `created_at`.",
    ),
    filters: Optional[str] = Query(
        None,
        description=FILTERS_DESCRIPTION,
    ),
    user: UserModel = Depends(get_user),
) -> StreamingResponse:
    """Download a CSV of runs matching the same filters as `/usage/runs`."""
    if not user.selected_organization_id:
        raise HTTPException(status_code=400, detail="No organization selected")

    start_dt = datetime.fromisoformat(start_date) if start_date else None
    end_dt = datetime.fromisoformat(end_date) if end_date else None

    parsed_filters = None
    if filters:
        try:
            parsed_filters = json.loads(filters)
        except json.JSONDecodeError:
            raise HTTPException(status_code=400, detail="Invalid filters format")

    output, filename = await generate_usage_runs_report_csv(
        user.selected_organization_id,
        start_date=start_dt,
        end_date=end_dt,
        filters=parsed_filters,
    )

    return StreamingResponse(
        output,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/usage/daily-breakdown", response_model=DailyUsageBreakdownResponse)
async def get_daily_usage_breakdown(
    days: int = Query(7, ge=1, le=30, description="Number of days to include"),
    user: UserModel = Depends(get_user),
):
    """Get daily usage breakdown for the last N days. Only available for organizations with pricing."""
    if not user.selected_organization_id:
        raise HTTPException(status_code=400, detail="No organization selected")

    try:
        # An account with no price set has nothing to break down, which is the
        # ordinary state of a new account rather than a client error. Answer an
        # empty series with the flag cleared so the dashboard renders "no data
        # yet"; a 400 here made a correct guard look like a broken tile.
        org = await db_client.get_organization_by_id(user.selected_organization_id)
        if not org or org.price_per_second_usd is None:
            return DailyUsageBreakdownResponse(
                breakdown=[],
                total_minutes=0.0,
                total_cost_usd=None,
                total_decibyl_tokens=0.0,
                currency=None,
                pricing_configured=False,
            )

        # Calculate date range
        end_date = datetime.now()
        start_date = end_date - timedelta(days=days - 1)
        start_date = start_date.replace(hour=0, minute=0, second=0, microsecond=0)

        # Get daily breakdown
        breakdown = await db_client.get_daily_usage_breakdown(
            user.selected_organization_id,
            start_date,
            end_date,
            org.price_per_second_usd,
        )

        return breakdown
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# Spend and token analytics, for the account that owns them
#
# The aggregation behind these already existed — it powers the superadmin
# screens — and it already accepted an ``organization_id``. What did not exist
# was any way for a *customer* to see their own numbers: their usage page was
# a table of runs and a single token total, while staff had spend composition,
# per-model breakdown and context growth.
#
# So these routes are deliberately thin. They force the organization from the
# authenticated user and never read it from the request, which is the whole
# security difference between them and the superadmin equivalents.
# ---------------------------------------------------------------------------


def _range(days: int) -> tuple[date_cls, date_cls]:
    """A start/end date pair covering the last ``days`` days inclusive."""
    end = datetime.now().date()
    return end - timedelta(days=max(1, min(days, 365)) - 1), end


@router.get("/usage/tokens")
async def get_token_usage(
    days: int = Query(30, ge=1, le=365),
    granularity: str = Query("day", pattern="^(day|week|month)$"),
    user: UserModel = Depends(get_user),
) -> Dict[str, Any]:
    """Token consumption over time and by model, for this account.

    ``by_model`` is the half customers ask for and could not previously get:
    a total tells you spend went up, the split tells you which model did it and
    whether a cheaper one would serve.

    ``context_growth`` is the shape a per-call total cannot show. A voice agent
    resends the whole conversation every turn, so language-model spend grows
    with the square of call length — the fix for which is structural and never
    appears as a line item.
    """
    if not user.selected_organization_id:
        raise HTTPException(status_code=400, detail="No organization selected")

    from api.db import billing_dashboard_client as dash

    start, end = _range(days)
    async with db_client.async_session() as session:
        return {
            "range": {"start": start.isoformat(), "end": end.isoformat()},
            "granularity": granularity,
            # Same reason as `by_model` below, one step less obvious: spend and
            # tokens for the same period divide into a blended per-token price,
            # and on an account running a single model "blended" is that
            # model's price. Money for this account lives on /usage/spend,
            # split by component, which is their bill without naming a rate.
            "series": [
                {
                    "period": row["period"],
                    "tokens": row["tokens"],
                    "calls": row["calls"],
                    "minutes": row["minutes"],
                    "tokens_per_minute": row["tokens_per_minute"],
                }
                for row in await dash.token_usage_series(
                    session,
                    start=start,
                    end=end,
                    granularity=granularity,
                    organization_id=user.selected_organization_id,
                )
            ],
            # Re-projected, not passed through. The staff version carries
            # `cost_paise` and `paise_per_1k_tokens` *per named model*, and on a
            # managed key those are our price for that model — one division
            # away from the vendor's public rate card, and therefore one
            # division away from our markup, on every account that opens this
            # page. The account-wide spend on /usage/spend is their bill and
            # stays; what is removed is the per-model unit price, which is the
            # only part that discloses the margin.
            #
            # Tokens, calls and tokens-per-call survive, because the question
            # this table exists to answer — which model is burning the context,
            # would a cheaper one serve — is answered in tokens, not rupees.
            "by_model": [
                {
                    "provider": row["provider"],
                    "model": row["model"],
                    "tokens": row["tokens"],
                    "calls": row["calls"],
                    "tokens_per_call": row["tokens_per_call"],
                }
                for row in await dash.token_usage_by_model(
                    session,
                    start=start,
                    end=end,
                    organization_id=user.selected_organization_id,
                )
            ],
            "context_growth": await dash.context_growth_by_turn(
                session,
                start=start,
                end=end,
                organization_id=user.selected_organization_id,
            ),
        }


@router.get("/usage/spend")
async def get_spend_breakdown(
    days: int = Query(30, ge=1, le=365),
    user: UserModel = Depends(get_user),
) -> Dict[str, Any]:
    """Daily spend split by component, plus the current balance.

    The split is the point. A single total answers "how much" and nothing else;
    the breakdown is what tells a customer whether their bill is speech,
    language or carriage — and those have completely different fixes.

    ``balance_paise`` and ``burn`` are returned alongside because the question
    that follows a spend chart is always "how long does my credit last".
    """
    if not user.selected_organization_id:
        raise HTTPException(status_code=400, detail="No organization selected")

    from api.db import billing_dashboard_client as dash
    from api.services.billing.costing import current_balance_paise

    start, end = _range(days)
    async with db_client.async_session() as session:
        composition = await dash.cost_composition_series(
            session,
            start=start,
            end=end,
            organization_id=user.selected_organization_id,
        )
        balance = await current_balance_paise(
            session, organization_id=user.selected_organization_id
        )

    # Summed over the component keys by name rather than by a suffix. The rows
    # are shaped {"day": ..., "stt": n, "llm": n, ...} with plain integers, so
    # a "anything ending in _paise" rule silently totals zero and the burn rate
    # reads as "no spend" on an account that is spending.
    spent = sum(
        int(row.get(component) or 0)
        for row in composition
        for component in ("stt", "llm", "tts", "telephony", "platform")
    )
    # Averaged over the window rather than over days that had traffic: a
    # customer who calls on weekdays only still runs out on a Sunday, and a
    # projection that ignores the quiet days is optimistic in the direction
    # that hurts.
    daily = spent / max(1, len(composition)) if composition else 0
    days_remaining = int(balance / daily) if daily > 0 else None

    return {
        "range": {"start": start.isoformat(), "end": end.isoformat()},
        "series": composition,
        "balance_paise": balance,
        "spent_paise": spent,
        "burn": {
            "daily_average_paise": int(daily),
            "days_remaining": days_remaining,
        },
    }


@router.get("/usage/calls")
async def get_call_analytics(
    days: int = Query(30, ge=1, le=365),
    user: UserModel = Depends(get_user),
) -> Dict[str, Any]:
    """The shape of this account's call traffic, not just its total.

    The runs table below already answers "which calls happened". What it cannot
    answer is the set of questions that decide whether an agent is working:
    what fraction connect, how long they run, which agent carries the volume,
    and what hour of the day the phone actually rings.

    ``daily`` comes off the rollup and stays cheap at any volume; the
    breakdowns are grouped queries over the runs in the range.
    """
    if not user.selected_organization_id:
        raise HTTPException(status_code=400, detail="No organization selected")

    from api.db import billing_dashboard_client as dash

    start, end = _range(days)
    async with db_client.async_session() as session:
        analytics = await dash.call_analytics(
            session,
            start=start,
            end=end,
            organization_id=user.selected_organization_id,
        )
        daily = await dash.daily_series(
            session,
            start=start,
            end=end,
            organization_id=user.selected_organization_id,
        )

    return {
        "range": {"start": start.isoformat(), "end": end.isoformat()},
        # Re-projected, not passed through. `daily_series` is a staff query and
        # carries `provider_cost_paise` and `margin_paise` — what the vendors
        # charged us and what we kept. Handing a customer those two columns
        # publishes our markup on every account, permanently, and nothing in the
        # UI would have to render them for it to be readable in the response.
        "daily": [
            {
                "day": row["day"],
                "calls": row["calls"],
                "billable_minutes": row["billable_minutes"],
                "charged_paise": row["charged_paise"],
            }
            for row in daily
        ],
        **analytics,
    }
