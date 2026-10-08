"""Scheduled work for launch stream `identity`. A no-op while its switch is
off, so registering it changes nothing until then."""

from __future__ import annotations

from loguru import logger


async def reconcile_unknown_outcomes(_ctx) -> None:
    """Ask each provider what happened to sends whose outcome is unknown,
    and settle the cards (services/identity/reconcile.py)."""
    from api.services.identity import reconcile

    counts = await reconcile.sweep()
    if any(counts[k] for k in ("delivered", "not_delivered", "asked")):
        logger.info("Reconciled unknown outcomes: {}", counts)
