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
            }
            for line in estimate.lines
        ],
        # Components asked about that we hold no rate for. Surfaced rather than
        # silently priced at zero, which would understate the estimate.
        "unpriced": list(estimate.unpriced),
        # The two fields the estimate is *about*, and both were being computed
        # and then dropped here. Without pulse_seconds the UI rendered "billed
        # in s pulses" — the differentiator stated as a typo — and without the
        # USD figure the dual-currency line silently disappeared.
        "pulse_seconds": estimate.pulse_seconds,
        "total_micros_usd_per_minute": estimate.total_micros_usd_per_minute,
    }
