"""A per-agent model override has to find the account's own key.

Found on a live account: OpenAI stored and listed as "In use" under Provider
Keys, an agent overridden to OpenAI, and the settings screen showing OpenAI's
own "Missing credentials" error.

The cause was that override enrichment copied secrets from the *global config
section*. That works only when the override names the same provider the account
already runs globally — which is the one case a per-agent override is not for.
Pick a different vendor, the shape of the feature, and the key came back empty.
"""

from types import SimpleNamespace

import pytest

from api.services.configuration import resolve


class _Session:
    """Stands in for a DB session; the vault lookup is what is patched."""


@pytest.fixture
def vault(monkeypatch):
    """Control which (component, provider) pairs the account holds a key for."""
    held: dict[tuple[str, str], str] = {}

    async def fake_resolve(session, *, organization_id, component, provider):
        value = component.value if hasattr(component, "value") else str(component)
        return held.get((value, provider))

    from api.services.configuration import organization_credentials

    monkeypatch.setattr(organization_credentials, "resolve_api_key", fake_resolve)
    return held


@pytest.mark.asyncio
class TestVaultEnrichment:
    async def test_a_divergent_override_gets_the_stored_key(self, vault):
        # The reported bug, exactly: global stack is Sarvam, this agent is
        # overridden to OpenAI, and an OpenAI key is in the vault.
        vault[("llm", "openai")] = "sk-from-the-vault"

        result = await resolve.enrich_overrides_from_vault(
            _Session(),
            {"llm": {"provider": "openai", "model": "gpt-4.1"}},
            organization_id=30,
        )
        assert result["llm"]["api_key"] == "sk-from-the-vault"

    async def test_an_explicit_key_is_never_overwritten(self, vault):
        # Somebody typed a key into the override. It outranks the vault.
        vault[("llm", "openai")] = "sk-from-the-vault"

        result = await resolve.enrich_overrides_from_vault(
            _Session(),
            {"llm": {"provider": "openai", "api_key": "sk-typed-in"}},
            organization_id=30,
        )
        assert result["llm"]["api_key"] == "sk-typed-in"

    async def test_a_provider_with_no_stored_key_is_left_alone(self, vault):
        # Genuinely unconfigured. Saying so beats inventing a credential.
        result = await resolve.enrich_overrides_from_vault(
            _Session(), {"llm": {"provider": "anthropic"}}, organization_id=30
        )
        assert "api_key" not in result["llm"]

    async def test_realtime_uses_the_llm_credential(self, vault):
        # A realtime model is a language model that speaks; the vendor issues
        # one key for both, and the vault stores it under llm.
        vault[("llm", "openai_realtime")] = "sk-realtime"

        result = await resolve.enrich_overrides_from_vault(
            _Session(),
            {"realtime": {"provider": "openai_realtime"}},
            organization_id=30,
        )
        assert result["realtime"]["api_key"] == "sk-realtime"

    async def test_voice_and_transcription_use_their_own_components(self, vault):
        vault[("tts", "rumik")] = "rumik-key"
        vault[("stt", "sarvam")] = "sarvam-key"

        result = await resolve.enrich_overrides_from_vault(
            _Session(),
            {"tts": {"provider": "rumik"}, "stt": {"provider": "sarvam"}},
            organization_id=30,
        )
        assert result["tts"]["api_key"] == "rumik-key"
        assert result["stt"]["api_key"] == "sarvam-key"

    async def test_the_input_is_not_mutated(self, vault):
        vault[("llm", "openai")] = "sk-from-the-vault"
        original = {"llm": {"provider": "openai"}}

        await resolve.enrich_overrides_from_vault(
            _Session(), original, organization_id=30
        )
        assert "api_key" not in original["llm"]

    async def test_a_section_with_no_provider_is_skipped(self, vault):
        result = await resolve.enrich_overrides_from_vault(
            _Session(), {"llm": {"model": "gpt-4.1"}}, organization_id=30
        )
        assert "api_key" not in result["llm"]

    async def test_empty_overrides_are_fine(self, vault):
        assert (
            await resolve.enrich_overrides_from_vault(
                _Session(), {}, organization_id=30
            )
            == {}
        )


class TestTheOldPathStillWorks:
    """The global-section copy is still needed and must not have regressed."""

    def test_a_matching_provider_still_inherits_from_the_global_config(self):
        global_llm = SimpleNamespace(provider="openai", api_key="sk-global")
        user_config = SimpleNamespace(llm=global_llm, tts=None, stt=None, realtime=None)

        result = resolve.enrich_overrides_with_api_keys(
            {"llm": {"provider": "openai", "model": "gpt-4.1"}}, user_config
        )
        assert result["llm"]["api_key"] == "sk-global"

    def test_a_different_provider_gets_nothing_from_the_global_config(self):
        # Not a bug in this function — it is why the vault path exists.
        global_llm = SimpleNamespace(provider="sarvam", api_key="sarvam-key")
        user_config = SimpleNamespace(llm=global_llm, tts=None, stt=None, realtime=None)

        result = resolve.enrich_overrides_with_api_keys(
            {"llm": {"provider": "openai"}}, user_config
        )
        assert "api_key" not in result["llm"]
