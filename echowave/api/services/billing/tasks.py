"""Post-call billing entry point.

Thin wrapper that owns its own database session, so ARQ tasks and any other
caller can cost a completed run without knowing how sessions are managed.
"""

from __future__ import annotations

from loguru import logger

from api.db import db_client
from api.services.billing.costing import cost_workflow_run


async def cost_completed_workflow_run(workflow_run_id: int) -> None:
    """Cost a completed run and persist its receipt.

    Safe to call more than once: :func:`cost_workflow_run` skips a run that has
    already been costed, so a retried completion job cannot double-charge.
    """
    async with db_client.async_session() as session:
        cost = await cost_workflow_run(session, workflow_run_id)
        if cost is None:
            return
        await session.commit()

    logger.info(
        "Costed workflow run {}: {} paise charged ({} provider + {} platform)",
        workflow_run_id,
        cost.total_charged_paise,
        cost.total_provider_cost_paise,
        cost.platform_fee_paise,
    )
