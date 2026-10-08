"""People and workspaces for the settings stream's tests (SETTINGS.md).

Two members of one business workspace, a stranger in another, an HTTP client
signed in as any of them, and a clean-up that removes what the tests wrote.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
from types import SimpleNamespace
from uuid import uuid4

from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from api.db import db_client


@dataclass
class People:
    a: object
    b: object
    stranger: object
    org: int
    other_org: int

    def as_user(self, who, organization_id: int | None = None, **extra):
        return SimpleNamespace(
            id=who.id,
            selected_organization_id=organization_id or self.org,
            email=f"user{who.id}@example.test",
            email_verified_at=None,
            mfa_enabled=False,
            mfa_secret_encrypted=None,
            mfa_last_counter=None,
            password_hash=None,
            **extra,
        )


async def make_people(tag: str) -> People:
    run = uuid4().hex[:8]
    a, _ = await db_client.get_or_create_user_by_provider_id(f"{tag}-a-{run}")
    b, _ = await db_client.get_or_create_user_by_provider_id(f"{tag}-b-{run}")
    stranger, _ = await db_client.get_or_create_user_by_provider_id(f"{tag}-s-{run}")
    org, _ = await db_client.get_or_create_organization_by_provider_id(
        f"{tag}-org-{run}", a.id
    )
    other, _ = await db_client.get_or_create_organization_by_provider_id(
        f"{tag}-other-{run}", stranger.id
    )
    await db_client.add_user_to_organization(a.id, org.id, "owner")
    await db_client.add_user_to_organization(b.id, org.id, "member")
    await db_client.add_user_to_organization(stranger.id, other.id, "owner")
    return People(a=a, b=b, stranger=stranger, org=org.id, other_org=other.id)


async def clean(people: People) -> None:
    users = [people.a.id, people.b.id, people.stranger.id]
    async with db_client.async_session() as session:
        spaces = (
            (
                await session.execute(
                    text(
                        "SELECT id FROM organizations WHERE personal_owner_user_id = ANY(:u)"
                    ),
                    {"u": users},
                )
            )
            .scalars()
            .all()
        )
        for org in [people.org, people.other_org, *spaces]:
            for table in (
                "credit_ledger",
                "organisation_facts",
                "memory_fact_revisions",
                "saved_items",
                "temporary_conversations",
            ):
                await session.execute(
                    text(f"DELETE FROM {table} WHERE organization_id = :o"), {"o": org}
                )
            # Agents made by a test: other suites count agents across the
            # database, so none may be left behind.
            await session.execute(
                text(
                    "UPDATE workflows SET released_definition_id = NULL "
                    "WHERE organization_id = :o"
                ),
                {"o": org},
            )
            await session.execute(
                text(
                    "DELETE FROM workflow_definitions WHERE workflow_id IN "
                    "(SELECT id FROM workflows WHERE organization_id = :o)"
                ),
                {"o": org},
            )
            await session.execute(
                text("DELETE FROM workflows WHERE organization_id = :o"), {"o": org}
            )
        await session.execute(
            text("DELETE FROM personal_data_requests WHERE user_id = ANY(:u)"),
            {"u": users},
        )
        await session.commit()


@asynccontextmanager
async def client_as(user):
    from api.app import app
    from api.services.auth.depends import get_user

    # Nested clients (A, then B inside it) each put back what was there.
    before = app.dependency_overrides.get(get_user)
    app.dependency_overrides[get_user] = lambda: user
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            yield client
    finally:
        if before is None:
            app.dependency_overrides.pop(get_user, None)
        else:
            app.dependency_overrides[get_user] = before
