"""A key that cannot reach the vendor is refused when it is pasted.

Every vendor key is ASCII and travels in an HTTP header, which carries nothing
else. Before this, ``set_credential`` checked only that the value was eight
characters long, so a non-ASCII one stored happily and then killed every call
on that provider with a ``UnicodeEncodeError`` raised from inside httpx — a 500
with nothing a reader could act on, arriving whenever somebody next used the
feature rather than at the screen where the mistake was made.

The case that actually happens is the **masked** key. A vendor shows the real
value once at creation and a masked form ever after — ``sk-proj-••••••••`` —
and that form has the right length, the right prefix, and a copy button beside
it. It was pasted into a live deployment's LLM slot; the stt and tts slots on
the same vendor had the real key, so managed speech worked and the agent
builder answered "The assistant could not reply" for twenty minutes.
"""

from __future__ import annotations

import pytest
from cryptography.fernet import Fernet

from api.enums import CostComponent
from api.services.configuration import platform_credentials

REAL = "sk-proj-" + "a1B2c3D4" * 19  # 160 chars, the shape of a project key

TEST_SECRET = Fernet.generate_key().decode()


@pytest.fixture
def configured_secret(monkeypatch):
    """Patch the constant rather than the environment, so these pass on a
    machine that has no platform secret configured — the same approach
    test_platform_credential_seed.py takes."""
    monkeypatch.setattr(platform_credentials, "PLATFORM_CREDENTIAL_SECRET", TEST_SECRET)


class TestTheMaskedKey:
    async def test_the_masked_key_is_refused(self, async_session):
        """The exact value that was pasted: prefix, then bullets."""
        masked = "sk-proj-" + "•" * 152

        with pytest.raises(platform_credentials.PlatformCredentialError) as caught:
            await platform_credentials.set_credential(
                async_session,
                actor_user_id=None,
                component=CostComponent.LLM,
                provider="openai",
                api_key=masked,
            )
        # The message has to name what they pasted, or they paste it again.
        assert "masked" in str(caught.value)

    @pytest.mark.parametrize("character", ["•", "·", "∙", "●", "*"])
    async def test_every_masking_character_is_caught(self, async_session, character):
        with pytest.raises(platform_credentials.PlatformCredentialError):
            await platform_credentials.set_credential(
                async_session,
                actor_user_id=None,
                component=CostComponent.LLM,
                provider="openai",
                api_key="sk-proj-" + character * 40,
            )


class TestOtherUnusableKeys:
    async def test_a_key_rewritten_by_a_copy_is_refused(self, async_session):
        """An en dash where a hyphen belongs — what a document does to text."""
        with pytest.raises(platform_credentials.PlatformCredentialError) as caught:
            await platform_credentials.set_credential(
                async_session,
                actor_user_id=None,
                component=CostComponent.LLM,
                provider="openai",
                api_key="sk–proj–" + "a1B2c3D4" * 8,
            )
        assert "cannot hold" in str(caught.value)

    async def test_a_short_value_is_still_refused(self, async_session):
        with pytest.raises(platform_credentials.PlatformCredentialError):
            await platform_credentials.set_credential(
                async_session,
                actor_user_id=None,
                component=CostComponent.LLM,
                provider="openai",
                api_key="sk-",
            )


class TestARealKeyStillStores:
    async def test_the_real_key_is_accepted_and_comes_back(
        self, async_session, configured_secret
    ):
        """The guard must not cost anyone a working key."""
        await platform_credentials.set_credential(
            async_session,
            actor_user_id=None,
            component=CostComponent.LLM,
            provider="openai",
            api_key=REAL,
        )
        await async_session.flush()

        resolved = await platform_credentials.resolve_api_key(
            async_session, component=CostComponent.LLM, provider="openai"
        )
        assert resolved == REAL
        # The property the whole guard exists to protect: it can be put in a
        # header without raising.
        assert f"Bearer {resolved}".encode("ascii")

    async def test_a_lone_asterisk_is_not_treated_as_masking(
        self, async_session, configured_secret
    ):
        """Three in a row is masking; one is just a character. Erring the other
        way would refuse a legitimate key with no way round it."""
        key = "sk-proj-a*b" + "c1D2e3F4" * 8
        await platform_credentials.set_credential(
            async_session,
            actor_user_id=None,
            component=CostComponent.LLM,
            provider="anthropic",
            api_key=key,
        )
        await async_session.flush()
        assert (
            await platform_credentials.resolve_api_key(
                async_session, component=CostComponent.LLM, provider="anthropic"
            )
            == key
        )


class TestTheSeedCannotClobberAGoodKey:
    """The failure this guard was written for, end to end.

    A deployment had a masked key in ``PLATFORM_KEY_LLM_OPENAI``. The seed runs
    at every boot and overwrites any stored key that differs from the
    environment's, so the real key typed into the staff screen was replaced by
    the masked one on the next restart — silently, and repeatedly. The operator
    had pasted a working key and watched the feature stay broken.

    With the key refused at ``set_credential``, the seed's own error handling
    does the right thing: it logs the component and provider, skips it, and
    leaves the good key in place.
    """

    async def test_a_masked_environment_key_is_skipped_not_stored(
        self, async_session, configured_secret, monkeypatch
    ):
        from api.services.configuration import platform_credential_seed as seed

        # The good key, as if typed into the staff screen.
        await platform_credentials.set_credential(
            async_session,
            actor_user_id=None,
            component=CostComponent.LLM,
            provider="openai",
            api_key=REAL,
        )
        await async_session.flush()

        monkeypatch.setattr(
            seed, "_declared", lambda: [("llm", "openai", "sk-proj-" + "•" * 152)]
        )
        monkeypatch.setattr(seed.db_client, "async_session", _reuse(async_session))

        result = await seed.seed_from_environment()

        assert ("llm", "openai") in result.skipped
        assert ("llm", "openai") not in result.stored
        # The point of the whole thing: the working key is still there.
        assert (
            await platform_credentials.resolve_api_key(
                async_session, component=CostComponent.LLM, provider="openai"
            )
            == REAL
        )


def _reuse(session):
    """Hand the seed the test's session instead of opening its own."""
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _ctx():
        yield session

    return lambda: _ctx()
