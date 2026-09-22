"""The nightly dialer import (CR-1), and the purge that keeps it bounded.

For every connected dialer: the last two Indian days' calls, the ones a
person handled and recorded, copied across and transcribed. One session per
connection, committed on its own, so one business's broken token never
holds back another's import. Does nothing while DIALER_IMPORT_ENABLED is
off -- not even the listing, which would spend the business's API quota.
"""

from loguru import logger
from sqlalchemy import select

from api import constants
from api.db import db_client
from api.db.models import DialerConnectionModel
from api.services.dialer_import import importer
from api.services.dialer_import.times import last_two_days_ist


async def import_dialer_calls(_ctx) -> None:
    if not constants.DIALER_IMPORT_ENABLED:
        return
    async with db_client.async_session() as session:
        ids = list(await session.scalars(select(DialerConnectionModel.id)))
    since, until = last_two_days_ist()
    for connection_id in ids:
        async with db_client.async_session() as session:
            connection = await session.get(DialerConnectionModel, connection_id)
            if connection is None:
                continue
            try:
                result = await importer.import_window(session, connection, since, until)
                await session.commit()
            except Exception as exc:
                await session.rollback()
                logger.exception(
                    "dialer import: connection {} failed: {}", connection_id, exc
                )
                continue
        logger.info(
            "dialer import: connection {} listed {} imported {} failed {}",
            connection_id,
            result.listed,
            result.imported,
            result.failed,
        )


async def purge_imported_calls(_ctx) -> None:
    """Runs whether or not the import is on: switching the flag off must not
    leave recordings sitting past their retention."""
    async with db_client.async_session() as session:
        purged = await importer.purge_expired(session)
        if purged:
            await session.commit()
            logger.info("dialer import: purged {} expired call(s)", purged)
