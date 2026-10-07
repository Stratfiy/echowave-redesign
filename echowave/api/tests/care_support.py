"""Shared set-up for the launch stream `care` tests: people, workspaces,
flags, and running a consent card the way a person's press does."""

from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from api import constants
from api.db import db_client

CARE_FLAGS = (
    "CARE_SIMPLE_MODE_ENABLED",
    "CARE_MEDICINE_CALLS_ENABLED",
    "CARE_SCAM_CHECK_ENABLED",
    "CARE_TECH_HELP_ENABLED",
    "CARE_FAMILY_CIRCLE_ENABLED",
)


def all_on(monkeypatch, *, ledger: bool = True) -> None:
    for flag in CARE_FLAGS:
        monkeypatch.setattr(constants, flag, True)
    monkeypatch.setattr(constants, "MEMBER_PREFERENCES_ENABLED", True)
    if ledger:
        monkeypatch.setattr(constants, "TASK_LEDGER_ENABLED", True)


async def person(tag: str, *, email: str | None = None):
    run = uuid4().hex[:10]
    user, _ = await db_client.get_or_create_user_by_provider_id(f"care-{tag}-{run}")
    async with db_client.async_session() as session:
        await session.execute(
            text("UPDATE users SET email = :e WHERE id = :u"),
            {"e": email or f"{tag}-{run}@example.com", "u": user.id},
        )
        await session.commit()
    return await db_client.get_user_by_id(user.id)


async def workspace(owner_id: int) -> int:
    org, _ = await db_client.get_or_create_organization_by_provider_id(
        f"care-org-{uuid4().hex[:10]}", owner_id
    )
    return int(org.id)


async def cleanup(*organization_ids: int) -> None:
    async with db_client.async_session() as session:
        for org in organization_ids:
            for table in (
                "care_alerts",
                "care_dose_calls",
                "care_medicines",
                "care_circle_members",
                "care_circles",
                "care_scam_checks",
                "care_help_sessions",
                "agent_events",
                "credit_ledger",
            ):
                await session.execute(
                    text(f"DELETE FROM {table} WHERE organization_id = :o"), {"o": org}
                )
        await session.commit()


async def press(
    organization_id: int, event_id: int, user_id: int, verb: str = "confirm"
):
    """A person's press on a card, then (for confirm) the job that fires it
    once the undo window has passed."""
    from api.services.workflow import actions

    event = await db_client.get_agent_event(event_id, organization_id=organization_id)
    version = (event.payload or {}).get("version")
    with patch("api.tasks.arq.enqueue_job", new=AsyncMock()):
        payload = await actions.settle(
            organization_id=organization_id,
            event_id=event_id,
            verb=verb,
            user_id=user_id,
            version=version,
        )
    if verb == "confirm":
        await actions.run(event_id, organization_id)
    event = await db_client.get_agent_event(event_id, organization_id=organization_id)
    return event.payload or payload


@asynccontextmanager
async def client(user_id: int, organization_id: int | None):
    from api.app import app
    from api.services.auth.depends import get_user

    app.dependency_overrides[get_user] = lambda: SimpleNamespace(
        id=user_id, selected_organization_id=organization_id
    )
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as c:
            yield c
    finally:
        app.dependency_overrides.pop(get_user, None)
