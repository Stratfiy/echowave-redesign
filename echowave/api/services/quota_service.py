"""Quota checking service for call credits.

This module provides reusable quota checking functionality that can be used
across different endpoints (WebRTC signaling, telephony, public API triggers).

Billing is local and prepaid: an organization's balance is the sum of its
OrganizationCreditLedgerModel entries (see db/organization_credit_ledger_client.py).
A run may not start unless that balance is at least MINIMUM_CALL_BALANCE_USD.
"""

from dataclasses import dataclass
from typing import Any

from loguru import logger

from api.constants import MINIMUM_CALL_BALANCE_USD
from api.db import db_client
from api.db.models import UserModel

INSUFFICIENT_BALANCE_MESSAGE = (
    "You have exhausted your call credits. "
    "Please purchase more credits from /billing to keep making calls."
)


@dataclass
class QuotaCheckResult:
    """Result of a quota check."""

    has_quota: bool
    error_message: str = ""
    error_code: str = ""


def _insufficient_balance_result() -> QuotaCheckResult:
    return QuotaCheckResult(
        has_quota=False,
        error_code="insufficient_credits",
        error_message=INSUFFICIENT_BALANCE_MESSAGE,
    )


async def authorize_workflow_run_start(
    *,
    workflow_id: int,
    organization_id: int,
    workflow_run_id: int | None = None,
    actor_user: UserModel | None = None,
) -> QuotaCheckResult:
    """Authorize a workflow run before any billable call/text runtime starts.

    The organization is always the billing subject: its prepaid credit
    balance must cover the minimum per-call threshold. The workflow owner is
    not billing-relevant here; actor_user is used only to validate that the
    caller (when one is present, e.g. an interactive UI session) is a member
    of the organization.
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

        if workflow_run_id is not None:
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

        balance = await db_client.get_balance(organization_id)
        if balance < MINIMUM_CALL_BALANCE_USD:
            logger.warning(
                "Insufficient balance for org {}: ${:.2f} remaining",
                organization_id,
                balance,
            )
            return _insufficient_balance_result()

        return QuotaCheckResult(has_quota=True)

    except Exception as e:
        logger.error(f"Error during quota check: {str(e)}")
        return QuotaCheckResult(
            has_quota=False,
            error_code="quota_check_failed",
            error_message="Could not verify call credits. Please try again.",
        )
