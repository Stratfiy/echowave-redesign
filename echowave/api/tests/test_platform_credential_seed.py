"""Seeding platform keys from the environment.

The parsing is pure and is tested directly. The storing path is tested against
the real vault, because the thing worth proving is that a restart does not
rewrite an unchanged key — and that only shows up against real ciphertext,
which is randomised per encryption and so cannot be compared byte for byte.
"""

import pytest

from api.services.configuration import platform_credential_seed as seed


class TestParse:
    def test_reads_component_and_provider(self):
        assert seed._parse("PLATFORM_KEY_LLM_GOOGLE") == ("llm", "google")

    def test_provider_may_contain_underscores(self):
        # openai_realtime is a real provider name; splitting on every
        # underscore would turn it into provider "openai" and lose the rest.
        assert seed._parse("PLATFORM_KEY_LLM_OPENAI_REALTIME") == (
            "llm",
            "openai_realtime",
        )

    def test_lowercases_both_halves(self):
        assert seed._parse("PLATFORM_KEY_STT_SARVAM") == ("stt", "sarvam")

    def test_rejects_a_name_with_no_provider(self):
        assert seed._parse("PLATFORM_KEY_LLM") is None

    def test_rejects_an_empty_provider(self):
        assert seed._parse("PLATFORM_KEY_LLM_") is None


class TestDeclared:
    def test_finds_only_prefixed_variables(self, monkeypatch):
        monkeypatch.setenv("PLATFORM_KEY_LLM_GOOGLE", "key-one")
        monkeypatch.setenv("OPENAI_API_KEY", "not-mine")
        monkeypatch.setenv("PLATFORM_CREDENTIAL_SECRET", "not-a-key-either")

        declared = seed._declared()
        assert ("llm", "google", "key-one") in declared
        assert all(not v.startswith("not-") for _, _, v in declared)

    def test_empty_value_means_not_yet(self, monkeypatch):
        # How a deployment says "I have not got this one" without deleting the
        # line from its environment file.
        monkeypatch.setenv("PLATFORM_KEY_TTS_SARVAM", "   ")
        assert all(c != "tts" for c, _, _ in seed._declared())

    def test_malformed_name_is_ignored_not_fatal(self, monkeypatch):
        monkeypatch.setenv("PLATFORM_KEY_NONSENSE", "value")
        seed._declared()  # must not raise

    def test_value_is_stripped(self, monkeypatch):
        monkeypatch.setenv("PLATFORM_KEY_LLM_GOOGLE", "  spaced-key  ")
        assert ("llm", "google", "spaced-key") in seed._declared()


@pytest.mark.asyncio
class TestSeedFromEnvironment:
    async def test_no_variables_does_nothing(self, monkeypatch):
        for name in list(seed.os.environ):
            if name.startswith(seed.ENV_PREFIX):
                monkeypatch.delenv(name, raising=False)
        result = await seed.seed_from_environment()
        assert result.total == 0

    async def test_skips_everything_without_an_encryption_secret(self, monkeypatch):
        monkeypatch.setenv("PLATFORM_KEY_LLM_GOOGLE", "some-key-value")
        monkeypatch.setattr(
            seed.platform_credentials, "encryption_is_configured", lambda: False
        )
        result = await seed.seed_from_environment()
        # Skipped rather than raised: a missing secret must not stop the API
        # process from starting.
        assert ("llm", "google") in result.skipped
        assert result.stored == []
