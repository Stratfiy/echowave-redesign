"""The provider-keys screen leads with the vendor, not with a component.

The add-key form used to open on "Component: stt / llm / tts", asking which
slot before the vendor was even named. For a vendor that does everything —
Sarvam, OpenAI — that made adding one key feel like adding three, because
nothing on the form said the vendor already covered the other two. The fix is
this route: it hands the screen the same answer ``components_for_provider``
already gives the fan-out, so the form can lead with "Provider" and read off
what it will cover before a key is ever pasted in.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from httpx import ASGITransport, AsyncClient

from api.db.models import UserModel
from api.enums import StaffRole


async def _staff(session, slug: str) -> UserModel:
    user = UserModel(provider_id=f"staff-{slug}", staff_role=StaffRole.SUPERADMIN.value)
    session.add(user)
    await session.flush()
    return user


def _client(user):
    from api.app import app
    from api.services.auth.depends import get_superuser

    @asynccontextmanager
    async def _ctx():
        async def _override():
            return user

        app.dependency_overrides[get_superuser] = _override
        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                yield client
        finally:
            app.dependency_overrides.pop(get_superuser, None)

    return _ctx()


class TestTheProviderListEndpoint:
    async def test_it_is_staff_only(self, db_session, async_session):
        from api.app import app

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/v1/admin/provider-keys/providers")
        assert response.status_code in (401, 403)

    async def test_a_multi_component_vendor_reports_all_of_them(
        self, db_session, async_session
    ):
        staff = await _staff(async_session, "list")

        async with _client(staff) as client:
            response = await client.get("/api/v1/admin/provider-keys/providers")

        assert response.status_code == 200
        by_name = {
            row["provider"]: row["components"] for row in response.json()["providers"]
        }
        assert by_name["sarvam"] == ["llm", "stt", "tts"]

    async def test_a_realtime_capable_vendor_names_its_realtime_provider(
        self, db_session, async_session
    ):
        """ "realtime" among a vendor's components means a key for the vendor
        also unlocks speech-to-speech — never a slot ``apply_to_all_components``
        stores a row against. ``realtime_provider`` is what tells the screen
        which vendor to ask model discovery about for that coverage."""
        staff = await _staff(async_session, "realtime")

        async with _client(staff) as client:
            response = await client.get("/api/v1/admin/provider-keys/providers")

        by_name = {row["provider"]: row for row in response.json()["providers"]}
        assert by_name["openai"]["realtime_provider"] == "openai_realtime"
        assert by_name["sarvam"]["realtime_provider"] is None

    async def test_the_managed_sentinel_is_not_offered(self, db_session, async_session):
        """Nobody pastes a key in under "decibyl" — it names the managed tier,
        not a vendor account anyone holds."""
        staff = await _staff(async_session, "sentinel")

        async with _client(staff) as client:
            response = await client.get("/api/v1/admin/provider-keys/providers")

        assert "decibyl" not in {
            row["provider"] for row in response.json()["providers"]
        }

    async def test_it_agrees_with_the_fan_out_endpoint(self, db_session, async_session):
        """Same source of truth the key-storage route reads for
        ``apply_to_all_components`` — a form offering "covers stt, llm, tts"
        for a vendor the fan-out only applies to two of would be a lie the
        screen tells before any key is even saved.

        "realtime" is the deliberate exception: real coverage, but never a
        slot ``apply_to_all_components`` writes a row against on its own — see
        ``TestRealtimeIsVisibleWithoutItsOwnKey`` in test_provider_key_fanout.py."""
        from api.services.configuration.registry import components_for_provider

        staff = await _staff(async_session, "agree")

        async with _client(staff) as client:
            response = await client.get("/api/v1/admin/provider-keys/providers")

        for row in response.json()["providers"]:
            keyable = [c for c in row["components"] if c != "realtime"]
            assert tuple(keyable) == tuple(
                sorted(components_for_provider(row["provider"]))
            )
