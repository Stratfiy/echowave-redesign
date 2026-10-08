"""Shared setup for the launch stream `today` tests: real users in a real
workspace (the tables have foreign keys to both), a colleague in the same
workspace, a stranger in another, flags switched with monkeypatch, and a
route client signed in as any of them."""

from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
from types import SimpleNamespace
from uuid import uuid4

from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from api import constants
from api.db import db_client
from api.services.today.scope import Viewer

TODAY_FLAGS = {
    "today_list": "TODAY_LIST_ENABLED",
    "approval_dock": "APPROVAL_DOCK_ENABLED",
    "today_reminders": "TODAY_REMINDERS_ENABLED",
    "daily_brief": "DAILY_BRIEF_ENABLED",
    "end_of_day_note": "END_OF_DAY_NOTE_ENABLED",
    "routine_start_on": "ROUTINE_START_ON_ENABLED",
}

_TABLES = (
    "today_deliveries",
    "today_reminders",
    "today_events",
    "daily_briefs",
    "daily_brief_settings",
    "today_dismissals",
    "agent_task_transitions",
    "agent_tasks",
    "agent_events",
    "agent_routines",
)


def switch_on(monkeypatch, *names: str) -> None:
    for name in names:
        monkeypatch.setattr(constants, TODAY_FLAGS[name], True)


@dataclass
class People:
    me: Viewer
    colleague: Viewer
    stranger: Viewer
    me_user: object
    colleague_user: object
    stranger_user: object


async def make_people(zone: str = "Asia/Kolkata") -> People:
    run = uuid4().hex[:8]
    me, _ = await db_client.get_or_create_user_by_provider_id(f"today-me-{run}")
    colleague, _ = await db_client.get_or_create_user_by_provider_id(f"today-col-{run}")
    stranger, _ = await db_client.get_or_create_user_by_provider_id(f"today-str-{run}")
    org, _ = await db_client.get_or_create_organization_by_provider_id(
        f"today-org-{run}", me.id
    )
    other, _ = await db_client.get_or_create_organization_by_provider_id(
        f"today-other-{run}", stranger.id
    )
    await db_client.add_user_to_organization(colleague.id, org.id)

    def user(u, o):
        return SimpleNamespace(
            id=u.id, selected_organization_id=o, provider_id=u.provider_id
        )

    return People(
        me=Viewer(user_id=me.id, organization_id=org.id, zone_name=zone),
        colleague=Viewer(user_id=colleague.id, organization_id=org.id, zone_name=zone),
        stranger=Viewer(user_id=stranger.id, organization_id=other.id, zone_name=zone),
        me_user=user(me, org.id),
        colleague_user=user(colleague, org.id),
        stranger_user=user(stranger, other.id),
    )


async def clean(people: People) -> None:
    async with db_client.async_session() as session:
        for org in {people.me.organization_id, people.stranger.organization_id}:
            for table in _TABLES:
                await session.execute(
                    text(f"DELETE FROM {table} WHERE organization_id = :o"), {"o": org}
                )
            await session.execute(
                text("DELETE FROM credit_ledger WHERE organization_id = :o"), {"o": org}
            )
        await session.commit()


@asynccontextmanager
async def client_as(user):
    from api.app import app
    from api.services.auth.depends import get_user

    app.dependency_overrides[get_user] = lambda: user
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as c:
            yield c
    finally:
        app.dependency_overrides.pop(get_user, None)
