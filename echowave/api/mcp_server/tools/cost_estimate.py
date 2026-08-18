"""MCP tool that prices a stack before the agent makes its first call.

This is the one thing in the authoring loop that no competing platform can do.
Vapi, Retell and Bolna all discover the cost of a configuration on the invoice;
Decibyl prices it from the same rate card the invoice will use, at the calling
account's own negotiated platform rate. Exposing it here is what lets a chat
answer "what will this cost me a month?" in the same breath as it builds the
agent.

It is read-only and changes nothing, so it is safe to call as often as the
model likes — including once per candidate stack when the user is choosing
between a cheap voice and a good one.
"""

from __future__ import annotations

from typing import Any, Optional

from api.db import db_client
from api.mcp_server.auth import authenticate_mcp_request
from api.mcp_server.tracing import traced_tool
from api.services.billing.estimator import estimate_cost_per_minute


# Rupees, from paise, for the figures a person reads. The underlying integers
# stay in paise everywhere else — see services/billing/money.py.
def _rupees(paise: int) -> float:
    return round(paise / 100, 4)


@traced_tool
async def estimate_agent_cost(
    stt_provider: Optional[str] = None,
    stt_model: str = "",
    llm_provider: Optional[str] = None,
    llm_model: str = "",
    tts_provider: Optional[str] = None,
    tts_model: str = "",
    telephony_provider: Optional[str] = None,
    calls_per_month: Optional[int] = None,
    typical_call_seconds: Optional[int] = None,
) -> dict[str, Any]:
    """Price an agent's provider stack per minute, and per month if asked.

    Call this after building or changing an agent, and whenever the user asks
    what something costs or compares two voices or models. Report the per-minute
    figure in rupees and, when you know the call volume, the monthly figure —
    the monthly number is the one people actually decide on.

    Every component is optional, so a half-configured agent still prices: the
    components you pass are costed and the rest are simply absent from the
    total. Passing a provider with no rate on file is reported in `unpriced`
    rather than silently costed at zero, so a quote never quietly understates.

    Use it to answer trade-offs concretely. Switching Sarvam Bulbul v3 to v2, or
    ElevenLabs to Sarvam, changes the per-minute price by more than any other
    single choice, because speech synthesis is most of what a call costs.

    Args:
        stt_provider / stt_model: e.g. "sarvam", "deepgram" with "nova-3".
        llm_provider / llm_model: e.g. "google" with "gemini-2.5-flash".
        tts_provider / tts_model: e.g. "sarvam" with "bulbul:v2".
        telephony_provider: e.g. "plivo", "twilio".
        calls_per_month: Optional. With `typical_call_seconds`, returns a
            monthly projection alongside the per-minute rate.
        typical_call_seconds: Optional. Average call length for the projection.

    Returns:
        `per_minute` with the rupee total and its breakdown into agent,
        telephony and platform; `usd_per_minute` and `pulse_seconds`; `lines`
        itemising each component; `monthly` when volume was supplied; and
        `unpriced` naming any component with no rate on file. Error codes:
        `no_organization` when the session has no selected organization, which
        needs the user to pick one in the UI.
    """
    user = await authenticate_mcp_request()
    organization_id = user.selected_organization_id
    if organization_id is None:
        return {
            "estimated": False,
            "error_code": "no_organization",
            "error": (
                "This session has no organization selected, so there is no "
                "platform rate to price against. Ask the user to select one."
            ),
        }

    async with db_client.async_session() as session:
        estimate = await estimate_cost_per_minute(
            session,
            organization_id=organization_id,
            stt_provider=stt_provider,
            stt_model=stt_model,
            llm_provider=llm_provider,
            llm_model=llm_model,
            tts_provider=tts_provider,
            tts_model=tts_model,
            telephony_provider=telephony_provider,
        )

    total_paise = estimate.total_paise_per_minute
    result: dict[str, Any] = {
        "estimated": True,
        "per_minute": {
            "total_rupees": _rupees(total_paise),
            "agent_rupees": _rupees(estimate.agent_paise_per_minute),
            "telephony_rupees": _rupees(estimate.telephony_paise_per_minute),
            "platform_rupees": _rupees(estimate.platform_paise_per_minute),
        },
        # Quoted in dollars as well, because the platform fee is set in dollars
        # and a customer comparing us against Vapi or Bolna is comparing a
        # dollar figure. See services/billing/money.py.
        "usd_per_minute": round(estimate.total_micros_usd_per_minute / 1_000_000, 5),
        "pulse_seconds": estimate.pulse_seconds,
        "lines": [
            {
                "component": line.component,
                "provider": line.provider,
                "model": line.model,
                "rupees_per_minute": _rupees(line.paise_per_minute),
                # True when the price came from a provider-wide row rather than
                # one for this exact model. Worth repeating to the user: it is
                # the case most likely to be wrong.
                "rate_is_provider_fallback": line.rate_is_provider_fallback,
            }
            for line in estimate.lines
        ],
    }

    # Components the rate card could not price. Surfaced rather than dropped:
    # a quote missing its most expensive line reads as a cheap quote.
    if estimate.unpriced:
        result["unpriced"] = list(estimate.unpriced)
        result["unpriced_warning"] = (
            "These components have no rate on file, so the total excludes them "
            "and is an underestimate. Say so when quoting."
        )

    if calls_per_month and typical_call_seconds:
        minutes = round(calls_per_month * typical_call_seconds / 60)
        result["monthly"] = {
            "calls": calls_per_month,
            "typical_call_seconds": typical_call_seconds,
            "minutes": minutes,
            "total_rupees": round(_rupees(total_paise) * minutes, 2),
        }
        # Billing rounds each call up to a whole 15-second pulse, so a book of
        # short calls bills slightly more than duration alone implies. Named
        # rather than folded in, because the pulse is a selling point and a
        # silent adjustment would make the quote look wrong.
        result["monthly"]["note"] = (
            f"Projection from call duration. Billing rounds each call up to a "
            f"whole {estimate.pulse_seconds}-second pulse, which is still well "
            f"under the whole minute most platforms charge."
        )

    return result
