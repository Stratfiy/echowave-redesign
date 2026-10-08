"""The memory manager (screen 16; handoff 8 "Memory").

Done when: a person sees their own memories and the workspace's -- never a
colleague's personal ones, not even by id; each fact shows where it came
from and every change to it; an edit from a stale screen is a conflict, not
an overwrite; forgetting goes through a card that runs once and can be put
back, and says nothing on the shared thread; sharing names its destination
first and moves exactly what the preview showed; the switch and the routes
are invisible while off.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from api import constants
from api.db import db_client
from api.db.models import AgentEventModel
from api.services import member_preferences
from api.services.settings import memory
from api.services.workflow import actions
from api.tests.support.settings_people import clean, client_as, make_people


@pytest.fixture
async def people(test_engine):
    found = await make_people("setmem")
    try:
        yield found
    finally:
        await clean(found)


@pytest.fixture
def manager_on(monkeypatch):
    monkeypatch.setattr(constants, "MEMORY_MANAGER_ENABLED", True)
    monkeypatch.setattr(constants, "PERSONAL_MEMORY_ENABLED", True)
    monkeypatch.setattr(constants, "MEMBER_PREFERENCES_ENABLED", True)


@pytest.fixture
def queue(monkeypatch):
    """The card's run job, captured instead of sent to Redis."""
    queued = AsyncMock()
    monkeypatch.setattr("api.tasks.arq.enqueue_job", queued)
    return queued


async def _facts(people):
    """A's personal fact, B's personal fact, and one of the workspace's."""
    await db_client.remember_organisation_facts(
        organization_id=people.org,
        facts={"tea": "masala, no sugar"},
        user_id=people.a.id,
    )
    await db_client.remember_organisation_facts(
        organization_id=people.org,
        facts={"salary": "private to B"},
        user_id=people.b.id,
    )
    await db_client.remember_organisation_facts(
        organization_id=people.org, facts={"opening_hours": "9 to 6"}
    )
    rows = await db_client.organisation_memory(
        organization_id=people.org, include_bots=True
    )
    workspace = next(r for r in rows if r.key == "opening_hours")
    mine = await db_client.organisation_memory(
        organization_id=people.org, user_id=people.a.id
    )
    a_fact = next(r for r in mine if r.key == "tea")
    theirs = await db_client.organisation_memory(
        organization_id=people.org, user_id=people.b.id
    )
    b_fact = next(r for r in theirs if r.key == "salary")
    return a_fact.id, b_fact.id, workspace.id


@pytest.mark.asyncio
class TestTheSwitchAndTheList:
    async def test_off_the_routes_are_not_there(self, people):
        async with client_as(people.as_user(people.a)) as c:
            assert (await c.get("/api/v1/me/memory")).status_code == 404

    async def test_memory_starts_off_and_the_switch_saves(self, people, manager_on):
        async with client_as(people.as_user(people.a)) as c:
            first = (await c.get("/api/v1/me/memory")).json()
            assert first["memory_enabled"] is False and first["memory_chosen"] is False
            assert "deleted" in first["temporary_retention"]
            on = await c.put(
                "/api/v1/me/memory/switch",
                json={"memory_enabled": True, "revision": first["revision"]},
            )
            assert on.status_code == 200 and on.json()["memory_enabled"] is True
            stale = await c.put(
                "/api/v1/me/memory/switch",
                json={"memory_enabled": False, "revision": first["revision"]},
            )
            assert stale.status_code == 409
            assert stale.json()["detail"]["memory_enabled"] is True
        # B's switch is B's.
        assert (await member_preferences.get(people.b.id))["memory_enabled"] is None

    async def test_mine_and_the_workspaces_never_a_colleagues(self, people, manager_on):
        await _facts(people)
        async with client_as(people.as_user(people.a)) as c:
            got = (await c.get("/api/v1/me/memory")).json()
        assert [f["key"] for f in got["mine"]] == ["tea"]
        assert "opening_hours" in [f["key"] for f in got["workspace"]]
        every = [f["key"] for f in got["mine"] + got["workspace"]]
        assert "salary" not in every
        tea = got["mine"][0]
        assert tea["scope"] == "mine"
        assert tea["source"]["line"] == "From your conversations with Decibyl"
        assert tea["source"]["first_seen_at"]

    async def test_a_failed_read_is_an_error_not_an_empty_list(
        self, people, manager_on, monkeypatch
    ):
        async def broken(**_kwargs):
            raise RuntimeError("database down")

        monkeypatch.setattr(memory, "overview", broken)
        async with client_as(people.as_user(people.a)) as c:
            with pytest.raises(RuntimeError):
                await c.get("/api/v1/me/memory")


