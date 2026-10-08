"""Saved items and search in one scope (screen 15).

Done when: a person saves a reply from their own workspace (never a line
from another), finds it in their personal scope, finds the workspace's
shared items and memory in the workspace scope, and never finds a
colleague's private item or memory -- not by search, not by id; deleting
goes through a card, describes what it does not touch, and can be put back.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from api import constants
from api.db import db_client
from api.services.workflow import actions
from api.tests.support.settings_people import clean, client_as, make_people


@pytest.fixture
async def people(test_engine):
    found = await make_people("setsav")
    try:
        yield found
    finally:
        await clean(found)


@pytest.fixture
def saved_on(monkeypatch):
    monkeypatch.setattr(constants, "SAVED_ITEMS_ENABLED", True)
    monkeypatch.setattr(constants, "PERSONAL_MEMORY_ENABLED", True)


@pytest.fixture
def queue(monkeypatch):
    queued = AsyncMock()
    monkeypatch.setattr("api.tasks.arq.enqueue_job", queued)
    return queued


async def _line(organization_id: int, body: str) -> int:
    return await db_client.record_agent_event(
        organization_id=organization_id,
        kind="message",
        actor="agent",
        summary=body,
        payload={"body": body},
    )


@pytest.mark.asyncio
class TestSaving:
    async def test_off_the_routes_are_not_there(self, people):
        async with client_as(people.as_user(people.a)) as c:
            assert (await c.get("/api/v1/me/saved")).status_code == 404
            assert (await c.get("/api/v1/me/search?q=x")).status_code == 404

    async def test_a_reply_is_saved_with_its_way_back(self, people, saved_on):
        line = await _line(people.org, "Your GST is due on the 20th, ₹12,400.")
        async with client_as(people.as_user(people.a)) as c:
            saved = await c.post(
                "/api/v1/me/saved",
                json={"title": "GST due date", "source_event_id": line},
            )
            assert saved.status_code == 200, saved.text
            item = saved.json()
            assert item["body"].startswith("Your GST is due")
            assert item["conversation_href"] == "/overview"
            assert item["visibility"] == "private"
            listed = (await c.get("/api/v1/me/saved?scope=personal")).json()["items"]
            assert [i["title"] for i in listed] == ["GST due date"]

    async def test_a_line_from_another_workspace_is_not_found(self, people, saved_on):
        elsewhere = await _line(people.other_org, "Somebody else's secret")
        async with client_as(people.as_user(people.a)) as c:
            answer = await c.post(
                "/api/v1/me/saved",
                json={"title": "steal", "source_event_id": elsewhere},
            )
        assert answer.status_code == 404

    async def test_rename_is_the_owners(self, people, saved_on):
        async with client_as(people.as_user(people.a)) as c:
            item = (
                await c.post(
                    "/api/v1/me/saved",
                    json={"title": "Note", "kind": "note", "body": "x"},
                )
            ).json()
            renamed = await c.patch(
                f"/api/v1/me/saved/{item['id']}", json={"title": "Better"}
            )
            assert renamed.json()["title"] == "Better"
        async with client_as(people.as_user(people.b)) as c:
            assert (
                await c.patch(
                    f"/api/v1/me/saved/{item['id']}", json={"title": "Mine now"}
                )
            ).status_code == 404
            assert (await c.get(f"/api/v1/me/saved/{item['id']}")).status_code == 404


@pytest.mark.asyncio
class TestSearchStaysInItsScope:
    async def test_personal_and_workspace_and_never_a_colleagues(
        self, people, saved_on
    ):
        async with client_as(people.as_user(people.a)) as c:
            await c.post(
                "/api/v1/me/saved",
                json={"title": "Invoice plan (mine)", "kind": "note"},
            )
            await c.post(
                "/api/v1/me/saved",
                json={
                    "title": "Invoice template",
                    "kind": "note",
                    "visibility": "workspace",
                },
            )
        async with client_as(people.as_user(people.b)) as c:
            await c.post(
                "/api/v1/me/saved", json={"title": "Invoice salary B", "kind": "note"}
            )
        await db_client.remember_organisation_facts(
            organization_id=people.org, facts={"invoice_terms": "30 days"}
        )
        await db_client.remember_organisation_facts(
            organization_id=people.org,
            facts={"invoice_secret": "B only"},
            user_id=people.b.id,
        )

        async with client_as(people.as_user(people.a)) as c:
            mine = (await c.get("/api/v1/me/search?q=invoice&scope=personal")).json()
            team = (await c.get("/api/v1/me/search?q=invoice&scope=workspace")).json()
        mine_titles = {r["title"] for r in mine["results"]}
        team_titles = {r["title"] for r in team["results"]}
        assert mine_titles == {"Invoice plan (mine)"}
        assert team_titles == {"Invoice template", "invoice_terms"}
        for titles in (mine_titles, team_titles):
            assert "Invoice salary B" not in titles and "invoice_secret" not in titles

    async def test_wildcards_are_words_not_patterns(self, people, saved_on):
        async with client_as(people.as_user(people.a)) as c:
            await c.post("/api/v1/me/saved", json={"title": "Plain", "kind": "note"})
            found = (await c.get("/api/v1/me/search?q=%25&scope=personal")).json()
        assert found["results"] == []

    async def test_another_workspace_sees_nothing(self, people, saved_on):
        async with client_as(people.as_user(people.a)) as c:
            await c.post(
                "/api/v1/me/saved",
                json={
                    "title": "Shared plan",
                    "kind": "note",
                    "visibility": "workspace",
                },
            )
        async with client_as(people.as_user(people.stranger, people.other_org)) as c:
            found = (await c.get("/api/v1/me/search?q=plan&scope=workspace")).json()
        assert found["results"] == []


@pytest.mark.asyncio
class TestDeletingIsACard:
    async def test_effects_are_described_and_it_can_be_put_back(
        self, people, saved_on, queue
    ):
        line = await _line(people.org, "Keep this")
        async with client_as(people.as_user(people.a)) as c:
            item = (
                await c.post(
                    "/api/v1/me/saved", json={"title": "Keep", "source_event_id": line}
                )
            ).json()
            assert (
                "The conversation it came from stays as it is."
                in item["deletion_effects"]
            )
            card = (await c.post(f"/api/v1/me/saved/{item['id']}/delete")).json()
            assert (
                card["state"] == "proposed" and "Only this saved copy" in card["effect"]
            )
            await c.post(
                f"/api/v1/me/settings/cards/{card['event_id']}/settle",
                json={
                    "organization_id": people.org,
                    "verb": "confirm",
                    "version": card["version"],
                },
            )
            await actions.run(card["event_id"], people.org)
            assert (await c.get("/api/v1/me/saved")).json()["items"] == []
            # The line it came from is untouched.
            assert (
                await db_client.get_agent_event(line, organization_id=people.org)
                is not None
            )
            await c.post(
                f"/api/v1/me/settings/cards/{card['event_id']}/settle",
                json={"organization_id": people.org, "verb": "undo"},
            )
            assert [
                i["title"] for i in (await c.get("/api/v1/me/saved")).json()["items"]
            ] == ["Keep"]
