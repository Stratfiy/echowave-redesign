"""Scheduled work for launch stream `voice`. A no-op while its switch is
off, so registering it changes nothing until then."""

from __future__ import annotations

from loguru import logger


async def sweep_stale_voice_sessions(_ctx) -> None:
    """Live voice sessions nobody has heard from are ended as ``lost``, so a
    closed tab or a dropped phone never holds a person's one live session
    (services/voice/sessions.py)."""
    from api.services import features
    from api.services.voice import sessions

    if not features.on_anywhere(sessions.FLAG):
        return
    ended = await sessions.sweep_stale()
    if ended:
        logger.info("Ended {} lost voice sessions", ended)