@pytest.mark.asyncio
class TestNobodyElses:
    async def test_a_colleagues_fact_is_not_found_by_id(
        self, people, manager_on, queue
    ):
        a_fact, b_fact, _ = await _facts(people)
        async with client_as(people.as_user(people.a)) as c:
            for call in (
                c.get(f"/api/v1/me/memory/{b_fact}"),
                c.patch(
                    f"/api/v1/me/memory/{b_fact}",
                    json={"value": "x", "expected_value": "private to B"},
                ),
                c.post(f"/api/v1/me/memory/{b_fact}/forget"),
                c.post(f"/api/v1/me/memory/{b_fact}/confirm"),
                c.get(
                    f"/api/v1/me/memory/{b_fact}/share-preview?destination={people.org}"
                ),
            ):
                assert (await call).status_code == 404
        # Another workspace cannot reach A's fact either.
        async with client_as(people.as_user(people.stranger, people.other_org)) as c:
            assert (await c.get(f"/api/v1/me/memory/{a_fact}")).status_code == 404


@pytest.mark.asyncio
class TestEditsAreRevisions:
    async def test_edit_records_history_and_a_stale_edit_conflicts(
        self, people, manager_on
    ):
        a_fact, _, _ = await _facts(people)
        async with client_as(people.as_user(people.a)) as c:
            edited = await c.patch(
                f"/api/v1/me/memory/{a_fact}",
                json={
                    "value": "ginger, no sugar",
                    "expected_value": "masala, no sugar",
                },
            )
            assert edited.status_code == 200, edited.text
            body = edited.json()
            assert body["value"] == "ginger, no sugar" and body["status"] == "confirmed"
            assert body["history"][-1]["change"] == "edited"
            assert body["history"][-1]["before"] == "masala, no sugar"
            assert body["history"][-1]["by_you"] is True
            stale = await c.patch(
                f"/api/v1/me/memory/{a_fact}",
                json={"value": "lemon", "expected_value": "masala, no sugar"},
            )
            assert stale.status_code == 409
            assert stale.json()["detail"]["stored"]["value"] == "ginger, no sugar"

    async def test_an_empty_edit_is_refused(self, people, manager_on):
        a_fact, _, _ = await _facts(people)
        async with client_as(people.as_user(people.a)) as c:
            answer = await c.patch(
                f"/api/v1/me/memory/{a_fact}",
                json={"value": "  ", "expected_value": "masala, no sugar"},
            )
        assert answer.status_code == 422


