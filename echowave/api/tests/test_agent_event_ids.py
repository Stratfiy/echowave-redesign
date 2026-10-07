"""A timeline write returns the id of the row it wrote.

It raised MissingGreenlet instead: the app's sessions expire rows on commit,
and the id was read after the commit. Every caller that needed the id --
action cards, inbox notices, outbound event webhooks -- got nothing.

The shared test session never expires rows, which is exactly why no test
saw this. So this one swaps in a session that behaves like production's
(``expire_on_commit`` left at its default, True) around the transactional
test connection.
"""

from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from api.db import db_client
from api.db.models import OrganizationModel


class _ExpiringSession:
    """Like the app's sessions: rows expire on commit."""

    def __init__(self, bind):
        self._session = AsyncSession(
            bind=bind, join_transaction_mode="create_savepoint"
        )

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, *exc):
        await self._session.close()


@pytest.mark.asyncio
async def test_a_timeline_write_returns_its_id(db_session, async_session, monkeypatch):
    org = OrganizationModel(provider_id=f"events-{datetime.now(UTC).timestamp()}")
    async_session.add(org)
    await async_session.flush()

    connection = await async_session.connection()
    monkeypatch.setattr(
        db_client, "async_session", lambda: _ExpiringSession(connection)
    )

    event_id = await db_client.record_agent_event(
        organization_id=org.id, kind="message", actor="human", summary="Plan my week"
    )

    assert isinstance(event_id, int)
