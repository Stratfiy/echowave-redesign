"""Dial a workflow at a number, with the guards every outbound path owes.

Extracted so that a second outbound entry point cannot quietly skip one of
them. The trigger API grew this sequence — concurrency slot, workflow run,
quota, webhook URL, dial, release the slot on every failure path — and getting
it slightly wrong is not visible in testing: a leaked concurrency slot only
shows up as an account that mysteriously cannot place calls an hour later, and
a run created before a refused quota check bills for a call that never
happened.

Deliberately *not* including the DND gate or the calling window. Those depend
on what the caller is: a campaign row and a number the account has proved it
owns get different answers, and folding them in here would force one policy on
both. Each caller applies them before it gets this far.
"""

import random

from loguru import logger

from api.db import db_client
from api.services.call_concurrency import (
    CallConcurrencyLimitError,
    call_concurrency,
)
from api.services.quota_service import authorize_workflow_run_start
from api.utils.common import get_backend_endpoints


class OutboundRefused(Exception):
    """The call was not placed, for a reason the caller should surface."""


class NoConcurrencySlot(OutboundRefused):
    """The organization is already using every concurrent call it has."""


class QuotaExhausted(OutboundRefused):
    """The organization cannot pay for another call."""


async def dial_workflow(
    *,
    workflow,
    organization_id: int,
    to_number: str,
    provider,
    telephony_configuration_id: int | None,
    source: str,
    extra_context: dict | None = None,
) -> int:
    """Place the call and return the workflow run id.

    ``source`` names the caller in concurrency accounting and in the run's
    context, so a bill can be traced back to the thing that caused it.
    """
    try:
        slot = await call_concurrency.acquire_org_slot(
            organization_id, source=source, timeout=0
        )
    except CallConcurrencyLimitError as exc:
        raise NoConcurrencySlot(str(exc)) from exc

    workflow_run_id = None
    try:
        initial_context = {
            "provider": provider.PROVIDER_NAME,
            "phone_number": to_number,
            "trigger_mode": "production",
            "telephony_configuration_id": telephony_configuration_id,
            "workflow_uuid": workflow.workflow_uuid,
        }
        initial_context.update(extra_context or {})

        workflow_run = await db_client.create_workflow_run(
            name=f"WR-{source.upper()}-{random.randint(1000, 9999)}",
            workflow_id=workflow.id,
            mode=provider.PROVIDER_NAME,
            initial_context=initial_context,
            user_id=workflow.user_id,
            use_draft=False,
            organization_id=organization_id,
        )
        workflow_run_id = workflow_run.id
        await call_concurrency.bind_workflow_run(slot, workflow_run_id)
    except Exception:
        # The slot is only bound to a run once the run exists. Until then it is
        # held by slot id, and releasing by run id would leak it.
        await call_concurrency.release_slot(slot)
        raise

    try:
        # After the run exists, so hosted billing can attach its correlation id
        # before the provider starts the call.
        quota = await authorize_workflow_run_start(
            workflow_id=workflow.id,
            organization_id=organization_id,
            workflow_run_id=workflow_run_id,
        )
        if not quota.has_quota:
            raise QuotaExhausted(quota.error_message)

        backend_endpoint, _ = await get_backend_endpoints()
        webhook_url = (
            f"{backend_endpoint}/api/v1/telephony/{provider.WEBHOOK_ENDPOINT}"
            f"?workflow_id={workflow.id}"
            f"&workflow_run_id={workflow_run_id}"
            f"&organization_id={organization_id}"
        )

        # workflow_id and organization_id are required by providers that build
        # the media WebSocket URL at dial time; without them the URL contains
        # "None/None" and the stream never connects.
        await provider.initiate_call(
            to_number=to_number,
            webhook_url=webhook_url,
            workflow_run_id=workflow_run_id,
            workflow_id=workflow.id,
            organization_id=organization_id,
        )
    except Exception:
        await call_concurrency.release_workflow_run_slot(workflow_run_id)
        raise

    logger.info(
        f"[{source}] call initiated for workflow run {workflow_run_id} to {to_number}"
    )
    return workflow_run_id
