"""Phase 3, shell: Chat's start screen for a plain member.

Found as a member with private threads on: the start screen read and wrote
the original (null) conversation, which is only its author's or an Admin's,
so the screen said "Could not load this conversation" and the first typed
message failed with "Thread not found". The threads list now says whether
the original conversation is the reader's, so the start screen can start a
new conversation of their own instead (minted in the browser, as New chat
does). Somebody else's named thread is still not found, and with the flag
off nothing changes.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from api import constants
from api.db import db_client
from api.enums import AgentEventActor, AgentEventKind
from api.tests.support.voice import clean, client_as, make_people

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def people(test_engine):
    p = await make_people("start-screen")
    yield p
    await clean(p)


async def _say(people, *, author, thread_id, text):
    await db_client.record_agent_event(
        organization_id=people.org,
        kind=AgentEventKind.MESSAGE.value,
        actor=AgentEventActor.HUMAN.value,
        summary=text,
        payload={"body": text, "author_id": author.id},
        thread_id=thread_id,
    )


class TestTheOriginalConversation:
    async def test_a_member_is_told_it_is_not_theirs(self, people, monkeypatch):
        monkeypatch.setattr(constants, "DECIBYL_PRIVATE_THREADS_ENABLED", True)
        async with client_as(people.as_b) as c:
            listed = await c.get("/api/v1/timeline/threads")
            read = await c.get("/api/v1/timeline", params={"assistant": True})
        assert listed.status_code == 200, listed.text
        assert listed.json()["original_is_yours"] is False
        # The reason the start screen must not read it: still not found.
        assert read.status_code == 404

    async def test_an_admin_keeps_it(self, people, monkeypatch):
        monkeypatch.setattr(constants, "DECIBYL_PRIVATE_THREADS_ENABLED", True)
        async with client_as(people.as_a) as c:
            listed = await c.get("/api/v1/timeline/threads")
        assert listed.json()["original_is_yours"] is True

    async def test_its_author_keeps_it_even_as_a_member(self, people, monkeypatch):
        monkeypatch.setattr(constants, "DECIBYL_PRIVATE_THREADS_ENABLED", True)
        await _say(people, author=people.b, thread_id=None, text="b said it first")
        async with client_as(people.as_b) as c:
            listed = await c.get("/api/v1/timeline/threads")
        async with client_as(people.as_a) as c:
            admin = await c.get("/api/v1/timeline/threads")
        assert listed.json()["original_is_yours"] is True
        # An admin is not the author of a conversation a member started.
        assert admin.json()["original_is_yours"] is False

    async def test_with_the_flag_off_it_is_everyones(self, people, monkeypatch):
        monkeypatch.setattr(constants, "DECIBYL_PRIVATE_THREADS_ENABLED", False)
        async with client_as(people.as_b) as c:
            listed = await c.get("/api/v1/timeline/threads")
            read = await c.get("/api/v1/timeline", params={"assistant": True})
        assert listed.json()["original_is_yours"] is True
        assert read.status_code == 200


class TestAMembersFirstMessage:
    async def test_a_minted_thread_is_theirs_to_start(self, people, monkeypatch):
        """What the start screen now sends: a fresh id, accepted, and the
        conversation is listed as theirs and nobody else's."""
        monkeypatch.setattr(constants, "DECIBYL_PRIVATE_THREADS_ENABLED", True)
        minted = str(uuid4())
        sent = []

        async def ask(**kwargs):
            sent.append(kwargs)
            await _say(
                people,
                author=people.b,
                thread_id=kwargs["thread_id"],
                text=kwargs["text"],
            )
            return []

        from api.services.workflow import decibyl

        monkeypatch.setattr(decibyl, "ask", ask)
        async with client_as(people.as_b) as c:
            posted = await c.post(
                "/api/v1/timeline/message",
                json={"assistant": True, "thread_id": minted, "text": "first words"},
            )
            mine = await c.get("/api/v1/timeline/threads")
            read = await c.get(
                "/api/v1/timeline", params={"assistant": True, "thread_id": minted}
            )
        assert posted.status_code == 200, posted.text
        assert sent[0]["thread_id"] == minted
        assert minted in {t["thread_id"] for t in mine.json()["threads"]}
        assert read.status_code == 200
        async with client_as(people.as_a) as c:
            theirs = await c.get(
                "/api/v1/timeline", params={"assistant": True, "thread_id": minted}
            )
            listed = await c.get("/api/v1/timeline/threads")
        assert theirs.status_code == 404
        assert minted not in {t["thread_id"] for t in listed.json()["threads"]}

    async def test_someone_elses_named_thread_stays_not_found(
        self, people, monkeypatch
    ):
        monkeypatch.setattr(constants, "DECIBYL_PRIVATE_THREADS_ENABLED", True)
        await _say(people, author=people.a, thread_id="t-asha", text="Asha's own")
        async with client_as(people.as_b) as c:
            posted = await c.post(
                "/api/v1/timeline/message",
                json={"assistant": True, "thread_id": "t-asha", "text": "hi"},
            )
        assert posted.status_code == 404


class TestTheMemoryMeter:
    """Found in the privacy pass: the meter beside the composer answered a
    member with the size and message count of somebody else's private
    conversation -- and so confirmed the id named one, which every other
    read hides behind "not found"."""

    async def test_someone_elses_thread_is_not_measured(self, people, monkeypatch):
        monkeypatch.setattr(constants, "DECIBYL_PRIVATE_THREADS_ENABLED", True)
        await _say(people, author=people.a, thread_id="t-asha", text="Asha's own words")
        async with client_as(people.as_b) as c:
            theirs = await c.get(
                "/api/v1/timeline/memory",
                params={"assistant": True, "thread_id": "t-asha"},
            )
        async with client_as(people.as_a) as c:
            mine = await c.get(
                "/api/v1/timeline/memory",
                params={"assistant": True, "thread_id": "t-asha"},
            )
        assert theirs.status_code == 404
        assert mine.status_code == 200 and mine.json()["messages_total"] == 1

    async def test_nor_the_original_when_it_is_not_theirs(self, people, monkeypatch):
        monkeypatch.setattr(constants, "DECIBYL_PRIVATE_THREADS_ENABLED", True)
        await _say(people, author=people.a, thread_id=None, text="the owner's line")
        async with client_as(people.as_b) as c:
            original = await c.get(
                "/api/v1/timeline/memory", params={"assistant": True}
            )
            fresh = await c.get(
                "/api/v1/timeline/memory",
                params={"assistant": True, "thread_id": str(uuid4())},
            )
        assert original.status_code == 404
        # A conversation nobody has spoken in yet is theirs to start.
        assert fresh.status_code == 200 and fresh.json()["messages_total"] == 0

    async def test_with_the_flag_off_it_is_measured_for_everyone(
        self, people, monkeypatch
    ):
        monkeypatch.setattr(constants, "DECIBYL_PRIVATE_THREADS_ENABLED", False)
        await _say(people, author=people.a, thread_id="t-open", text="shared")
        async with client_as(people.as_b) as c:
            read = await c.get(
                "/api/v1/timeline/memory",
                params={"assistant": True, "thread_id": "t-open"},
            )
        assert read.status_code == 200 and read.json()["messages_total"] == 1
