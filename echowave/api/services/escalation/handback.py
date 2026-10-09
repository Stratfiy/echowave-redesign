"""The agent picks the caller back up after a person hands them back.

The carrier fetches the resume URL on the caller's live leg (see
``actions.hand_back``). What it gets is an ordinary media stream for a *new*
run of the same agent: a finished run cannot be restarted, and a new one keeps
billing, recording and transcripts per run as they are everywhere else. The
new run carries the person's note under ``escalation_handback`` in its initial
context, and ``EscalationRuntime.announce_handback`` turns that into the
agent's first words -- an LLMMessagesAppendFrame with the outcome, so it says
what was decided and carries on rather than greeting the caller again.
"""

from __future__ import annotations

import uuid
from typing import Any, Mapping

from loguru import logger

from api.db import db_client
from api.enums import CallType


def _call_type(run: Any) -> CallType:
    try:
        return CallType(getattr(run, "call_type", None) or CallType.INBOUND.value)
    except ValueError:
        return CallType.INBOUND


async def start_resumed_run(
    claim: Mapping[str, Any],
    *,
    provider: Any,
    original_run: Any,
    call_id: str | None,
):
    """Create the resumed run and return the carrier's stream response."""
    from api.services.call_concurrency import (
        CallConcurrencyLimitError,
        call_concurrency,
    )
    from api.services.telephony import stream_capability
    from api.utils.common import get_backend_endpoints

    organization_id = int(claim["organization_id"])
    workflow = await db_client.get_workflow(
        original_run.workflow_id, organization_id=organization_id
    )
    if workflow is None:
        raise LookupError("The agent for this call is gone.")
    try:
        slot = await call_concurrency.acquire_org_slot(
            organization_id, source="escalation_handback", timeout=0
        )
    except CallConcurrencyLimitError:
        logger.warning("No call slot free to hand a caller back to the agent")
        raise
    initial = dict(getattr(original_run, "initial_context", None) or {})
    initial["escalation_handback"] = {
        "note": claim.get("note"),
        "summary": claim.get("summary"),
        "human": claim.get("human"),
        "escalation_uuid": claim.get("escalation_uuid"),
        "previous_run_id": original_run.id,
    }
    suffix = int(uuid.uuid4().hex[:8], 16) % 100000000
    try:
        run = await db_client.create_workflow_run(
            f"WR-TEL-BACK-{suffix:08d}",
            workflow.id,
            original_run.mode,
            user_id=workflow.user_id,
            call_type=_call_type(original_run),
            initial_context=initial,
            gathered_context={
                "call_id": call_id
                or (getattr(original_run, "gathered_context", None) or {}).get(
                    "call_id"
                )
            },
            organization_id=organization_id,
        )
        await call_concurrency.bind_workflow_run(slot, run.id)
    except Exception:
        await call_concurrency.release_slot(slot)
        raise
    backend_endpoint, _ = await get_backend_endpoints()
    websocket_url = await stream_capability.stream_url(
        workflow_id=workflow.id,
        organization_id=organization_id,
        workflow_run_id=run.id,
    )
    logger.info(
        "Escalation {} handed back: run {} continues as run {}",
        claim.get("escalation_uuid"),
        original_run.id,
        run.id,
    )
    return await provider.start_inbound_stream(
        websocket_url=websocket_url,
        workflow_run_id=run.id,
        normalized_data=None,
        backend_endpoint=backend_endpoint,
    )


__all__ = ["start_resumed_run"]
