"""Fixtures shared by the stream `reach` tests.

The fake servers (``reach_fakes``) run on a real port for the session, so
these tests go over HTTP and MCP exactly as production does -- only the
far end is a fake. No real provider is ever called.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from api import constants
from api.db import db_client
from api.services import acting
from api.tests.support import reach_fakes


@pytest.fixture(scope="session")
def fake_servers():
    with reach_fakes.Running() as running:
        yield running


@pytest.fixture
def reach_on(monkeypatch, fake_servers):
    """Every reach flag on, the fake Zomato configured, private addresses
    allowed (the fakes are on 127.0.0.1)."""
    reach_fakes.reset()
    for name in (
        "OUTSIDE_TOOLS_ENABLED",
        "ORDERING_ENABLED",
        "PRICE_COMPARE_ENABLED",
        "REACH_ALLOW_PRIVATE_SERVERS",
    ):
        monkeypatch.setattr(constants, name, True)
    monkeypatch.setattr(constants, "ZOMATO_MCP_URL", f"{fake_servers.base}/zomato/mcp")
    monkeypatch.setattr(constants, "SWIGGY_MCP_URL", None)
    return fake_servers


@pytest.fixture
def reach_off(monkeypatch):
    for name in ("OUTSIDE_TOOLS_ENABLED", "ORDERING_ENABLED", "PRICE_COMPARE_ENABLED"):
        monkeypatch.setattr(constants, name, False)


@pytest.fixture
async def people(test_engine):
    """Two people in one workspace, and a third in another."""
    run = uuid4().hex[:8]
    a, _ = await db_client.get_or_create_user_by_provider_id(f"reach-a-{run}")
    b, _ = await db_client.get_or_create_user_by_provider_id(f"reach-b-{run}")
    c, _ = await db_client.get_or_create_user_by_provider_id(f"reach-c-{run}")
    org, _ = await db_client.get_or_create_organization_by_provider_id(
        f"reach-org-{run}", a.id
    )
    other, _ = await db_client.get_or_create_organization_by_provider_id(
        f"reach-other-{run}", c.id
    )
    return SimpleNamespace(
        a=SimpleNamespace(
            id=a.id, selected_organization_id=org.id, provider_id=a.provider_id
        ),
        b=SimpleNamespace(
            id=b.id, selected_organization_id=org.id, provider_id=b.provider_id
        ),
        c=SimpleNamespace(
            id=c.id, selected_organization_id=other.id, provider_id=c.provider_id
        ),
        org=org.id,
        other=other.id,
    )


@pytest.fixture
def no_queue(monkeypatch):
    """Confirm queues the card's job; the tests run it themselves."""
    queued = []

    async def fake_enqueue(*args, **kwargs):
        queued.append(args)

    import api.tasks.arq as arq

    monkeypatch.setattr(arq, "enqueue_job", fake_enqueue)
    return queued


@asynccontextmanager
async def client_as(user):
    from api.app import app
    from api.services.auth.depends import get_user

    app.dependency_overrides[get_user] = lambda: user
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            yield client
    finally:
        app.dependency_overrides.pop(get_user, None)


def call(_tool, **arguments):
    return SimpleNamespace(
        name=_tool, arguments=arguments, id=f"call-{uuid4().hex[:6]}"
    )


async def connect_notes(org, user_id, base, token=reach_fakes.NOTES_TOKEN):
    from api.services.reach import connections

    started = await connections.connect(
        organization_id=org,
        user_id=user_id,
        kind="tool",
        provider="notes",
        name="Notes",
        server_url=f"{base}/notes/mcp",
        pasted_token=token,
    )
    return started.connection


async def connect_zomato(org, user_id):
    """Signs in to the fake Zomato the way a person would: the chip's
    authorize link, the server's redirect back, the callback."""
    import httpx

    from api.services.reach import connections
    from api.services.reach.ordering import providers

    started = await connections.connect(
        organization_id=org,
        user_id=user_id,
        kind="ordering",
        provider="zomato",
        name="Zomato",
        server_url=providers.ZOMATO.url(),
    )
    assert started.authorize_url
    async with httpx.AsyncClient() as client:
        redirect = await client.get(started.authorize_url, follow_redirects=False)
    location = httpx.URL(redirect.headers["location"])
    return await connections.finish_sign_in(
        location.params["state"], location.params["code"]
    )


acting_as = acting.acting_as
