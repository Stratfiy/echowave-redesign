"""Workflow run start authorization.

This module authorizes a workflow run before any billable call/text runtime
starts. It is the single seam every runtime entrypoint (WebRTC signaling,
telephony, campaigns, public API triggers, text chat, agent stream) goes
through, so it owns two responsibilities:

1. **Tenant isolation.** The workflow must belong to the organization being
   billed, the acting user must be a member of that organization, and a
   supplied run must belong to that workflow. These are security checks, not
   accounting ones.
2. **Managed model services correlation.** When the resolved configuration
   uses Decibyl-managed model services, a correlation ID is minted so the
   model gateway can attribute STT/LLM/TTS usage to this run.

Decibyl does not sell prepaid inference credits, so there is no balance check
here. Local platform-fee accounting is applied after a run completes.
"""

from dataclasses import dataclass
from typing import Any

import httpx
from loguru import logger

from api.db import db_client
from api.db.models import UserModel
from api.services.configuration.ai_model_configuration import (
    get_effective_ai_model_configuration_for_workflow,
)
from api.services.managed_model_services import (
    MPS_CORRELATION_ID_CONTEXT_KEY,
    get_decibyl_service_api_key,
    uses_managed_model_services_v2,
)
from api.services.mps_service_key_client import mps_service_key_client

_MODEL_GATEWAY_UNREACHABLE_ERRORS = (
    httpx.TimeoutException,
    httpx.NetworkError,
    httpx.RemoteProtocolError,
    httpx.ProxyError,
)

INVALID_SERVICE_KEY_MESSAGE = (
    "You have invalid keys in your model configuration. "
    "Please validate the service keys."
)


@dataclass
class QuotaCheckResult:
    """Result of a workflow run start authorization."""

    has_quota: bool
    error_message: str = ""
    error_code: str = ""


def _gateway_unreachable_result(error: httpx.RequestError) -> QuotaCheckResult:
    """Allow the run to proceed when the model gateway is unreachable.

    A transport failure minting a correlation ID only costs usage attribution,
    so it must not take down call handling. Every other failure mode below
    fails closed.
    """
    logger.warning(
        "Model gateway unreachable while minting correlation id; allowing "
        "workflow run to proceed without usage correlation: {}",
        error,
    )
    return QuotaCheckResult(has_quota=True)


async def _store_run_correlation_id(
    workflow_run_id: int | None,
    correlation_id: str | None,
) -> None:
    if not workflow_run_id or not correlation_id:
        return

    workflow_run = await db_client.get_workflow_run_by_id(workflow_run_id)
    if not workflow_run:
        logger.warning(
            "Could not store model gateway correlation id for missing workflow run {}",
            workflow_run_id,
        )
        return

    initial_context = dict(workflow_run.initial_context or {})
    if initial_context.get(MPS_CORRELATION_ID_CONTEXT_KEY) == correlation_id:
        return

    initial_context[MPS_CORRELATION_ID_CONTEXT_KEY] = correlation_id
    await db_client.update_workflow_run(
        workflow_run_id,
        initial_context=initial_context,
    )


async def _mint_managed_model_correlation(
    *,
    workflow_id: int,
    workflow_run_id: int | None,
    user_config: Any,
) -> QuotaCheckResult:
    """Mint and persist a model gateway correlation ID for a managed-model run."""
    if not workflow_run_id or not uses_managed_model_services_v2(user_config):
        return QuotaCheckResult(has_quota=True)

    service_key = get_decibyl_service_api_key(user_config)
    if not service_key:
        return QuotaCheckResult(
            has_quota=False,
            error_code="invalid_service_key",
            error_message=INVALID_SERVICE_KEY_MESSAGE,
        )

    try:
        response = await mps_service_key_client.create_correlation_id(
            service_key=service_key,
            workflow_run_id=workflow_run_id,
        )
        await _store_run_correlation_id(
            workflow_run_id,
            response.get("correlation_id"),
        )
    except _MODEL_GATEWAY_UNREACHABLE_ERRORS as e:
        return _gateway_unreachable_result(e)
    except Exception as e:
        logger.error(
            "Failed to mint model gateway correlation id for workflow {} run {}: {}",
            workflow_id,
            workflow_run_id,
            e,
        )
        return QuotaCheckResult(
            has_quota=False,
            error_code="quota_check_failed",
            error_message="Could not start the run. Please try again.",
        )

    return QuotaCheckResult(has_quota=True)


