"""A personal space for every person (founder: person + business as one;
handoff 8; launch stream controls).

Done when: every person can have a personal space distinct from the
workspaces they join; nobody else can ever be in it, by any path; what is
kept there is never read from a business workspace (and the reverse); and
making it neither mints credit nor moves the person out of their workspace.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError

from api import constants
from api.db import db_client
from api.services import personal_space
from api.services.organizations import invitations


@pytest.fixture
def space_on(monkeypatch):
    monkeypatch.setattr(constants, "PERSONAL_SPACE_ENABLED", True)


@pytest.fixture
async def people(test_engine):
    run = uuid4().hex[:8]
    a, _ = await db_client.get_or_create_user_by_provider_id(f"space-a-{run}")
    b, _ = await db_client.get_or_create_user_by_provider_id(f"space-b-{run}")
    business, _ = await db_client.get_or_create_organization_by_provider_id(
        f"space-biz-{run}", a.id
    )
    await db_client.add_user_to_organization(a.id, business.id, "owner")
    await db_client.add_user_to_organization(b.id, business.id, "member")
    orgs = [business.id]
    try:
        yield a, b, business.id, orgs
    finally:
        async with db_client.async_session() as session:
            spaces = (
                (
                    await session.execute(
                        text(
                            "SELECT id FROM organizations WHERE personal_owner_user_id = ANY(:u)"
                        ),
                        {"u": [a.id, b.id]},
                    )
                )
                .scalars()
                .all()
            )
            for org in [*orgs, *spaces]:
                for table in ("credit_ledger", "organisation_facts"):
                    await session.execute(
                        text(f"DELETE FROM {table} WHERE organization_id = :o"),
                        {"o": org},
                    )
            await session.commit()


@pytest.mark.asyncio
class TestTheSpace:
    async def test_every_person_gets_their_own(self, people, space_on):
        a, b, business, _ = people
        mine = await personal_space.ensure(a.id)
        theirs = await personal_space.ensure(b.id)
        assert mine.id != theirs.id != business
        assert personal_space.is_personal(mine)
        assert mine.personal_owner_user_id == a.id
        assert await db_client.is_user_member_of_organization(a.id, mine.id)
        assert not await db_client.is_user_member_of_organization(b.id, mine.id)

    async def test_asking_twice_at_once_makes_one(self, people, space_on):
        a, _, _, _ = people
        spaces = await asyncio.gather(*(personal_space.ensure(a.id) for _ in range(5)))
        assert len({s.id for s in spaces}) == 1

    async def test_it_mints_no_credit_and_moves_nobody(self, people, space_on):
        a, _, business, _ = people
        async with db_client.async_session() as session:
            await session.execute(
                text("UPDATE users SET selected_organization_id = :o WHERE id = :u"),
                {"o": business, "u": a.id},
            )
            await session.commit()
        space = await personal_space.ensure(a.id)
        async with db_client.async_session() as session:
            credit = await session.scalar(
                text("SELECT count(*) FROM credit_ledger WHERE organization_id = :o"),
                {"o": space.id},
            )
            keys = await session.scalar(
                text("SELECT count(*) FROM api_keys WHERE organization_id = :o"),
                {"o": space.id},
            )
            selected = await session.scalar(
                text("SELECT selected_organization_id FROM users WHERE id = :u"),
                {"u": a.id},
            )
        assert credit == 0 and keys == 0
        assert selected == business


@pytest.mark.asyncio
class TestNobodyElseIsEverIn:
    async def test_the_database_refuses_a_second_member(self, people, space_on):
        a, b, _, _ = people
        space = await personal_space.ensure(a.id)
        with pytest.raises((IntegrityError, DBAPIError)):
            await db_client.add_user_to_organization(b.id, space.id, "member")
        assert not await db_client.is_user_member_of_organization(b.id, space.id)

    async def test_moving_a_membership_into_it_is_refused_too(self, people, space_on):
        a, b, business, _ = people
        space = await personal_space.ensure(a.id)
        with pytest.raises((IntegrityError, DBAPIError)):
            async with db_client.async_session() as session:
                await session.execute(
                    text(
                        "UPDATE organization_memberships SET organization_id = :s "
                        "WHERE user_id = :b AND organization_id = :o"
                    ),
                    {"s": space.id, "b": b.id, "o": business},
                )
                await session.commit()

    async def test_an_invitation_to_it_is_refused_in_words(self, people, space_on):
        a, _, _, _ = people
        space = await personal_space.ensure(a.id)
        async with db_client.async_session() as session:
            with pytest.raises(invitations.InvitationError, match="personal space"):
                await invitations.invite(
                    session,
                    organization_id=space.id,
                    email="friend@example.com",
                    role="member",
                    invited_by=a.id,
                )

    async def test_a_workspace_still_takes_members(self, people, space_on):
        a, b, business, _ = people
        await personal_space.ensure(a.id)
        assert await db_client.is_user_member_of_organization(b.id, business)


@pytest.mark.asyncio
class TestWhatIsKeptThereStaysThere:
    async def test_memory_kept_there_is_not_read_from_the_business(
        self, people, space_on
    ):
        a, b, business, _ = people
        space = await personal_space.ensure(a.id)
        await db_client.remember_organisation_facts(
            organization_id=space.id, facts={"doctor": "Dr Iyer on Thursdays"}
        )
        await db_client.remember_organisation_facts(
            organization_id=business, facts={"opening hours": "9 to 6"}
        )
        in_business = {
            r.value
            for r in await db_client.organisation_memory(organization_id=business)
        }
        in_space = {
            r.value
            for r in await db_client.organisation_memory(organization_id=space.id)
        }
        assert "Dr Iyer on Thursdays" in in_space
        assert "Dr Iyer on Thursdays" not in in_business
        # ...and the business's memory does not leak into the person's space.
        assert "9 to 6" in in_business and "9 to 6" not in in_space
        # A colleague in the business never reaches it, even asking as themself.
        as_b = {
            r.value
            for r in await db_client.organisation_memory(
                organization_id=business, user_id=b.id
            )
        }
        assert "Dr Iyer on Thursdays" not in as_b


@asynccontextmanager
async def _client(user):
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


@pytest.mark.asyncio
class TestArrival:
    async def test_off_there_is_no_personal_space(self, people):
        a, _, business, _ = people
        user = SimpleNamespace(id=a.id, selected_organization_id=business)
        async with _client(user) as client:
            assert (await client.get("/api/v1/me/personal-space")).status_code == 404
        assert await personal_space.find(a.id) is None

    async def test_on_a_person_opens_theirs(self, people, space_on):
        a, b, business, _ = people
        async with _client(
            SimpleNamespace(id=a.id, selected_organization_id=business)
        ) as client:
            response = await client.get("/api/v1/me/personal-space")
        assert response.status_code == 200
        body = response.json()
        assert body["kind"] == "personal" and body["owner_user_id"] == a.id
        assert body["selected"] is False
        async with _client(
            SimpleNamespace(id=b.id, selected_organization_id=business)
        ) as client:
            other = (await client.get("/api/v1/me/personal-space")).json()
        assert other["organization_id"] != body["organization_id"]