@pytest.mark.asyncio
class TestForgettingIsACard:
    async def test_nothing_changes_until_confirmed_then_once_then_undo(
        self, people, manager_on, queue
    ):
        a_fact, _, _ = await _facts(people)
        async with client_as(people.as_user(people.a)) as c:
            card = (await c.post(f"/api/v1/me/memory/{a_fact}/forget")).json()
            assert card["state"] == "proposed" and card["reversible"] is True
            # The label never carries a personal fact's own words.
            assert "tea" not in card["label"]
            assert (
                await memory.detail(
                    organization_id=people.org, user_id=people.a.id, fact_id=a_fact
                )
            )["status"] == "confirmed"

            # B cannot see or press A's card.
            async with client_as(people.as_user(people.b)) as other:
                assert (
                    await other.post(
                        f"/api/v1/me/settings/cards/{card['event_id']}/settle",
                        json={
                            "organization_id": people.org,
                            "verb": "confirm",
                            "version": card["version"],
                        },
                    )
                ).status_code == 404

            confirmed = await c.post(
                f"/api/v1/me/settings/cards/{card['event_id']}/settle",
                json={
                    "organization_id": people.org,
                    "verb": "confirm",
                    "version": card["version"],
                },
            )
            assert confirmed.status_code == 200, confirmed.text
            assert confirmed.json()["state"] == "armed"
            assert queue.await_count == 1

            # The worker runs it -- twice, as a retried job would; it runs once.
            await actions.run(card["event_id"], people.org)
            await actions.run(card["event_id"], people.org)
            done = (
                await c.get(
                    f"/api/v1/me/settings/cards/{card['event_id']}?organization_id={people.org}"
                )
            ).json()
            assert done["state"] == "done" and "Forgotten" in done["note"]
            gone = (await c.get("/api/v1/me/memory")).json()
            assert "tea" not in [f["key"] for f in gone["mine"]]

            undone = await c.post(
                f"/api/v1/me/settings/cards/{card['event_id']}/settle",
                json={"organization_id": people.org, "verb": "undo"},
            )
            assert undone.status_code == 200 and undone.json()["state"] == "undone"
            back = (await c.get("/api/v1/me/memory")).json()
            assert "tea" in [f["key"] for f in back["mine"]]
            history = [
                h["change"]
                for h in (await c.get(f"/api/v1/me/memory/{a_fact}")).json()["history"]
            ]
            assert history[-2:] == ["forgotten", "restored"]

        # Nothing about it was said on the workspace's shared thread.
        async with db_client.async_session() as session:
            lines = (
                (
                    await session.execute(
                        select(AgentEventModel.summary).where(
                            AgentEventModel.organization_id == people.org,
                            AgentEventModel.kind == "message",
                        )
                    )
                )
                .scalars()
                .all()
            )
        assert lines == []

    async def test_confirm_must_name_the_version_shown(
        self, people, manager_on, queue, monkeypatch
    ):
        monkeypatch.setattr(constants, "TASK_LEDGER_ENABLED", True)
        a_fact, _, _ = await _facts(people)
        async with client_as(people.as_user(people.a)) as c:
            card = (await c.post(f"/api/v1/me/memory/{a_fact}/forget")).json()
            assert card["ledger_state"] == "awaiting_approval"
            wrong = await c.post(
                f"/api/v1/me/settings/cards/{card['event_id']}/settle",
                json={
                    "organization_id": people.org,
                    "verb": "confirm",
                    "version": "not-it",
                },
            )
            assert wrong.status_code == 409
            assert queue.await_count == 0
            right = await c.post(
                f"/api/v1/me/settings/cards/{card['event_id']}/settle",
                json={
                    "organization_id": people.org,
                    "verb": "confirm",
                    "version": card["version"],
                },
            )
            assert right.status_code == 200 and right.json()["state"] == "armed"

    async def test_declined_nothing_happens(self, people, manager_on, queue):
        a_fact, _, _ = await _facts(people)
        async with client_as(people.as_user(people.a)) as c:
            card = (await c.post(f"/api/v1/me/memory/{a_fact}/forget")).json()
            declined = await c.post(
                f"/api/v1/me/settings/cards/{card['event_id']}/settle",
                json={"organization_id": people.org, "verb": "decline"},
            )
            assert declined.json()["state"] == "declined"
            assert "tea" in [
                f["key"] for f in (await c.get("/api/v1/me/memory")).json()["mine"]
            ]


@pytest.mark.asyncio
class TestSharing:
    async def test_preview_then_share_moves_exactly_that_fact(self, people, manager_on):
        a_fact, _, _ = await _facts(people)
        async with client_as(people.as_user(people.a)) as c:
            preview = await c.get(
                f"/api/v1/me/memory/{a_fact}/share-preview?destination={people.org}"
            )
            assert preview.status_code == 200
            body = preview.json()
            assert body["moves"] is True and body["value"] == "masala, no sugar"
            assert body["members"] == 2
            assert any("Everyone in" in line for line in body["lines"])

            changed = await c.post(
                f"/api/v1/me/memory/{a_fact}/share",
                json={
                    "destination_organization_id": people.org,
                    "expected_value": "old words",
                },
            )
            assert changed.status_code == 409

            shared = await c.post(
                f"/api/v1/me/memory/{a_fact}/share",
                json={
                    "destination_organization_id": people.org,
                    "expected_value": "masala, no sugar",
                },
            )
            assert shared.status_code == 200, shared.text
            new_id = shared.json()["shared_fact_id"]
        async with client_as(people.as_user(people.b)) as c:
            seen = (await c.get("/api/v1/me/memory")).json()
            assert "tea" in [f["key"] for f in seen["workspace"]]
            history = (await c.get(f"/api/v1/me/memory/{new_id}")).json()["history"]
            assert history[-1]["change"] == "shared"

    async def test_only_into_a_workspace_you_are_in(self, people, manager_on):
        a_fact, _, _ = await _facts(people)
        async with client_as(people.as_user(people.a)) as c:
            answer = await c.get(
                f"/api/v1/me/memory/{a_fact}/share-preview?destination={people.other_org}"
            )
            assert answer.status_code == 404
            places = (await c.get("/api/v1/me/memory/destinations")).json()
        assert [p["organization_id"] for p in places] == [people.org]

    async def test_a_workspace_fact_is_not_yours_to_share(self, people, manager_on):
        _, _, workspace_fact = await _facts(people)
        async with client_as(people.as_user(people.a)) as c:
            answer = await c.get(
                f"/api/v1/me/memory/{workspace_fact}/share-preview?destination={people.org}"
            )
        assert answer.status_code == 422
