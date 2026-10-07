"""Privacy and security for one person (screen 25).

Done when: the person sees the effective retention their workspace runs (and
that it is the workspace's to change), MFA state, and their own requests;
an export of their own data is built by the worker, contains theirs and no
one else's, and expires; deletion needs an identity check and a card in
their personal space, deletes every store of theirs and nothing of anybody
else's, and keeps a record of each store with the exceptions that remain.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import text

from api import constants
from api.db import db_client
from api.services import member_preferences
from api.services.settings import privacy
from api.services.workflow import actions
from api.tests.support.settings_people import clean, client_as, make_people


@pytest.fixture
async def people(test_engine):
    found = await make_people("setpriv")
    try:
        yield found
    finally:
        await clean(found)


@pytest.fixture
def privacy_on(monkeypatch):
    monkeypatch.setattr(constants, "PRIVACY_CENTER_ENABLED", True)
    monkeypatch.setattr(constants, "PERSONAL_MEMORY_ENABLED", True)
    monkeypatch.setattr(constants, "MEMBER_PREFERENCES_ENABLED", True)


@pytest.fixture
def space_on(monkeypatch):
    monkeypatch.setattr(constants, "PERSONAL_SPACE_ENABLED", True)


@pytest.fixture
def worker(monkeypatch):
    """Jobs run inline: the export is built, the card's run is captured."""
    calls: list[tuple] = []

    async def enqueue(name, *args, **_kwargs):
        calls.append((name, *args))
        if name == "build_personal_export":
            await privacy.build_export(args[0])

    monkeypatch.setattr("api.tasks.arq.enqueue_job", AsyncMock(side_effect=enqueue))
    return calls


async def _theirs_and_mine(people) -> None:
    await member_preferences.save(people.a.id, {"language": "hi-IN"}, revision=0)
    await member_preferences.save(people.b.id, {"language": "ta-IN"}, revision=0)
    await db_client.remember_organisation_facts(
        organization_id=people.org, facts={"tea": "masala"}, user_id=people.a.id
    )
    await db_client.remember_organisation_facts(
        organization_id=people.org, facts={"coffee": "filter"}, user_id=people.b.id
    )
    await db_client.remember_organisation_facts(
        organization_id=people.org, facts={"opening_hours": "9 to 6"}
    )


@pytest.mark.asyncio
class TestTheOverview:
    async def test_off_the_routes_are_not_there(self, people):
        async with client_as(people.as_user(people.a)) as c:
            assert (await c.get("/api/v1/me/privacy")).status_code == 404

    async def test_effective_policy_and_who_manages_it(self, people, privacy_on):
        async with client_as(people.as_user(people.b)) as c:
            got = (await c.get("/api/v1/me/privacy")).json()
        retention = got["retention"]
        assert retention["recording_days"] >= 1 and retention["transcript_days"] >= 1
        # B is a member: the workspace's retention is managed by its admins.
        assert retention["managed_by_workspace"] is True
        assert got["security"]["mfa_enabled"] is False
        assert got["deletion_available"] is False  # personal space off
        async with client_as(people.as_user(people.a)) as c:
            owner = (await c.get("/api/v1/me/privacy")).json()
        assert owner["retention"]["managed_by_workspace"] is False
        assert owner["workspace_owner"] is True


@pytest.mark.asyncio
class TestExport:
    async def test_built_by_the_worker_mine_only_and_it_expires(
        self, people, privacy_on, worker
    ):
        await _theirs_and_mine(people)
        async with client_as(people.as_user(people.a)) as c:
            preview = (await c.get("/api/v1/me/privacy/preview?kind=export")).json()
            assert (
                next(s for s in preview["stores"] if s["store"] == "memory")["count"]
                == 1
            )
            asked = await c.post("/api/v1/me/privacy/export")
            assert asked.status_code == 200
            request = asked.json()
            assert worker[0][0] == "build_personal_export"
            ready = (await c.get(f"/api/v1/me/privacy/requests/{request['id']}")).json()
            assert ready["status"] == "ready" and ready["expires_at"]
            data = (
                await c.get(f"/api/v1/me/privacy/requests/{request['id']}/download")
            ).json()
        assert data["preferences"]["language"] == "hi-IN"
        assert [m["key"] for m in data["memory"]] == ["tea"]
        flat = str(data)
        assert (
            "coffee" not in flat and "ta-IN" not in flat and "opening_hours" not in flat
        )

        async with client_as(people.as_user(people.b)) as c:
            # Not B's to read.
            assert (
                await c.get(f"/api/v1/me/privacy/requests/{request['id']}/download")
            ).status_code == 404

        async with db_client.async_session() as session:
            await session.execute(
                text("UPDATE personal_data_requests SET expires_at = :t WHERE id = :i"),
                {"t": datetime.now(UTC) - timedelta(minutes=1), "i": request["id"]},
            )
            await session.commit()
        async with client_as(people.as_user(people.a)) as c:
            expired = await c.get(
                f"/api/v1/me/privacy/requests/{request['id']}/download"
            )
            assert expired.status_code == 410
            assert (await c.get(f"/api/v1/me/privacy/requests/{request['id']}")).json()[
                "status"
            ] == "expired"