async def authorize_workflow_run_start(
    *,
    workflow_id: int,
    organization_id: int,
    workflow_run_id: int | None = None,
    actor_user: UserModel | None = None,
) -> QuotaCheckResult:
    """Authorize a workflow run before any billable call/text runtime starts.

    Validates that the workflow belongs to ``organization_id``, that
    ``actor_user`` (when supplied) is a member of that organization, and that
    ``workflow_run_id`` (when supplied) belongs to that workflow. Then mints a
    managed model services correlation ID when the run needs one.
    """
    if organization_id is None:
        logger.warning(
            "Workflow start authorization denied: missing organization scope for workflow {}",
            workflow_id,
        )
        return QuotaCheckResult(
            has_quota=False,
            error_code="workflow_not_found",
            error_message="Workflow not found",
        )

    try:
        workflow = await db_client.get_workflow(
            workflow_id,
            organization_id=organization_id,
        )
    except Exception as e:
        logger.error(
            "Workflow start authorization denied: failed to load workflow {} for org {}: {}",
            workflow_id,
            organization_id,
            e,
        )
        return QuotaCheckResult(
            has_quota=False,
            error_code="workflow_not_found",
            error_message="Workflow not found",
        )

    if not workflow:
        logger.warning(
            "Workflow start authorization denied: workflow {} not found for org {}",
            workflow_id,
            organization_id,
        )
        return QuotaCheckResult(
            has_quota=False,
            error_code="workflow_not_found",
            error_message="Workflow not found",
        )

    try:
        actor_id = getattr(actor_user, "id", None) if actor_user is not None else None
        if actor_user is not None and actor_id is None:
            logger.warning(
                "Workflow start authorization denied: actor is missing id for workflow {} org {}",
                workflow_id,
                organization_id,
            )
            return QuotaCheckResult(
                has_quota=False,
                error_code="workflow_not_found",
                error_message="Workflow not found",
            )

        if actor_id is not None:
            try:
                is_member = await db_client.is_user_member_of_organization(
                    user_id=actor_id,
                    organization_id=organization_id,
                )
            except Exception as e:
                logger.error(
                    "Workflow start authorization denied: failed to validate actor {} membership for workflow {} org {}: {}",
                    actor_id,
                    workflow_id,
                    organization_id,
                    e,
                )
                return QuotaCheckResult(
                    has_quota=False,
                    error_code="workflow_not_found",
                    error_message="Workflow not found",
                )
            if not is_member:
                logger.warning(
                    "Workflow start authorization denied: actor {} is not a member of workflow {} org {}",
                    actor_id,
                    workflow_id,
                    organization_id,
                )
                return QuotaCheckResult(
                    has_quota=False,
                    error_code="workflow_not_found",
                    error_message="Workflow not found",
                )

        # A DB read failure here is a "cannot verify" condition, not a
        # definitive "not found": let it fall through to the outer handler so
        # it fails closed. The None case below is a genuine missing row and keeps
        # its specific code.
        workflow_owner = await db_client.get_user_by_id(workflow.user_id)
        if not workflow_owner:
            return QuotaCheckResult(
                has_quota=False,
                error_code="user_not_found",
                error_message="User not found",
            )

        # The run executes its pinned definition's configuration, so the
        # correlation must be minted for the service key in that snapshot.
        # workflow.workflow_configurations is a legacy column synced to the
        # draft on every save, which can carry a different service key than
        # the definition the run will actually use.
        workflow_configurations = workflow.workflow_configurations
        if workflow_run_id is not None:
            # As with the owner lookup, a DB read failure falls through to the
            # outer fail-closed handler; only a genuinely missing/mismatched run
            # returns the specific code below.
            workflow_run = await db_client.get_workflow_run(
                workflow_run_id, organization_id=organization_id
            )
            if workflow_run is None or workflow_run.workflow_id != workflow.id:
                logger.warning(
                    "Workflow start authorization denied: workflow run {} not found for workflow {} org {}",
                    workflow_run_id,
                    workflow_id,
                    organization_id,
                )
                return QuotaCheckResult(
                    has_quota=False,
                    error_code="workflow_run_not_found",
                    error_message="Workflow run not found",
                )
            if workflow_run.definition is not None:
                workflow_configurations = (
                    workflow_run.definition.workflow_configurations
                )

        user_config = await get_effective_ai_model_configuration_for_workflow(
            organization_id=organization_id,
            workflow_configurations=workflow_configurations,
        )

        return await _mint_managed_model_correlation(
            workflow_id=workflow.id,
            workflow_run_id=workflow_run_id,
            user_config=user_config,
        )

    except Exception as e:
        logger.error(f"Error during workflow run start authorization: {str(e)}")
        # Only an httpx transport failure raised while calling the model gateway
        # is allowed to fail open, and that is handled at the call site above.
        # Database, configuration, response-validation, and programming errors
        # all reach this handler and fail closed.
        return QuotaCheckResult(
            has_quota=False,
            error_code="quota_check_failed",
            error_message="Could not start the run. Please try again.",
        )
