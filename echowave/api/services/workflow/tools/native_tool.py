"""Execution for native (non-OAuth) telephony tools.

SMS is a plain outbound HTTP call, implemented here. DTMF needs direct
access to the live audio pipeline (it plays a tone through the call, it
doesn't call an external API) — see dtmf.py for tone generation and
pipecat_engine_custom_tools.py for where it's played into the pipeline.

v1 scope: SMS is Twilio-only, matching the existing Transfer Call tool's
"Twilio only" scope in this codebase. Other providers get a clear
unsupported-provider error rather than a silent failure.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

import httpx
from loguru import logger

from api.services.telephony.factory import load_credentials_for_transport


async def execute_sms_tool(
    tool: Any,
    arguments: Dict[str, Any],
    *,
    organization_id: int,
    telephony_configuration_id: Optional[Any] = None,
) -> Dict[str, Any]:
    """Send an SMS via the org's Twilio account."""
    to_number = (arguments or {}).get("to_number")
    message = (arguments or {}).get("message")
    if not to_number or not message:
        return {
            "status": "error",
            "error": "Both 'to_number' and 'message' are required.",
        }

    try:
        credentials = await load_credentials_for_transport(
            organization_id, telephony_configuration_id, expected_provider="twilio"
        )
    except ValueError:
        return {
            "status": "error",
            "error": (
                "SMS is currently only supported for Twilio telephony "
                "configurations."
            ),
        }

    account_sid = credentials.get("account_sid")
    auth_token = credentials.get("auth_token")
    from_numbers = credentials.get("from_numbers") or []
    if not account_sid or not auth_token or not from_numbers:
        return {
            "status": "error",
            "error": "Twilio SMS is not fully configured for this organization.",
        }

    from_number = from_numbers[0]
    url = f"https://api.twilio.com/2010-04-01/Accounts/{account_sid}/Messages.json"

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                url,
                data={"To": to_number, "From": from_number, "Body": message},
                auth=(account_sid, auth_token),
            )
            response.raise_for_status()
            data = response.json()
    except httpx.HTTPStatusError as e:
        logger.error(
            f"SMS send failed for tool '{tool.name}': {e.response.status_code} "
            f"{e.response.text}"
        )
        return {
            "status": "error",
            "error": f"Twilio rejected the message: {e.response.text}",
        }
    except httpx.RequestError as e:
        logger.error(f"SMS send request failed for tool '{tool.name}': {e}")
        return {"status": "error", "error": f"Request failed: {str(e)}"}

    return {
        "status": "success",
        "message_sid": data.get("sid"),
        "to": to_number,
    }
