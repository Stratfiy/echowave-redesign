"""Simple mode (launch stream care): a person's own preference, reachable
from their preferences and switchable back.

Done when: with ``care_simple_mode`` on, a person turns it on, reads it back
on, turns it off again; it is theirs alone (a colleague's stays off); it
only takes true or false; and a stale save is a conflict like every other
preference.
"""

from __future__ import annotations

import pytest

from api.services import member_preferences
from api.tests import care_support as cs


@pytest.fixture
async def two(test_engine):
    a = await cs.person("simple-a")
    b = await cs.person("simple-b")
    org = await cs.workspace(a.id)
    yield a, b, org
    await cs.cleanup(org)


@pytest.mark.asyncio
async def test_on_read_back_and_switched_back(two, monkeypatch):
    a, b, org = two
    cs.all_on(monkeypatch)
    async with cs.client(a.id, org) as c:
        first = (await c.get("/api/v1/me/preferences")).json()
        assert first["simple_mode"] is None
        on = await c.put(
            "/api/v1/me/preferences",
            json={"simple_mode": True, "revision": first["revision"]},
        )
        assert on.status_code == 200 and on.json()["simple_mode"] is True
        assert (await c.get("/api/v1/me/preferences")).json()["simple_mode"] is True
        off = await c.put(
            "/api/v1/me/preferences",
            json={"simple_mode": False, "revision": on.json()["revision"]},
        )
        assert off.status_code == 200 and off.json()["simple_mode"] is False
    assert await member_preferences.simple_mode_of(a.id) is False
    assert await member_preferences.simple_mode_of(b.id) is False


@pytest.mark.asyncio
async def test_it_is_only_this_persons(two, monkeypatch):
    a, b, _ = two
    cs.all_on(monkeypatch)
    await member_preferences.save(a.id, {"simple_mode": True}, revision=0)
    assert await member_preferences.simple_mode_of(a.id) is True
    assert await member_preferences.simple_mode_of(b.id) is False


@pytest.mark.asyncio
async def test_only_on_or_off(two, monkeypatch):
    a, _, org = two
    cs.all_on(monkeypatch)
    with pytest.raises(member_preferences.PreferenceInvalid):
        await member_preferences.save(a.id, {"simple_mode": "yes"}, revision=0)
    async with cs.client(a.id, org) as c:
        bad = await c.put(
            "/api/v1/me/preferences", json={"simple_mode": "loud", "revision": 0}
        )
        assert bad.status_code == 422


@pytest.mark.asyncio
async def test_a_stale_switch_is_a_conflict(two, monkeypatch):
    a, _, org = two
    cs.all_on(monkeypatch)
    await member_preferences.save(a.id, {"simple_mode": True}, revision=0)
    async with cs.client(a.id, org) as c:
        stale = await c.put(
            "/api/v1/me/preferences", json={"simple_mode": False, "revision": 0}
        )
        assert stale.status_code == 409
        assert stale.json()["detail"]["stored"]["simple_mode"] is True


@pytest.mark.asyncio
async def test_status_says_simple_mode_needs_preferences(two, monkeypatch):
    a, _, org = two
    cs.all_on(monkeypatch)
    from api import constants

    monkeypatch.setattr(constants, "MEMBER_PREFERENCES_ENABLED", False)
    async with cs.client(a.id, org) as c:
        parts = {
            p["key"]: p for p in (await c.get("/api/v1/care/status")).json()["parts"]
        }
    assert parts["care_simple_mode"]["state"] == "needs_setup"
    assert "preferences" in parts["care_simple_mode"]["reason"]
