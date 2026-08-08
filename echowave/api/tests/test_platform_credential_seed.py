"""Seeding platform keys from the environment.

The parsing is pure and is tested directly. The storing path is tested against
the real vault, because the thing worth proving is that a restart does not
rewrite an unchanged key — and that only shows up against real ciphertext,
which is randomised per encryption and so cannot be compared byte for byte.
"""

import pytest
from cryptography.fernet import Fernet

from api.services.configuration import platform_credential_seed as seed
from api.services.configuration import platform_credentials as creds

TEST_SECRET = Fernet.generate_key().decode()
A_KEY = "sk-proj-abcdefghijklmnopqrstuvwxyz0042"
ANOTHER_KEY = "sk-proj-zyxwvutsrqponmlkjihgfedcba9911"


@pytest.fixture
def configured_secret(monkeypatch):
    """Patch the constant rather than the environment, so these pass on a
    machine that has no platform secret configured."""
    monkeypatch.setattr(creds, "PLATFORM_CREDENTIAL_SECRET", TEST_SECRET)


@pytest.fixture
def no_platform_key_vars(monkeypatch):
    """The process environment may carry real ones; start from none."""
    for name in list(seed.os.environ):
        if name.startswith(seed.ENV_PREFIX):
            monkeypatch.delenv(name, raising=False)


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


@pytest.mark.asyncio
class TestSeedingWritesToTheVault:
    """Against a real database, because the behaviour that matters most —
    re-seeding an unchanged key changes nothing — cannot be observed from the
    ciphertext. Fernet randomises every encryption, so two encryptions of the
    same key never compare equal and only the decrypted value tells the truth.
    """

    async def test_a_seeded_key_is_resolvable_afterwards(
        self,
        db_session,
        async_session,
        configured_secret,
        no_platform_key_vars,
        monkeypatch,
    ):
        monkeypatch.setenv("PLATFORM_KEY_LLM_GOOGLE", A_KEY)

        result = await seed.seed_from_environment()
        assert ("llm", "google") in result.stored

        # The pipeline can authenticate with what was seeded.
        resolved = await creds.resolve_api_key(
            async_session, component="llm", provider="google"
        )
        assert resolved == A_KEY

    async def test_reseeding_an_unchanged_key_is_a_no_op(
        self,
        db_session,
        async_session,
        configured_secret,
        no_platform_key_vars,
        monkeypatch,
    ):
        monkeypatch.setenv("PLATFORM_KEY_LLM_GOOGLE", A_KEY)

        first = await seed.seed_from_environment()
        assert ("llm", "google") in first.stored

        # A restart. Nothing changed, so nothing should be written — otherwise
        # every bounce rewrites the row and resets who set it.
        second = await seed.seed_from_environment()
        assert ("llm", "google") in second.unchanged
        assert second.stored == []

    async def test_a_changed_key_is_rotated_in_place(
        self,
        db_session,
        async_session,
        configured_secret,
        no_platform_key_vars,
        monkeypatch,
    ):
        monkeypatch.setenv("PLATFORM_KEY_LLM_GOOGLE", A_KEY)
        await seed.seed_from_environment()

        monkeypatch.setenv("PLATFORM_KEY_LLM_GOOGLE", ANOTHER_KEY)
        second = await seed.seed_from_environment()

        assert ("llm", "google") in second.stored
        resolved = await creds.resolve_api_key(
            async_session, component="llm", provider="google"
        )
        assert resolved == ANOTHER_KEY

        # Rotated, not appended: one row per component and provider.
        listed = await creds.list_credentials(async_session)
        google = [c for c in listed if c.provider == "google" and c.component == "llm"]
        assert len(google) == 1

    async def test_it_is_labelled_as_coming_from_the_environment(
        self,
        db_session,
        async_session,
        configured_secret,
        no_platform_key_vars,
        monkeypatch,
    ):
        # So an operator looking at the staff screen can tell which keys they
        # cannot simply delete — the next restart would put them back.
        monkeypatch.setenv("PLATFORM_KEY_TTS_SARVAM", A_KEY)
        await seed.seed_from_environment()

        listed = await creds.list_credentials(async_session)
        sarvam = next(c for c in listed if c.provider == "sarvam")
        assert sarvam.label == seed.SEED_LABEL

    async def test_seeding_lights_up_the_matching_managed_slot(
        self,
        db_session,
        async_session,
        configured_secret,
        no_platform_key_vars,
        monkeypatch,
    ):
        """The whole point, end to end: a key in the environment turns a
        'coming soon' slot into an offered one, with nothing else touched."""
        from api.services.configuration import managed_resolution, managed_tiers

        before = await managed_resolution.managed_availability(async_session)
        assert before["tts"] is False

        upstream = managed_tiers.resolve("tts", "default")
        monkeypatch.setenv(f"PLATFORM_KEY_TTS_{upstream.provider.upper()}", A_KEY)
        await seed.seed_from_environment()

        after = await managed_resolution.managed_availability(async_session)
        assert after["tts"] is True

    async def test_an_unknown_component_is_skipped_not_fatal(
        self,
        db_session,
        async_session,
        configured_secret,
        no_platform_key_vars,
        monkeypatch,
    ):
        # Telephony credentials live on telephony configurations, so the vault
        # refuses them. A typo must not stop the other keys installing.
        monkeypatch.setenv("PLATFORM_KEY_TELEPHONY_TWILIO", A_KEY)
        monkeypatch.setenv("PLATFORM_KEY_LLM_GOOGLE", A_KEY)

        result = await seed.seed_from_environment()
        assert ("telephony", "twilio") in result.skipped
        assert ("llm", "google") in result.stored
