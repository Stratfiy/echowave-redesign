"""The ladder's ``Dialer``, on a real carrier, through the existing transfer path.

Every dial is the same ``provider.transfer_call`` the transfer tool has always
used -- the person's leg rings, hears the private briefing, and waits in the
conference named for the caller's call -- with two additions:

* the provider's ``escalation_dial_options`` (answering-machine detection on
  Plivo), and
* a Redis subscription opened *before* the dial, so an outcome that comes
  back at once (busy, rejected) is not published into an empty channel.

``report_leg_outcome`` is the other half: what a carrier callback calls to
say how a leg went, in the event vocabulary the transfer path already uses.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from loguru import logger

from api.services.escalation import ladder
from api.services.escalation.policy import TransferTarget
from api.services.telephony.call_transfer_manager import get_call_transfer_manager
from api.services.telephony.transfer_event_protocol import (
    TransferContext,
    TransferEvent,
    TransferEventType,
    TransferRedisChannels,
)
from api.utils.common import get_backend_endpoints

#: TransferEvent.reason -> ladder outcome. Anything unlisted is a failure,
#: never a person: a reason nobody mapped must not bridge a caller.
_REASONS = {
    "answered_by_machine": ladder.MACHINE,
    "no_answer": ladder.NO_ANSWER,
    "busy": ladder.BUSY,
    "declined": ladder.DECLINED,
    "timeout": ladder.TIMEOUT,
}


def outcome_of(event: TransferEvent | None) -> str:
    if event is None:
        return ladder.TIMEOUT
    if event.type == TransferEventType.DESTINATION_ANSWERED or (
        event.type == TransferEventType.DESTINATION_ANSWERED.value
    ):
        return ladder.HUMAN
    return _REASONS.get(event.reason or "", ladder.FAILED)


class ProviderDialer:
    """Dials people for one escalation on one call."""

    def __init__(
        self,
        *,
        provider: Any,
        original_call_sid: str,
        workflow_run_id: int | None,
        escalation_uuid: str,
        tool_uuid: str = "escalation",
    ):
        self._provider = provider
        self._call_sid = original_call_sid
        self._run_id = workflow_run_id
        self._escalation_uuid = escalation_uuid
        self._tool_uuid = tool_uuid
        self.conference_name = f"transfer-{original_call_sid}"
        self._ready: dict[str, asyncio.Event] = {}
        self._legs: dict[str, str | None] = {}

    def watch(self, transfer_id: str, timeout: float) -> "asyncio.Task[str]":
        ready = asyncio.Event()
        self._ready[transfer_id] = ready
        return asyncio.create_task(self._wait(transfer_id, timeout, ready))

    async def _wait(
        self, transfer_id: str, timeout: float, ready: asyncio.Event
    ) -> str:
        manager = await get_call_transfer_manager()
        redis = await manager._get_redis()
        pubsub = redis.pubsub()
        channel = TransferRedisChannels.transfer_events(transfer_id)
        try:
            await pubsub.subscribe(channel)
            ready.set()

            async def first_outcome() -> str:
                async for message in pubsub.listen():
                    if message.get("type") != "message":
                        continue
                    try:
                        event = TransferEvent.from_json(message["data"])
                    except Exception:  # noqa: BLE001
                        continue
                    return outcome_of(event)
                return ladder.TIMEOUT

            return await asyncio.wait_for(first_outcome(), timeout=timeout)
        except asyncio.TimeoutError:
            return ladder.TIMEOUT
        finally:
            ready.set()
            try:
                await pubsub.unsubscribe(channel)
                await pubsub.aclose()
            except Exception:  # noqa: BLE001
                pass

    async def dial(
        self,
        target: TransferTarget,
        *,
        transfer_id: str,
        ring_timeout: int,
        briefing: str,
    ) -> None:
        ready = self._ready.get(transfer_id)
        if ready is not None:
            try:
                await asyncio.wait_for(ready.wait(), timeout=2.0)
            except asyncio.TimeoutError:
                logger.warning("Escalation listener not ready; dialling anyway")
        manager = await get_call_transfer_manager()
        context = TransferContext(
            transfer_id=transfer_id,
            call_sid=None,
            target_number=target.number,
            tool_uuid=self._tool_uuid,
            original_call_sid=self._call_sid,
            conference_name=self.conference_name,
            initiated_at=time.time(),
            workflow_run_id=self._run_id,
            escalation_uuid=self._escalation_uuid,
        )
        await manager.store_transfer_context(context)
        backend_endpoint, _ = await get_backend_endpoints()
        options = self._provider.escalation_dial_options(
            transfer_id=transfer_id, backend_endpoint=backend_endpoint
        )
        try:
            result = await self._provider.transfer_call(
                destination=target.number,
                transfer_id=transfer_id,
                conference_name=self.conference_name,
                timeout=ring_timeout,
                briefing=briefing,
                **options,
            )
        except Exception:
            await manager.remove_transfer_context(transfer_id)
            raise
        context.call_sid = (result or {}).get("call_sid")
        self._legs[transfer_id] = context.call_sid
        await manager.store_transfer_context(context)

    async def drop(self, transfer_id: str) -> None:
        """Forget the leg so a late answer cannot be bridged, then end it."""
        manager = await get_call_transfer_manager()
        await manager.remove_transfer_context(transfer_id)
        call_id = self._legs.get(transfer_id)
        if call_id:
            try:
                await self._provider.hangup_transfer_leg(call_id)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Could not end an unanswered leg: {}", exc)


async def report_leg_outcome(
    transfer_id: str,
    *,
    human: bool,
    reason: str | None = None,
    call_id: str | None = None,
) -> None:
    """Publish how a person's leg went, for the ladder waiting on it."""
    manager = await get_call_transfer_manager()
    context = await manager.get_transfer_context(transfer_id)
    event = TransferEvent(
        type=TransferEventType.DESTINATION_ANSWERED
        if human
        else TransferEventType.TRANSFER_FAILED,
        transfer_id=transfer_id,
        original_call_sid=context.original_call_sid if context else "",
        transfer_call_sid=call_id,
        conference_name=context.conference_name if context else None,
        status="success" if human else "transfer_failed",
        action="destination_answered" if human else "transfer_failed",
        reason=None if human else (reason or "call_failed"),
    )
    await manager.publish_transfer_event(event)


__all__ = ["ProviderDialer", "outcome_of", "report_leg_outcome"]
