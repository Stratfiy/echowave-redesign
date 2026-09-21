"""Post-call billing entry point.

Thin wrapper that owns its own database session, so ARQ tasks and any other
caller can cost a completed run without knowing how sessions are managed.
"""

from __future__ import annotations

from loguru import logger

from api.db import db_client
from api.services.billing.costing import cost_workflow_run


async def record_text_run_cost(workflow_run_id: int) -> None:
    """Refresh a text session's vendor-cost receipt after a turn.

    Charged nothing -- the turn was charged as an event -- so this only
    records what the model cost us. Never raises: the answer was given and
    the event debited, and a receipt that fails to refresh is a gap in the
    margin report, not in the customer's balance.
    """
    try:
        async with db_client.async_session() as session:
            cost = await cost_workflow_run(session, workflow_run_id, recost=True)
            if cost is None:
                return
            await session.commit()
    except Exception as exc:  # noqa: BLE001 - see the docstring
        logger.error(
            "Could not record the vendor cost of text run {}: {}",
            workflow_run_id,
            exc,
        )


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
