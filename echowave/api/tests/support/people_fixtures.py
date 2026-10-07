"""Fixtures for People's tests (PEOPLE.md): two members of one workspace and
a stranger elsewhere, the flag on, the fake Google/Microsoft provider on a
real port, and jobs run inline. No real provider or model is ever called."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from sqlalchemy import text

from api import constants
from api.db import db_client
from api.tests.support import people_fakes
from api.tests.support.settings_people import (  # noqa: F401
    clean,
    client_as,
    make_people,
)

PEOPLE_TABLES = (
    "person_shares",
    "person_merges",
    "person_interactions",
    "person_sources",
    "person_handles",
    "people",
    "people_syncs",
    "people_settings",
)


async def wipe(*orgs: int) -> None:
    async with db_client.async_session() as session:
        for table in PEOPLE_TABLES:
            await session.execute(
                text(f"DELETE FROM {table} WHERE organization_id = ANY(:o)"),
                {"o": list(orgs)},
            )
        await session.commit()


@pytest.fixture
async def people(test_engine):
    found = await make_people("people")
    try:
        yield found
    finally:
        await wipe(found.org, found.other_org)
        await clean(found)


@pytest.fixture(scope="session")
def fake_provider():
    with people_fakes.Running() as running:
        yield running


@pytest.fixture
def people_on(monkeypatch, fake_provider):
    people_fakes.reset()
    monkeypatch.setattr(constants, "PEOPLE_ENABLED", True)
    monkeypatch.setattr(constants, "PEOPLE_FAKE_PROVIDER_URL", fake_provider.base)
    monkeypatch.setattr(constants, "PEOPLE_BRIEF_WRITER", "fake")
    monkeypatch.setattr(constants, "PEOPLE_BRIEF_DEBOUNCE_SECONDS", 600)
    return fake_provider


@pytest.fixture
def worker(monkeypatch):
    """Jobs run inline: a sync started over HTTP runs before the answer."""
    calls: list[tuple] = []

    async def enqueue(name, *args, **kwargs):
        calls.append((name, args, kwargs))
        if name == "sync_people":
            from api.services.people import sync

            await sync.run(
                kwargs["organization_id"], kwargs["user_id"], kwargs["provider"]
            )

    monkeypatch.setattr("api.tasks.arq.enqueue_job", AsyncMock(side_effect=enqueue))
    return calls
