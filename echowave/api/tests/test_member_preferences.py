"""A person's own preferences (handoff 30, 31 item 3; launch stream controls).

Done when: language, timezone, voice and summary time persist for the
person across sessions, a stale save is a conflict that shows both values,
and saving them never changes another member's preferences or the
workspace's defaults.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from api import constants
from api.db import db_client
from api.services import member_preferences
from api.services.organization_preferences import get_organization_preferences


@pytest.fixture
def prefs_on(monkeypatch):
    monkeypatch.setattr(constants, "MEMBER_PREFERENCES_ENABLED", True)


@pytest.fixture
async def team(test_engine):
    run = uuid4().hex[:8]
    a, _ = await db_client.get_or_create_user_by_provider_id(f"prefs-a-{run}")
    b, _ = await db_client.get_or_create_user_by_provider_id(f"prefs-b-{run}")
    org, _ = await db_client.get_or_create_organization_by_provider_id(
        f"prefs-org-{run}", a.id
    )
    yield a, b, org.id
    from sqlalchemy import text

    async with db_client.async_session() as session:
        await session.execute(
            text("DELETE FROM credit_ledger WHERE organization_id = :o"), {"o": org.id}
        )
        await session.commit()


@pytest.mark.asyncio
class TestTheirOwn:
    async def test_saved_and_read_back(self, team):
        a, _, _ = team
        saved = await member_preferences.save(
            a.id,
            {
                "language": "ta-IN",
                "timezone": "Asia/Kolkata",
                "voice": "meera",
                "summary_time": "08:30",
            },
            revision=0,
        )
        assert saved["revision"] == 1
        again = await member_preferences.get(a.id)
        assert again["language"] == "ta-IN" and again["summary_time"] == "08:30"

    async def test_only_the_fields_sent_change(self, team):
        a, _, _ = team
        await member_preferences.save(
            a.id, {"language": "hi-IN", "timezone": "Asia/Kolkata"}, revision=0
        )
        saved = await member_preferences.save(a.id, {"voice": "arjun"}, revision=1)
        assert saved["language"] == "hi-IN" and saved["voice"] == "arjun"

    async def test_a_stale_save_is_a_conflict_not_an_overwrite(self, team):
        a, _, _ = team
        await member_preferences.save(a.id, {"language": "hi-IN"}, revision=0)
        await member_preferences.save(a.id, {"language": "bn-IN"}, revision=1)
        with pytest.raises(member_preferences.Conflict) as caught:
            # A second tab, still holding revision 1.
            await member_preferences.save(a.id, {"language": "en-IN"}, revision=1)
        assert caught.value.stored["language"] == "bn-IN"
        assert (await member_preferences.get(a.id))["language"] == "bn-IN"

    async def test_two_first_saves_one_wins(self, team):
        a, _, _ = team
        await member_preferences.save(a.id, {"language": "hi-IN"}, revision=0)
        with pytest.raises(member_preferences.Conflict):
            await member_preferences.save(a.id, {"language": "ta-IN"}, revision=0)

    @pytest.mark.parametrize(
        "change",
        [
            {"language": "klingon"},
            {"timezone": "Mars/Olympus"},
            {"summary_time": "25:00"},
            {"summary_time": "9am"},
            {"voice": "a voice; DROP TABLE"},
            {"organization_id": 1},
        ],
    )
    async def test_nonsense_is_refused(self, team, change):
        a, _, _ = team
        with pytest.raises(member_preferences.PreferenceInvalid):
            await member_preferences.save(a.id, change, revision=0)

    async def test_null_clears_a_field(self, team):
        a, _, _ = team
        await member_preferences.save(a.id, {"summary_time": "09:00"}, revision=0)
        saved = await member_preferences.save(a.id, {"summary_time": None}, revision=1)
        assert saved["summary_time"] is None


@pytest.mark.asyncio
class TestNobodyElses:
    async def test_mine_never_change_a_colleagues_or_the_workspaces(self, team):
        a, b, org = team
        await member_preferences.save(b.id, {"timezone": "Europe/London"}, revision=0)
        workspace_before = await get_organization_preferences(org)
        await member_preferences.save(a.id, {"timezone": "Asia/Kolkata"}, revision=0)
        assert (await member_preferences.get(b.id))["timezone"] == "Europe/London"
        assert await get_organization_preferences(org) == workspace_before


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
    async def test_off_the_route_is_not_there(self, team):
        a, _, org = team
        async with _client(SimpleNamespace(id=a.id, selected_organization_id=org)) as c:
            assert (await c.get("/api/v1/me/preferences")).status_code == 404

    async def test_save_read_and_conflict_over_http(self, team, prefs_on):
        a, b, org = team
        async with _client(SimpleNamespace(id=a.id, selected_organization_id=org)) as c:
            empty = (await c.get("/api/v1/me/preferences")).json()
            assert empty["revision"] == 0 and "ta-IN" in empty["languages"]
            saved = await c.put(
                "/api/v1/me/preferences",
                json={"language": "ta-IN", "summary_time": "08:00", "revision": 0},
            )
            assert saved.status_code == 200 and saved.json()["revision"] == 1
            stale = await c.put(
                "/api/v1/me/preferences", json={"language": "hi-IN", "revision": 0}
            )
            assert stale.status_code == 409
            assert stale.json()["detail"]["stored"]["language"] == "ta-IN"
            bad = await c.put(
                "/api/v1/me/preferences", json={"timezone": "Nowhere", "revision": 1}
            )
            assert bad.status_code == 422
            # No way to name somebody else.
            sneaky = await c.put(
                "/api/v1/me/preferences",
                json={"user_id": b.id, "language": "hi-IN", "revision": 1},
            )
            assert sneaky.status_code == 422
        assert (await member_preferences.get(b.id))["language"] is None
