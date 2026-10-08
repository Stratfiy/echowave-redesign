"""What the private-browser tests share: accounts, the switch, a fake web,
and a way to run a whole session inside one test.

The session loop and the test poke the same transactional database session
from two asyncio tasks (the loop runs; the test presses Confirm). An
``AsyncSession`` does not allow that, so ``serial_db`` puts a task-aware lock
around the patched ``db_client.async_session``: one task at a time, and the
same task may nest.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest

from api import constants
from api.db import db_client
from api.db.models import OrganizationModel, UserModel
from api.services.browser import channel, drivers, session
from api.services.browser.fake import FakeDriver, Page, Step

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "browser_injection"
SITE = "bills.example.in"
BILL_URL = f"https://{SITE}/bill"


class _TaskLock:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._owner: asyncio.Task | None = None
        self._depth = 0

    @asynccontextmanager
    async def held(self):
        task = asyncio.current_task()
        if self._owner is task:
            self._depth += 1
            try:
                yield
            finally:
                self._depth -= 1
            return
        async with self._lock:
            self._owner, self._depth = task, 1
            try:
                yield
            finally:
                self._owner, self._depth = None, 0


@pytest.fixture
def serial_db(db_session):
    original = db_client.async_session
    lock = _TaskLock()

    @asynccontextmanager
    async def serial():
        async with lock.held():
            async with original() as s:
                yield s

    db_client.async_session = serial
    yield db_client
    db_client.async_session = original


@pytest.fixture
def browser_on(monkeypatch, serial_db):
    """The flag on, queueing swallowed (the test runs the job itself), a
    fresh Redis client for this test's loop, and no driver left behind."""
    monkeypatch.setattr(constants, "DECIBYL_BROWSER_ENABLED", True)
    monkeypatch.setattr(constants, "BROWSER_DRIVER", "fake")
    queued: list[tuple] = []

    async def enqueue(*args, **kwargs):
        queued.append((args, kwargs))

    import api.tasks.arq as arq

    monkeypatch.setattr(arq, "enqueue_job", enqueue)
    channel.reset()
    yield queued
    drivers.use(None)
    channel.reset()


async def account(async_session, slug: str) -> tuple[OrganizationModel, UserModel]:
    org = OrganizationModel(provider_id=f"org-browser-{slug}", quota_decibyl_tokens=0)
    async_session.add(org)
    await async_session.flush()
    user = UserModel(
        provider_id=f"user-browser-{slug}",
        email=f"{slug}@browser.example",
        selected_organization_id=org.id,
    )
    async_session.add(user)
    await async_session.flush()
    return org, user


async def colleague(async_session, org: OrganizationModel, slug: str) -> UserModel:
    user = UserModel(
        provider_id=f"user-browser-{slug}",
        email=f"{slug}@browser.example",
        selected_organization_id=org.id,
    )
    async_session.add(user)
    await async_session.flush()
    return user


def bill_page(extra: str = "") -> str:
    return (
        '<!doctype html><html><head><meta charset="utf-8"><title>Your bill</title>'
        "</head><body><h1>BESCOM bill for consumer 12345</h1>"
        f"<p>Amount due: ₹2,340 by 14 Oct</p>{extra}</body></html>"
    )


def fixture_page(name: str) -> str:
    return (FIXTURES / f"{name}.html").read_text(encoding="utf-8")


async def start(
    org: OrganizationModel,
    user: UserModel,
    *,
    request: str,
    sites: list[str] | None = None,
    may: list[str] | None = None,
    start_url: str | None = BILL_URL,
    steps: int | None = None,
    minutes: int | None = None,
) -> Any:
    """Start a session the way the tool does; return its row."""
    result = await session.start(
        organization_id=org.id,
        user_id=user.id,
        thread_id=None,
        task=request,
        request=request,
        sites_named=[SITE] if sites is None else sites,
        verbs_declared=may,
        start_url=start_url,
        steps=steps,
        minutes=minutes,
    )
    assert result["status"] == "started", result
    rows = await db_client.agent_events(
        organization_id=org.id,
        workflow_id=None,
        kinds=["browser_session"],
        limit=5,
        assistant_thread=True,
    )
    uuid = rows[0].payload["session_uuid"]
    return await db_client.get_browser_session_for_worker(uuid)


async def run(row: Any, fake: FakeDriver, start_url: str | None = BILL_URL) -> Any:
    """Run the job to its end with ``fake`` as the browser; return the row."""
    drivers.use(fake)
    await asyncio.wait_for(session.run(row.session_uuid, start_url or ""), timeout=30)
    return await db_client.get_browser_session_for_worker(row.session_uuid)


async def until(predicate, *, timeout: float = 10.0, every: float = 0.05):
    """Wait for ``predicate()`` (sync or async) to be truthy; return it."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while True:
        value = predicate()
        if asyncio.iscoroutine(value):
            value = await value
        if value:
            return value
        if loop.time() > deadline:
            raise AssertionError("timed out waiting")
        await asyncio.sleep(every)


__all__ = [
    "BILL_URL",
    "FakeDriver",
    "Page",
    "SITE",
    "Step",
    "account",
    "bill_page",
    "browser_on",
    "colleague",
    "fixture_page",
    "run",
    "serial_db",
    "start",
    "until",
]
