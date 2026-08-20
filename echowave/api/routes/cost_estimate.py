"""Per-minute cost estimate for an agent configuration.

Answers "what will this stack cost me a minute?" while the agent is being
configured, so the choice of model is made with its price visible rather than
discovered on the first invoice.

Unlike the admin billing dashboard this is a product surface for any signed-in
user, and it is scoped to the caller's own organization: the platform rate it
prices with is that account's negotiated rate, never another's.
"""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from api.db import db_client
from api.db.models import UserModel
from api.services.auth.depends import get_user
from api.services.billing.estimator import estimate_cost_per_minute
from api.services.billing.realtime_pricing import CallShape, compare_realtime_models
from api.services.billing.realtime_rates import AS_OF, REALTIME_PRICES, SEED_NOTE

router = APIRouter(prefix="/cost-estimate", tags=["cost-estimate"])


class CostEstimateRequest(BaseModel):
    """The stack to price. Every component is optional so the estimate can be
    shown while the agent is still half-configured."""

    stt_provider: str | None = None
    stt_model: str = ""
    llm_provider: str | None = None
    llm_model: str = ""
    tts_provider: str | None = None
    tts_model: str = ""
    telephony_provider: str | None = None
    #: Components the account runs on its own vendor key — "stt", "llm", "tts".
    #: Those cost the customer nothing from us, so they price at zero.
    customer_keyed: list[str] = []


@router.post("/per-minute")
async def get_cost_per_minute(
    payload: CostEstimateRequest,
    user: UserModel = Depends(get_user),
) -> dict[str, Any]:
    organization_id = user.selected_organization_id
    if organization_id is None:
        raise HTTPException(status_code=400, detail="No organization selected")

    async with db_client.async_session() as session:
        estimate = await estimate_cost_per_minute(
            session,
            organization_id=organization_id,
            stt_provider=payload.stt_provider,
            stt_model=payload.stt_model,
            llm_provider=payload.llm_provider,
            llm_model=payload.llm_model,
            tts_provider=payload.tts_provider,
            tts_model=payload.tts_model,
            telephony_provider=payload.telephony_provider,
            customer_keyed=set(payload.customer_keyed),
        )

    return {
        "total_paise_per_minute": estimate.total_paise_per_minute,
        # The three groups the breakdown bar is split into.
        "agent_paise_per_minute": estimate.agent_paise_per_minute,
        "telephony_paise_per_minute": estimate.telephony_paise_per_minute,
        "platform_paise_per_minute": estimate.platform_paise_per_minute,
        "lines": [
            {
                "component": line.component,
                "provider": line.provider,
                "model": line.model,
                "units_per_minute": line.units_per_minute,
                "unit_rate_mpaise": line.unit_rate_mpaise,
                "paise_per_minute": line.paise_per_minute,
                "basis": line.basis,
                "rate_is_provider_fallback": line.rate_is_provider_fallback,
                "customer_keyed": line.customer_keyed,
            }
            for line in estimate.lines
        ],
        # Components asked about that we hold no rate for. Surfaced rather than
        # silently priced at zero, which would understate the estimate.
        "unpriced": list(estimate.unpriced),
        # The two facts a model picker has to show, and the reason this screen
        # read as a broken calculator: a component priced against the
        # provider's default rather than the chosen model shows the same
        # number for every unpriced model on that provider.
        "approximated": list(estimate.approximated),
        "customer_keyed": list(estimate.customer_keyed),
        # The two fields the estimate is *about*, and both were being computed
        # and then dropped here. Without pulse_seconds the UI rendered "billed
        # in s pulses" — the differentiator stated as a typo — and without the
        # USD figure the dual-currency line silently disappeared.
        "pulse_seconds": estimate.pulse_seconds,
        "total_micros_usd_per_minute": estimate.total_micros_usd_per_minute,
    }


class RealtimeEstimateRequest(BaseModel):
    """The call to price. Defaults describe a typical Indian outbound call —
    three minutes, ten-second turns, about two-thirds of the clock spent
    speaking — and every one of them is worth changing to match a real book."""

    duration_seconds: int = 180
    seconds_per_turn: float = 10.0
    agent_talk_share: float = 0.45
    caller_talk_share: float = 0.30
    #: Off by default. Assuming the provider's context cache is being hit
    #: roughly halves the answer, and it is not free to hit.
    caching_enabled: bool = False


@router.post("/realtime")
async def compare_realtime(
    payload: RealtimeEstimateRequest,
    user: UserModel = Depends(get_user),
) -> dict[str, Any]:
    """Every speech-to-speech model priced against the same call, cheapest first.

    Needs no database and no organization: these are vendor list prices, not
    this account's negotiated ones. The platform fee is deliberately absent —
    the question here is what the *provider* costs, so that the choice between
    a realtime model and a cascaded stack can be made on the part that differs.
    """
    try:
        shape = CallShape(
            duration_seconds=payload.duration_seconds,
            seconds_per_turn=payload.seconds_per_turn,
            agent_talk_share=payload.agent_talk_share,
            caller_talk_share=payload.caller_talk_share,
            caching_enabled=payload.caching_enabled,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    estimates = compare_realtime_models(REALTIME_PRICES, shape)

    return {
        "models": [
            {
                "provider": estimate.provider,
                "model": estimate.model,
                "label": estimate.label,
                "micros_usd_per_minute": estimate.micros_usd_per_minute,
                "total_micros_usd": estimate.total_micros_usd,
                "fresh_input_micros_usd": estimate.fresh_input_micros_usd,
                "repeated_input_micros_usd": estimate.repeated_input_micros_usd,
                "output_micros_usd": estimate.output_micros_usd,
                "fresh_input_tokens": estimate.fresh_input_tokens,
                "repeated_input_tokens": estimate.repeated_input_tokens,
                "output_tokens": estimate.output_tokens,
                # The share of the bill that is context arriving again. The
                # single number that explains why this is not the vendor's
                # quoted per-minute price.
                "repeated_share": estimate.repeated_share,
                "caching_applied": estimate.caching_applied,
                "basis": estimate.basis,
            }
            for estimate in estimates
        ],
        "turns": shape.turns,
        "context_multiple": shape.context_multiple,
        # Provenance travels with the numbers, so a list price is never
        # mistaken on screen for something measured.
        "as_of": AS_OF,
        "note": SEED_NOTE,
    }
