"""Plivo-specific call operation strategies.

Caller-side leg of the conference-based transfer. The destination is dialled
into the conference by ``PlivoProvider.transfer_call``; this strategy moves the
caller into that same conference when the pipeline tears down with
``EndTaskReason.TRANSFER_CALL``.

Plivo has no "join conference" API call. What it has is live-call redirection:
POST to the call with a new ``aleg_url`` and Plivo fetches that URL and runs
whatever XML it returns on the in-progress leg. So the caller is not *moved*
into the conference so much as *re-pointed* at XML whose only element is the
conference — see ``/plivo/transfer-caller/{conference_name}`` in ``routes.py``.

API reference:
- Transfer a live call: https://www.plivo.com/docs/voice/api/call#transfer-a-call
"""

from typing import Any, Dict
from urllib.parse import quote

import aiohttp
from loguru import logger
from pipecat.serializers.call_strategies import TransferStrategy

from api.utils.common import get_backend_endpoints


class PlivoConferenceStrategy(TransferStrategy):
    """Redirects the caller's live leg into the transfer conference."""

    async def execute_transfer(self, context: Dict[str, Any]) -> bool:
        call_uuid = context.get("call_id")
        auth_id = context.get("auth_id")
        auth_token = context.get("auth_token")

        if not call_uuid or not auth_id or not auth_token:
            logger.error(
                "[Plivo Transfer] Missing call_id or credentials; cannot move "
                "the caller into the conference."
            )
            return False

        transfer_context = await self._find_transfer_context_for_call(call_uuid)
        if not transfer_context:
            logger.error(
                f"[Plivo Transfer] No active transfer context for call {call_uuid}"
            )
            return False

        conference_name = transfer_context.conference_name
        if not conference_name:
            logger.error(
                f"[Plivo Transfer] Transfer context "
                f"{transfer_context.transfer_id} carries no conference name."
            )
            await self._cleanup_transfer_context(transfer_context.transfer_id)
            return False

        backend_endpoint, _ = await get_backend_endpoints()
        caller_url = (
            f"{backend_endpoint}/api/v1/telephony/plivo/transfer-caller/"
            f"{quote(conference_name, safe='')}"
        )

        logger.info(
            f"[Plivo Transfer] Redirecting caller {call_uuid} into conference "
            f"{conference_name} (transfer={transfer_context.transfer_id})"
        )

        endpoint = f"https://api.plivo.com/v1/Account/{auth_id}/Call/{call_uuid}/"
        # `legs=aleg` is the caller's own leg. Without it Plivo defaults to
        # redirecting both legs, which on a call that has no b-leg yet is a
        # silent no-op rather than an error.
        payload = {
            "legs": "aleg",
            "aleg_url": caller_url,
            "aleg_method": "POST",
        }

        try:
            async with aiohttp.ClientSession() as session:
                auth = aiohttp.BasicAuth(auth_id, auth_token)
                async with session.post(endpoint, json=payload, auth=auth) as response:
                    body = await response.text()
                    # Plivo answers 202 Accepted for a redirect it has queued.
                    if response.status not in (200, 202):
                        logger.error(
                            f"[Plivo Transfer] Redirect of caller {call_uuid} into "
                            f"conference {conference_name} failed: "
                            f"status={response.status} body={body}"
                        )
                        return False

                    logger.info(
                        f"[Plivo Transfer] Caller {call_uuid} redirected into "
                        f"conference {conference_name}"
                    )
                    return True

        except Exception as e:
            logger.error(f"[Plivo Transfer] Failed to redirect caller leg: {e}")
            return False

        finally:
            # The context has done its job either way. Leaving it behind means
            # the next transfer on this call resolves the stale one.
            await self._cleanup_transfer_context(transfer_context.transfer_id)

    async def _find_transfer_context_for_call(self, call_uuid: str):
        try:
            from api.services.telephony.call_transfer_manager import (
                get_call_transfer_manager,
            )

            manager = await get_call_transfer_manager()
            return await manager.find_transfer_context_for_call(call_uuid)

        except Exception as e:
            logger.error(f"[Plivo Transfer] Error finding transfer context: {e}")
            return None

    async def _cleanup_transfer_context(self, transfer_id: str) -> None:
        try:
            from api.services.telephony.call_transfer_manager import (
                get_call_transfer_manager,
            )

            manager = await get_call_transfer_manager()
            await manager.remove_transfer_context(transfer_id)
        except Exception as e:
            logger.error(f"[Plivo Transfer] Error cleaning up transfer context: {e}")
