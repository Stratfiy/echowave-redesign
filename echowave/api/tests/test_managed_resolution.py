"""managed_resolution's direct path: a real provider + use_platform_key, with
no tier translation involved.

The tier path (``provider="decibyl"``) is exercised by test_managed_language.py
and test_managed_availability.py; this file covers the newer, additive path
where a customer picks an exact vendor and model themselves -- same as BYOK --
but runs it on Decibyl's platform key instead of their own.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from api.services.configuration import managed_resolution
from api.services.configuration.registry import (
    DecibylLLMService,
    OpenAILLMService,
    SarvamLLMConfiguration,
)


def _effective(**sections):
    base = {"llm": None, "stt": None, "tts": None, "realtime": None, "embeddings": None}
    base.update(sections)
    return SimpleNamespace(**base)


class _FakeSession:
    pass


@pytest.mark.asyncio
class TestDirectProviderPath:
    async def test_a_real_provider_with_use_platform_key_is_resolved_without_a_tier(
        self, monkeypatch
    ):
        section = OpenAILLMService(model="gpt-4o", api_key="", use_platform_key=True)
        effective = _effective(llm=section)

        async def fake_key(session, *, component, provider):
            assert provider == "openai"
            return "platform-key-xyz"

        monkeypatch.setattr(
            managed_resolution.platform_credentials, "resolve_api_key", fake_key
        )

        await managed_resolution.apply(effective)

        assert effective.llm.provider == "openai"
        assert effective.llm.model == "gpt-4o"  # untouched -- no tier translation
        assert effective.llm.api_key == "platform-key-xyz"

    async def test_a_missing_platform_key_leaves_the_section_alone(self, monkeypatch):
        section = SarvamLLMConfiguration(
            model="sarvam-105b", api_key="", use_platform_key=True
        )
        effective = _effective(llm=section)

        async def fake_key(session, *, component, provider):
            return None

        monkeypatch.setattr(
            managed_resolution.platform_credentials, "resolve_api_key", fake_key
        )

        await managed_resolution.apply(effective)

        assert effective.llm.provider == "sarvam"  # unchanged -- not silently swapped
        assert effective.llm.api_key == ""

    async def test_a_provider_without_the_flag_is_untouched(self, monkeypatch):
        """An ordinary BYOK section -- managed_resolution must not touch it,
        let alone try to authenticate it with a platform key."""
        section = OpenAILLMService(model="gpt-4o", api_key="sk-real-key")
        effective = _effective(llm=section)

        async def fake_key(session, *, component, provider):
            raise AssertionError("should not be called for a non-managed section")

        monkeypatch.setattr(
            managed_resolution.platform_credentials, "resolve_api_key", fake_key
        )

        await managed_resolution.apply(effective)

        assert effective.llm.api_key == "sk-real-key"

    async def test_a_customer_chosen_endpoint_never_receives_the_platform_key(
        self, monkeypatch
    ):
        """base_url exists so BYOK can point their own key at a proxy or
        self-hosted server -- their key, their destination. On a
        use_platform_key section that destination would receive *our* key
        instead, so any customer override must be discarded back to the
        vendor's real endpoint rather than trusted."""
        section = OpenAILLMService(
            model="gpt-4o",
            api_key="",
            use_platform_key=True,
            base_url="https://attacker.example.com/v1",
        )
        effective = _effective(llm=section)

        async def fake_key(session, *, component, provider):
            return "platform-key-xyz"

        monkeypatch.setattr(
            managed_resolution.platform_credentials, "resolve_api_key", fake_key
        )

        await managed_resolution.apply(effective)

        assert effective.llm.base_url == "https://api.openai.com/v1"
        assert effective.llm.api_key == "platform-key-xyz"

    async def test_language_is_not_translated_on_the_direct_path(self, monkeypatch):
        """managed_language's translation table exists for the tier path,
        where the customer's language is in Decibyl's vocabulary. On the
        direct path the customer already picked a language in the real
        vendor's own vocabulary (the same form BYOK renders) -- translating
        it again would corrupt it."""
        from api.services.configuration.registry import SarvamSTTConfiguration

        section = SarvamSTTConfiguration(
            model="saarika:v2.5",
            api_key="",
            use_platform_key=True,
            language="en-IN",
        )
        effective = _effective(stt=section)

        async def fake_key(session, *, component, provider):
            return "platform-key"

        monkeypatch.setattr(
            managed_resolution.platform_credentials, "resolve_api_key", fake_key
        )

        await managed_resolution.apply(effective)

        assert effective.stt.language == "en-IN"


class TestIsManaged:
    def test_the_decibyl_tier_marker_is_managed(self):
        section = DecibylLLMService(model="default")
        assert section.is_managed is True

    def test_a_real_provider_with_the_flag_is_managed(self):
        section = OpenAILLMService(model="gpt-4o", api_key="", use_platform_key=True)
        assert section.is_managed is True

    def test_a_real_provider_without_the_flag_is_not_managed(self):
        section = OpenAILLMService(model="gpt-4o", api_key="sk-x")
        assert section.is_managed is False


@pytest.mark.asyncio
class TestPlatformProviderCatalog:
    async def test_reshapes_by_section_name(self, monkeypatch):
        async def fake_managed_providers(session):
            return {"llm": ["openai", "google"], "stt": ["sarvam"], "tts": []}

        monkeypatch.setattr(
            managed_resolution.platform_credentials,
            "managed_providers",
            fake_managed_providers,
        )

        result = await managed_resolution.platform_provider_catalog(_FakeSession())

        assert result["llm"] == ["openai", "google"]
        assert result["stt"] == ["sarvam"]
        assert result["tts"] == []

    async def test_realtime_and_embeddings_ride_on_the_llm_bucket(self, monkeypatch):
        async def fake_managed_providers(session):
            return {"llm": ["openai"]}

        monkeypatch.setattr(
            managed_resolution.platform_credentials,
            "managed_providers",
            fake_managed_providers,
        )

        result = await managed_resolution.platform_provider_catalog(_FakeSession())

        assert result["realtime"] == ["openai"]
        assert result["embeddings"] == ["openai"]

    async def test_an_unheld_component_is_an_empty_list_not_missing(self, monkeypatch):
        async def fake_managed_providers(session):
            return {}

        monkeypatch.setattr(
            managed_resolution.platform_credentials,
            "managed_providers",
            fake_managed_providers,
        )

        result = await managed_resolution.platform_provider_catalog(_FakeSession())
        assert set(result) == {name for name, _ in managed_resolution.MANAGED_SECTIONS}
        assert all(v == [] for v in result.values())
