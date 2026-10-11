"""Scheduled work for evolving skills (services/evolve). A no-op while
``evolve_skills`` is off everywhere, so registering it changes nothing
until then."""

from __future__ import annotations

from loguru import logger


async def evolve_skills_tick(_ctx) -> None:
    """Record what finished, propose and gate lessons, watch promotions."""
    from api.services.evolve import learn

    passed = await learn.tick()
    if passed:
        logger.info("evolve_skills: {} workspaces passed over", passed)
