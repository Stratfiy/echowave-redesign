"""Every carrier callback proves who sent it before anything is processed.

Most of the callback routes already verify their provider's signature
against the run's own credentials. The ones keyed by a transfer id (the
destination leg of a warm transfer) and Cloudonix's three did not, and a
forged POST there could complete a transfer, end a call or file a CDR.
One helper, fail closed: no run, no provider, no signature, no processing.
"""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException, Request
from loguru import logger

from api.db import db_client
from api.services.telephony.call_transfer_manager import get_call_transfer_manager
from api.services.telephony.factory import get_telephony_provider_for_run


async def provider_for_run_id(workflow_run_id: int) -> tuple[Any, Any] | None:
    """(provider, workflow_run) for a run, or None when either is missing."""
    workflow_run = await db_client.get_workflow_run_by_id(workflow_run_id)
    if not workflow_run:
        return None
    workflow = await db_client.get_workflow_by_id(workflow_run.workflow_id)
    if not workflow:
        return None
    provider = await get_telephony_provider_for_run(
        workflow_run, workflow.organization_id
    )
    return provider, workflow_run


async def provider_for_transfer(transfer_id: str) -> tuple[Any, Any] | None:
    """(provider, transfer_context) for a transfer, through the run it
    belongs to. A context with no run cannot be verified and is refused."""
    manager = await get_call_transfer_manager()
    context = await manager.get_transfer_context(transfer_id)
    if not context or not getattr(context, "workflow_run_id", None):
        return None
    resolved = await provider_for_run_id(int(context.workflow_run_id))
    if resolved is None:
        return None
    return resolved[0], context


async def require_signature(
    request: Request, provider: Any, data: dict[str, Any], body: str = ""
) -> None:
    """Raise 401 unless ``provider`` accepts this request as its own."""
    try:
        valid = await provider.verify_inbound_signature(
            str(request.url), data, dict(request.headers), body
        )
    except TypeError:
        # A provider whose verifier predates the ``body`` argument.
        valid = await provider.verify_inbound_signature(
            str(request.url), data, dict(request.headers)
        )
    if not valid:
        logger.warning(
            "Rejected an unsigned or forged callback at {}", request.url.path
        )
        raise HTTPException(status_code=401, detail="Invalid webhook signature")


__all__ = ["provider_for_run_id", "provider_for_transfer", "require_signature"]
