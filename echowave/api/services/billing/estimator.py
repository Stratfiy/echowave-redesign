"""Forward-looking cost estimate for a chosen agent stack.

Everything else in this package prices a call after it happens. This module
answers the question an operator asks *before*: "if I build an agent on this
STT, LLM, TTS and telephony provider, what will a minute cost me?"

The rates are the same effective-dated rows the receipts use, so an estimate
and the invoice it predicts can never drift apart. What has to be assumed is
consumption — tokens and characters per minute — and rather than ship a
constant, that assumption is measured from our own completed calls on that
exact model. A vendor's generic figure would be a guess about someone else's
traffic; the median of our own is a statement about ours.

Falls back to a documented default only where we have not yet run enough calls
on a model to measure it, and always reports which of the two it used, because
an estimate whose provenance is invisible invites more trust than it has
earned.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import Float, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.db.models import CallCostItemModel, WorkflowRunModel
from api.enums import CostComponent
from api.services.billing.money import cost_paise, platform_fee_paise
from api.services.billing.rates import resolve_platform_rate, resolve_provider_rate

# Consumption per connected minute, used only when we have no measured history
# for a model. Derived from a typical Indian voice-agent turn: roughly six
# exchanges a minute, ~150 output tokens and ~380 spoken characters each, with
# the prompt re-sent per turn dominating input tokens.
DEFAULT_TOKENS_PER_MINUTE = 1_400
DEFAULT_CHARACTERS_PER_MINUTE = 2_300

# Below this many costed calls the median is noise, so the default is used.
MIN_CALLS_FOR_MEASURED_ASSUMPTION = 20

# How far back to measure. Long enough to be stable, short enough that a prompt
# rewrite six months ago does not distort today's estimate.
ASSUMPTION_WINDOW_DAYS = 30


@dataclass(frozen=True)
class EstimateLine:
    """One component of the per-minute estimate."""

    component: str
    provider: str | None
    model: str | None
    # Raw units consumed per minute, in the unit the rate is quoted against.
    units_per_minute: int
    unit_rate_mpaise: int
    paise_per_minute: int
    # "measured" when units_per_minute came from our own calls on this model,
    # "default" when we fell back, "exact" when the quantity is not an
    # assumption at all (telephony and the platform fee are per-minute rates).
    basis: str
    # True when the rate itself came from the provider-wide fallback row rather
    # than one quoted for this specific model.
    rate_is_provider_fallback: bool = False


@dataclass(frozen=True)
class CostEstimate:
    lines: tuple[EstimateLine, ...]
    total_paise_per_minute: int
    agent_paise_per_minute: int
    telephony_paise_per_minute: int
    platform_paise_per_minute: int
    # Components we were asked about but hold no rate for. Reported rather than
    # priced at zero, exactly as the cost engine does for a real call.
    unpriced: tuple[str, ...]


async def _measured_units_per_minute(
    session: AsyncSession,
    *,
    component: CostComponent,
    provider: str,
    model: str,
) -> int | None:
    """Median units per connected minute on this model, from our own calls.

    None when we have not run enough of them to say. Uses the median rather
    than the mean because one runaway call — a caller who never hung up, a
    prompt-injection loop — would drag an average badly.
    """
    since = datetime.now(UTC) - timedelta(days=ASSUMPTION_WINDOW_DAYS)

    conditions = [
        CallCostItemModel.component == component.value,
        CallCostItemModel.provider == provider,
        WorkflowRunModel.billable_seconds > 0,
        WorkflowRunModel.costed_at.isnot(None),
        WorkflowRunModel.created_at >= since,
    ]
    # An empty model means "whatever we ran", so it must not filter.
    if model:
        conditions.append(CallCostItemModel.model == model)

    per_minute = cast(CallCostItemModel.units, Float) * 60.0 / cast(
        WorkflowRunModel.billable_seconds, Float
    )

    row = (
        await session.execute(
            select(
                func.percentile_cont(0.5).within_group(per_minute).label("median"),
                func.count().label("calls"),
            )
            .select_from(CallCostItemModel)
            .join(
                WorkflowRunModel,
                CallCostItemModel.workflow_run_id == WorkflowRunModel.id,
            )
            .where(*conditions)
        )
    ).one()

    if not row.calls or row.calls < MIN_CALLS_FOR_MEASURED_ASSUMPTION:
        return None
    if row.median is None:
        return None
    return max(int(round(row.median)), 0)


async def _inference_line(
    session: AsyncSession,
    *,
    component: CostComponent,
    provider: str,
    model: str,
    at: datetime,
    default_units: int,
) -> EstimateLine | None:
    """A per-minute line for a token- or character-metered component."""
    rate = await resolve_provider_rate(
        session, provider=provider, component=component, at=at, model=model
    )
    if rate is None:
        return None

    measured = await _measured_units_per_minute(
        session, component=component, provider=provider, model=model
    )
    units = measured if measured is not None else default_units

    return EstimateLine(
        component=component.value,
        provider=provider,
        model=model or None,
        units_per_minute=units,
        unit_rate_mpaise=rate.rate_mpaise,
        paise_per_minute=cost_paise(
            quantity=units, rate_mpaise=rate.rate_mpaise, unit=rate.unit
        ),
        basis="measured" if measured is not None else "default",
        rate_is_provider_fallback=bool(model) and rate.model == "",
    )


async def _per_minute_line(
    session: AsyncSession,
    *,
    component: CostComponent,
    provider: str,
    at: datetime,
) -> EstimateLine | None:
    """A line for a component already quoted per minute — STT and telephony.

    No consumption assumption is involved: a minute of call is a minute of
    audio, so this is exact rather than estimated.
    """
    rate = await resolve_provider_rate(
        session, provider=provider, component=component, at=at
    )
    if rate is None:
        return None

    return EstimateLine(
        component=component.value,
        provider=provider,
        model=None,
        units_per_minute=60,
        unit_rate_mpaise=rate.rate_mpaise,
        paise_per_minute=cost_paise(
            quantity=60, rate_mpaise=rate.rate_mpaise, unit=rate.unit
        ),
        basis="exact",
    )


async def estimate_cost_per_minute(
    session: AsyncSession,
    *,
    organization_id: int,
    stt_provider: str | None = None,
    stt_model: str = "",
    llm_provider: str | None = None,
    llm_model: str = "",
    tts_provider: str | None = None,
    tts_model: str = "",
    telephony_provider: str | None = None,
    at: datetime | None = None,
) -> CostEstimate:
    """What one connected minute costs on this stack, itemised.

    Priced with the account's own platform rate, so two accounts on different
    negotiated rates see their own number rather than a list price.
    """
    at = at or datetime.now(UTC)
    lines: list[EstimateLine] = []
    unpriced: list[str] = []

    if stt_provider:
        line = await _per_minute_line(
            session, component=CostComponent.STT, provider=stt_provider, at=at
        )
        lines.append(line) if line else unpriced.append(f"stt:{stt_provider}")

    if llm_provider:
        line = await _inference_line(
            session,
            component=CostComponent.LLM,
            provider=llm_provider,
            model=llm_model,
            at=at,
            default_units=DEFAULT_TOKENS_PER_MINUTE,
        )
        lines.append(line) if line else unpriced.append(f"llm:{llm_provider}")

    if tts_provider:
        line = await _inference_line(
            session,
            component=CostComponent.TTS,
            provider=tts_provider,
            model=tts_model,
            at=at,
            default_units=DEFAULT_CHARACTERS_PER_MINUTE,
        )
        lines.append(line) if line else unpriced.append(f"tts:{tts_provider}")

    if telephony_provider:
        line = await _per_minute_line(
            session,
            component=CostComponent.TELEPHONY,
            provider=telephony_provider,
            at=at,
        )
        lines.append(line) if line else unpriced.append(
            f"telephony:{telephony_provider}"
        )

    platform = await resolve_platform_rate(
        session, organization_id=organization_id, at=at
    )
    platform_line = EstimateLine(
        component=CostComponent.PLATFORM.value,
        provider=None,
        model=None,
        units_per_minute=1,
        unit_rate_mpaise=platform.rate_mpaise,
        # The platform rate is already quoted per minute, so one minute costs
        # the rate itself. Priced through platform_fee_paise rather than by
        # dividing here, so the estimate rounds exactly the way the invoice will.
        paise_per_minute=platform_fee_paise(
            billable_minutes=1, rate_mpaise=platform.rate_mpaise
        ),
        basis="exact",
    )
    lines.append(platform_line)

    agent = sum(
        line.paise_per_minute
        for line in lines
        if line.component in {"stt", "llm", "tts"}
    )
    telephony = sum(
        line.paise_per_minute for line in lines if line.component == "telephony"
    )

    return CostEstimate(
        lines=tuple(lines),
        # Defined as the sum of its own lines, the same rule the receipt uses,
        # so an estimate can always be reconciled against its breakdown.
        total_paise_per_minute=sum(line.paise_per_minute for line in lines),
        agent_paise_per_minute=agent,
        telephony_paise_per_minute=telephony,
        platform_paise_per_minute=platform_line.paise_per_minute,
        unpriced=tuple(unpriced),
    )
