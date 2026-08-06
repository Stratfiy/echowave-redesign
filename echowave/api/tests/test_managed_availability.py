"""Which slots may be offered as managed.

The rule under test is one sentence: a slot is available only if we hold an
active platform key for the provider its default tier resolves to. Everything
else — the "coming soon" badge, the disabled toggle — is a rendering of this.
"""

import pytest

from api.services.configuration import managed_resolution


class _FakeSession:
    pass


@pytest.fixture
def held(monkeypatch):
    """Control exactly which (component, provider) pairs have a key."""
    store: set[tuple[str, str]] = set()

    async def fake_resolve(session, *, component, provider):
        value = component.value if hasattr(component, "value") else str(component)
        return "a-key" if (value, provider) in store else None

    monkeypatch.setattr(
        managed_resolution.platform_credentials, "resolve_api_key", fake_resolve
    )
    return store


@pytest.mark.asyncio
class TestManagedAvailability:
    async def test_nothing_held_means_nothing_offered(self, held):
        result = await managed_resolution.managed_availability(_FakeSession())
        assert result
        assert not any(result.values())

    async def test_a_held_key_makes_its_slot_available(self, held, monkeypatch):
        upstream = managed_resolution.managed_tiers.resolve("stt", "default")
        held.add(("stt", upstream.provider))

        result = await managed_resolution.managed_availability(_FakeSession())
        assert result["stt"] is True
        assert result["llm"] is False

    async def test_every_managed_section_is_reported(self, held):
        result = await managed_resolution.managed_availability(_FakeSession())
        expected = {name for name, _ in managed_resolution.MANAGED_SECTIONS}
        assert set(result) == expected

    async def test_realtime_rides_on_the_llm_key(self, held):
        # A realtime model is a language model that speaks; the vendor issues
        # one key for both. Holding the LLM key must light up realtime too,
        # otherwise an operator pastes the same key twice to no effect.
        realtime = managed_resolution.managed_tiers.resolve(
            managed_resolution.managed_tiers.REALTIME_COMPONENT, "default"
        )
        held.add(("llm", realtime.provider))

        result = await managed_resolution.managed_availability(_FakeSession())
        assert result["realtime"] is True

    async def test_embeddings_ride_on_the_llm_key(self, held):
        embeddings = managed_resolution.managed_tiers.resolve(
            managed_resolution.managed_tiers.EMBEDDINGS_COMPONENT, "default"
        )
        held.add(("llm", embeddings.provider))

        result = await managed_resolution.managed_availability(_FakeSession())
        assert result["embeddings"] is True

    async def test_values_are_booleans_not_keys(self, held):
        # This dict is serialised to the browser. A key leaking into it would
        # be the worst possible bug in this file.
        upstream = managed_resolution.managed_tiers.resolve("tts", "default")
        held.add(("tts", upstream.provider))

        result = await managed_resolution.managed_availability(_FakeSession())
        assert all(isinstance(v, bool) for v in result.values())
