"""What each channel has actually done on this deployment (screen 22).

A channel's capability is "available" only after a message whose signature
checked arrived on it (handoff 22: "availability comes from verified
capability flags"). Each inbound path calls :func:`saw_verified_inbound`
after its own signature check, and delivery calls the other two. Platform
wide, not per person. Never raises, and writes nothing while
``identity_connections`` is off everywhere.
"""

from __future__ import annotations

from datetime import UTC, datetime

from loguru import logger
from sqlalchemy.dialects.postgresql import insert

from api.db import db_client
from api.db.identity_models import ChannelCheckModel
from api.services import features

FLAG = "identity_connections"


async def _upsert(channel: str, **values) -> None:
    if not features.on_anywhere(FLAG):
        return
    now = datetime.now(UTC)
    values["updated_at"] = now
    try:
        async with db_client.async_session() as session:
            await session.execute(
                insert(ChannelCheckModel)
                .values(channel=channel, **values)
                .on_conflict_do_update(index_elements=["channel"], set_=values)
            )
            await session.commit()
    except Exception as exc:  # noqa: BLE001 - a health note must not break a message
        logger.warning("Could not record {} health: {}", channel, exc)


async def saw_verified_inbound(channel: str) -> None:
    await _upsert(channel, verified_inbound_at=datetime.now(UTC))


async def delivered(channel: str) -> None:
    await _upsert(channel, delivery_ok_at=datetime.now(UTC))


async def delivery_failed(channel: str, code: str) -> None:
    await _upsert(
        channel, delivery_failed_at=datetime.now(UTC), failure_code=(code or "")[:64]
    )