@pytest.mark.asyncio
class TestDeletion:
    async def test_without_a_personal_space_it_says_so(self, people, privacy_on):
        async with client_as(people.as_user(people.a)) as c:
            answer = await c.post(
                "/api/v1/me/privacy/deletion", json={"phrase": "delete my data"}
            )
        assert answer.status_code == 503

    async def test_needs_the_identity_check(self, people, privacy_on, space_on):
        async with client_as(people.as_user(people.a)) as c:
            answer = await c.post("/api/v1/me/privacy/deletion", json={"phrase": "yes"})
        assert answer.status_code == 422 and "delete my data" in answer.json()["detail"]

    async def test_a_card_then_every_store_of_mine_and_nothing_of_theirs(
        self, people, privacy_on, space_on, worker
    ):
        await _theirs_and_mine(people)
        async with client_as(people.as_user(people.a)) as c:
            await c.post(
                "/api/v1/me/saved", json={"title": "x"}
            )  # flag off: 404, harmless
            asked = await c.post(
                "/api/v1/me/privacy/deletion", json={"phrase": "Delete my data"}
            )
            assert asked.status_code == 200, asked.text
            request = asked.json()
            card = request["card"]
            assert request["status"] == "awaiting_approval"
            assert card["reversible"] is False and "cannot be undone" in card["effect"]
            # Nothing is gone yet.
            assert (await member_preferences.get(people.a.id))["language"] == "hi-IN"
            # The card is in A's personal space, not the business workspace.
            assert card["organization_id"] != people.org
            confirmed = await c.post(
                f"/api/v1/me/settings/cards/{card['event_id']}/settle",
                json={
                    "organization_id": card["organization_id"],
                    "verb": "confirm",
                    "version": card["version"],
                },
            )
            assert confirmed.status_code == 200, confirmed.text
            await actions.run(card["event_id"], card["organization_id"])
            done = (await c.get(f"/api/v1/me/privacy/requests/{request['id']}")).json()
        assert done["status"] == "complete"
        stores = {s["store"]: s for s in done["stores"]}
        assert (
            stores["memory"]["status"] == "deleted" and stores["memory"]["count"] == 1
        )
        assert stores["preferences"]["count"] == 1
        assert stores["sign_in"]["status"] == "exception"
        # Mine are gone; B's and the workspace's are not.
        assert (await member_preferences.get(people.a.id))["revision"] == 0
        assert (await member_preferences.get(people.b.id))["language"] == "ta-IN"
        left = await db_client.organisation_memory(
            organization_id=people.org, include_bots=True, user_id=people.b.id
        )
        assert {r.key for r in left} == {"coffee", "opening_hours"}

    async def test_a_second_ask_is_the_same_request(self, people, privacy_on, space_on):
        async with client_as(people.as_user(people.a)) as c:
            first = (
                await c.post(
                    "/api/v1/me/privacy/deletion", json={"phrase": "delete my data"}
                )
            ).json()
            again = (
                await c.post(
                    "/api/v1/me/privacy/deletion", json={"phrase": "delete my data"}
                )
            ).json()
        assert again["id"] == first["id"]

    async def test_every_personal_table_is_covered(self):
        # A table holding a person's own data must be exported and deleted.
        assert {key for key, _ in privacy.STORES} == {
            "preferences",
            "onboarding",
            "memory",
            "saved_items",
            "feedback",
            "temporary",
            "personal_space",
            "people",
        }
