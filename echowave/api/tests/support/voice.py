"""Shared set-up for launch stream `voice` tests: two people in one
workspace, a third in another, an HTTP client as any of them, and the
flags switched on."""

from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace
from uuid import uuid4

from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from api import constants
from api.db import db_client
from api.enums import OrganizationRole

FLAGS = (
    "DECIBYL_VOICE_ENABLED",
    "VOICE_LATENCY_ENABLED",
    "CALL_FOR_ME_ENABLED",
    "CALL_APPOINTMENT_ENABLED",
)


def all_on(monkeypatch) -> None:
    for name in FLAGS:
        monkeypatch.setattr(constants, name, True)


async def make_people(prefix: str) -> SimpleNamespace:
    run = uuid4().hex[:8]
    a, _ = await db_client.get_or_create_user_by_provider_id(f"{prefix}-a-{run}")
    b, _ = await db_client.get_or_create_user_by_provider_id(f"{prefix}-b-{run}")
    c, _ = await db_client.get_or_create_user_by_provider_id(f"{prefix}-c-{run}")
    org, _ = await db_client.get_or_create_organization_by_provider_id(
        f"{prefix}-org-{run}", a.id
    )
    other, _ = await db_client.get_or_create_organization_by_provider_id(
        f"{prefix}-other-{run}", c.id
    )
    await db_client.add_user_to_organization(a.id, org.id, OrganizationRole.ADMIN.value)
    await db_client.add_user_to_organization(b.id, org.id)
    await db_client.add_user_to_organization(
        c.id, other.id, OrganizationRole.ADMIN.value
    )
    return SimpleNamespace(
        a=a,
        b=b,
        c=c,
        org=org.id,
        other=other.id,
        as_a=SimpleNamespace(
            id=a.id, selected_organization_id=org.id, provider_id=a.provider_id
        ),
        as_b=SimpleNamespace(
            id=b.id, selected_organization_id=org.id, provider_id=b.provider_id
        ),
        as_c=SimpleNamespace(
            id=c.id, selected_organization_id=other.id, provider_id=c.provider_id
        ),
    )


async def clean(people: SimpleNamespace) -> None:
    async with db_client.async_session() as session:
        for org in (people.org, people.other):
            for table in (
                "appointments",
                "appointment_policies",
                "voice_sessions",
                "credit_ledger",
            ):
                await session.execute(
                    text(f"DELETE FROM {table} WHERE organization_id = :o"), {"o": org}
                )
        for user in (people.a, people.b, people.c):
            await session.execute(
                text("DELETE FROM operational_usage WHERE user_id = :u"), {"u": user.id}
            )
            await session.execute(
                text("DELETE FROM member_preferences WHERE user_id = :u"),
                {"u": user.id},
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
