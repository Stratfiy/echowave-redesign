"""D-1b: a Decibyl conversation belongs to the person who started it.

Behind ``DECIBYL_PRIVATE_THREADS_ENABLED``. Off, every member sees every
conversation, as before. On, a thread is listed and read by its author; one
with no author on record is an Admin's to see; a member who guesses another
member's thread id is told it is not found, the way a wrong tenant is.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from api import constants
from api.db.models import AgentEventModel, OrganizationModel, UserModel
from api.enums import AgentEventActor, AgentEventKind
from api.routes import agent_timeline as route

pytestmark = pytest.mark.asyncio


async def _org(session, slug):
    org = OrganizationModel(provider_id=f"org-{slug}", quota_decibyl_tokens=0)
    session.add(org)
    await session.flush()
    return org


async def _user(session, slug):
    user = UserModel(provider_id=f"user-{slug}", email=f"{slug}@example.com")
    session.add(user)
    await session.flush()
    return user


def _line(org, *, thread_id, author_id, text):
    return AgentEventModel(
        organization_id=org.id,
        thread_id=thread_id,
        kind=AgentEventKind.MESSAGE.value,
        actor=AgentEventActor.HUMAN.value,
        summary=text,
        payload={"body": text, "author_id": author_id} if author_id else {"body": text},
        visibility="always",
    )


class TestTheListing:
    async def test_off_everyone_sees_everything(self, async_session, monkeypatch):
        monkeypatch.setattr(constants, "DECIBYL_PRIVATE_THREADS_ENABLED", False)
        from api.db import db_client

        org = await _org(async_session, "open")
        a, b = await _user(async_session, "a"), await _user(async_session, "b")
        async_session.add_all(
            [
                _line(org, thread_id="t-a", author_id=a.id, text="a's chat"),
                _line(org, thread_id="t-b", author_id=b.id, text="b's chat"),
            ]
        )
        await async_session.flush()
        monkeypatch.setattr(db_client, "async_session", lambda: _Borrow(async_session))
        rows = await db_client.assistant_threads(organization_id=org.id)
        assert {r["thread_id"] for r in rows} == {"t-a", "t-b"}

    async def test_on_a_member_sees_their_own_and_an_admin_sees_the_unowned(
        self, async_session, monkeypatch
    ):
        from api.db import db_client

        org = await _org(async_session, "priv")
        a, b = await _user(async_session, "a2"), await _user(async_session, "b2")
        async_session.add_all(
            [
                _line(org, thread_id="t-a", author_id=a.id, text="a's chat"),
                _line(org, thread_id="t-b", author_id=b.id, text="b's chat"),
                _line(org, thread_id=None, author_id=None, text="the old chat"),
            ]
        )
        await async_session.flush()
        monkeypatch.setattr(db_client, "async_session", lambda: _Borrow(async_session))
        mine = await db_client.assistant_threads(organization_id=org.id, viewer_id=a.id)
        assert {r["thread_id"] for r in mine} == {"t-a"}
        admin = await db_client.assistant_threads(
            organization_id=org.id, viewer_id=a.id, viewer_is_admin=True
        )
        assert {r["thread_id"] for r in admin} == {"t-a", None}


class TestReadingAThread:
    async def test_anothers_thread_is_not_found(self, async_session, monkeypatch):
        monkeypatch.setattr(constants, "DECIBYL_PRIVATE_THREADS_ENABLED", True)
        from api.db import db_client

        org = await _org(async_session, "read")
        a, b = await _user(async_session, "a3"), await _user(async_session, "b3")
        async_session.add(_line(org, thread_id="t-b", author_id=b.id, text="b's"))
        await async_session.flush()
        monkeypatch.setattr(db_client, "async_session", lambda: _Borrow(async_session))
        monkeypatch.setattr(route, "_is_admin", _never_admin)
        with pytest.raises(HTTPException) as refused:
            await route._assert_thread_is_theirs(a, org.id, "t-b")
        assert refused.value.status_code == 404
        # Their own passes, and a chat nobody has spoken in yet passes.
        await route._assert_thread_is_theirs(b, org.id, "t-b")
        await route._assert_thread_is_theirs(a, org.id, "t-new")

    async def test_the_unowned_thread_is_an_admins(self, async_session, monkeypatch):
        monkeypatch.setattr(constants, "DECIBYL_PRIVATE_THREADS_ENABLED", True)
        from api.db import db_client

        org = await _org(async_session, "old")
        a = await _user(async_session, "a4")
        async_session.add(_line(org, thread_id=None, author_id=None, text="old"))
        await async_session.flush()
        monkeypatch.setattr(db_client, "async_session", lambda: _Borrow(async_session))
        monkeypatch.setattr(route, "_is_admin", _never_admin)
        with pytest.raises(HTTPException):
            await route._assert_thread_is_theirs(a, org.id, None)
        monkeypatch.setattr(route, "_is_admin", _always_admin)
        await route._assert_thread_is_theirs(a, org.id, None)

    async def test_off_nothing_is_checked(self, monkeypatch):
        monkeypatch.setattr(constants, "DECIBYL_PRIVATE_THREADS_ENABLED", False)
        await route._assert_thread_is_theirs(UserModel(id=1), 7, "anything")


async def _never_admin(user, organization_id):
    return False


async def _always_admin(user, organization_id):
    return True


class _Borrow:
    """The test's session, handed out by the client without being closed."""

    def __init__(self, session):
        self._session = session

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, *exc):
        return False
