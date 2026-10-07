"""Follow-up chips that answer the reply on screen (AGENTS.md: "the chat
carries its own next steps").

Found using the Research helper as a person would: after a report, the chips
under it were the home screen's ("What needs closing before tomorrow?"),
never "Go deeper on point 2" or "Turn this into a one-page brief", and a
tapped chip was sent as Automatic, dropping the helper the person had chosen.
"""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from api import constants
from api.db import db_client
from api.enums import AgentEventActor, AgentEventKind
from api.services.helpers import follow_ups
from api.services.workflow import agent_timeline

REPORT = """Here is what I found.

1. Central assistance is up to Rs 78,000 for 3 kW [1].
2. Net metering rules vary by state [2].
3. I infer 3 kW suits most homes.

Sources:
[1] https://example.gov/a
[2] https://example.gov/b
"""


class TestForReply:
    def test_research_offers_each_numbered_point_and_a_brief(self):
        chips = follow_ups.for_reply("research", REPORT)
        texts = [c["text"] for c in chips]
        assert texts[:3] == [
            "Go deeper on point 1",
            "Go deeper on point 2",
            "Go deeper on point 3",
        ]
        assert "Turn this into a one-page brief" in texts
        assert {c["helper"] for c in chips} == {"research"}

    def test_research_without_points_still_offers_the_brief(self):
        texts = [c["text"] for c in follow_ups.for_reply("research", "Short answer.")]
        assert texts == ["Turn this into a one-page brief"]

    def test_learning_guide_offers_practice(self):
        chips = follow_ups.for_reply("learning_guide", "Loops repeat steps.")
        assert [c["text"] for c in chips] == [
            "Quiz me on this",
            "Explain it more simply",
        ]
        assert {c["helper"] for c in chips} == {"learning_guide"}

    def test_automatic_and_unknown_helpers_add_nothing(self):
        assert follow_ups.for_reply(None, REPORT) == []
        assert follow_ups.for_reply("nobody", REPORT) == []


@pytest.fixture
async def person(test_engine):
    run = uuid4().hex[:8]
    a, _ = await db_client.get_or_create_user_by_provider_id(f"chips-a-{run}")
    org, _ = await db_client.get_or_create_organization_by_provider_id(
        f"chips-org-{run}", a.id
    )
    yield a, org.id
    async with db_client.async_session() as session:
        await session.execute(
            text("DELETE FROM agent_events WHERE organization_id = :o"), {"o": org.id}
        )
        await session.commit()


async def _reply(org: int, thread: str, body: str, **payload) -> None:
    with agent_timeline.in_thread(thread):
        await agent_timeline.record(
            organization_id=org,
            kind=AgentEventKind.MESSAGE.value,
            actor=AgentEventActor.AGENT.value,
            summary=body[:500],
            payload={"body": body, "from": "Decibyl", **payload},
            in_channel=False,
        )


async def _chips(user_id: int, org: int, thread: str | None) -> list[dict]:
    from api.app import app
    from api.services.auth.depends import get_user

    app.dependency_overrides[get_user] = lambda: SimpleNamespace(
        id=user_id, selected_organization_id=org
    )
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as c:
            url = "/api/v1/timeline/chips"
            got = await c.get(url, params={"thread_id": thread} if thread else None)
            assert got.status_code == 200, got.text
            return got.json()["chips"]
    finally:
        app.dependency_overrides.pop(get_user, None)


@pytest.mark.asyncio
class TestThreadChips:
    async def test_chips_answer_the_last_research_reply(self, person, monkeypatch):
        monkeypatch.setattr(constants, "DECIBYL_PRIVATE_THREADS_ENABLED", False)
        a, org = person
        thread = str(uuid4())
        await _reply(org, thread, REPORT, helper="research")
        chips = await _chips(a.id, org, thread)
        assert chips[0] == {
            "kind": "follow_up",
            "text": "Go deeper on point 1",
            "helper": "research",
        }
        assert any(c["text"] == "Turn this into a one-page brief" for c in chips)

    async def test_a_failed_reply_gets_no_follow_ups(self, person, monkeypatch):
        monkeypatch.setattr(constants, "DECIBYL_PRIVATE_THREADS_ENABLED", False)
        a, org = person
        thread = str(uuid4())
        await _reply(org, thread, REPORT, helper="research", failed=True)
        chips = await _chips(a.id, org, thread)
        assert not any(c["kind"] == "follow_up" for c in chips)

    async def test_without_a_thread_the_chips_are_as_before(self, person):
        a, org = person
        chips = await _chips(a.id, org, None)
        assert not any(c["kind"] == "follow_up" for c in chips)


class TestAnAgentsOwnChat:
    def test_a_reply_with_sources_gets_the_research_follow_ups(self):
        chips = follow_ups.for_agent_reply(REPORT)
        assert [c["text"] for c in chips][:2] == [
            "Go deeper on point 1",
            "Go deeper on point 2",
        ]
        assert "Turn this into a one-page brief" in [c["text"] for c in chips]
        assert {c["helper"] for c in chips} == {None}

    def test_a_reply_without_sources_gets_none(self):
        assert follow_ups.for_agent_reply("1. Hello\n2. There") == []


@pytest.mark.asyncio
class TestAgentChatChips:
    async def test_chips_for_an_agents_own_chat_are_its_reply_only(self, person):
        a, org = person
        bot = await db_client.create_workflow(
            name="Research agent",
            workflow_definition={"nodes": [], "edges": []},
            user_id=a.id,
            organization_id=org,
        )
        await agent_timeline.record(
            organization_id=org,
            kind=AgentEventKind.MESSAGE.value,
            actor=AgentEventActor.AGENT.value,
            summary="report",
            payload={"body": REPORT},
            workflow_id=bot.id,
            in_channel=False,
        )
        from api.app import app
        from api.services.auth.depends import get_user

        app.dependency_overrides[get_user] = lambda: SimpleNamespace(
            id=a.id, selected_organization_id=org
        )
        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as c:
                got = await c.get(
                    "/api/v1/timeline/chips", params={"workflow_id": bot.id}
                )
        finally:
            app.dependency_overrides.pop(get_user, None)
        chips = got.json()["chips"]
        assert chips and all(c["kind"] == "follow_up" for c in chips)
        assert chips[0]["text"] == "Go deeper on point 1"
