"""Decibyl's own provider keys.

These buy inference capacity billed to us for every customer at once, so the
tests are mostly about what must *not* happen: the plaintext must never come
back out of a read path, and a misconfigured deployment must fail loudly rather
than storing keys under a secret published in the source.
"""

import pytest
from cryptography.fernet import Fernet

from api.db.models import PlatformProviderCredentialModel, UserModel
from api.enums import CostComponent
from api.services.configuration import platform_credentials as creds

# A real Fernet key, generated once. Tests patch the constant rather than
# relying on the environment, so they pass on a machine with none configured.
TEST_SECRET = Fernet.generate_key().decode()
OPENAI_KEY = "sk-proj-abcdefghijklmnopqrstuvwxyz0042"


@pytest.fixture(autouse=True)
def configured_secret(monkeypatch):
    monkeypatch.setattr(creds, "PLATFORM_CREDENTIAL_SECRET", TEST_SECRET)


async def _staff(session) -> UserModel:
    user = UserModel(provider_id="staff-platform-creds")
    session.add(user)
    await session.flush()
    return user


@pytest.mark.asyncio
class TestKeysAreNotReadable:
    async def test_a_stored_key_never_appears_in_a_listing(self, async_session):
        """The property that matters most. A read path here would turn one
        compromised staff session into every provider key on the platform."""
        staff = await _staff(async_session)
        await creds.set_credential(
            async_session,
            actor_user_id=staff.id,
            component=CostComponent.LLM,
            provider="openai",
            api_key=OPENAI_KEY,
        )

        listed = await creds.list_credentials(async_session)

        assert len(listed) == 1
        assert listed[0].masked_key == "••••0042"
        # The plaintext appears nowhere in what a screen would render.
        assert OPENAI_KEY not in repr(listed)

    async def test_it_is_not_stored_in_plaintext(self, async_session):
        staff = await _staff(async_session)
        await creds.set_credential(
            async_session,
            actor_user_id=staff.id,
            component=CostComponent.LLM,
            provider="openai",
            api_key=OPENAI_KEY,
        )

        from sqlalchemy import select

        row = await async_session.scalar(select(PlatformProviderCredentialModel))
        assert OPENAI_KEY not in row.encrypted_key
        # And it really is the same key, encrypted — not a different one.
        assert row.key_last_four == "0042"

    async def test_the_pipeline_can_still_get_the_real_key(self, async_session):
        """Encryption is only useful if the one legitimate reader works."""
        staff = await _staff(async_session)
        await creds.set_credential(
            async_session,
            actor_user_id=staff.id,
            component=CostComponent.STT,
            provider="deepgram",
            api_key="dg-secret-key-9876",
        )

        resolved = await creds.resolve_api_key(
            async_session, component=CostComponent.STT, provider="deepgram"
        )
        assert resolved == "dg-secret-key-9876"


@pytest.mark.asyncio
class TestMisconfigurationFailsLoudly:
    async def test_storing_without_a_secret_is_refused(
        self, async_session, monkeypatch
    ):
        """A default secret would mean a deployment that forgot to configure one
        still appeared to work, with every key encrypted under a published
        value."""
        monkeypatch.setattr(creds, "PLATFORM_CREDENTIAL_SECRET", None)
        staff = await _staff(async_session)

        with pytest.raises(creds.PlatformCredentialError, match="not set"):
            await creds.set_credential(
                async_session,
                actor_user_id=staff.id,
                component=CostComponent.LLM,
                provider="openai",
                api_key=OPENAI_KEY,
            )

    async def test_a_malformed_secret_is_refused(self, async_session, monkeypatch):
        monkeypatch.setattr(creds, "PLATFORM_CREDENTIAL_SECRET", "not-a-fernet-key")
        staff = await _staff(async_session)

        with pytest.raises(creds.PlatformCredentialError, match="valid Fernet"):
            await creds.set_credential(
                async_session,
                actor_user_id=staff.id,
                component=CostComponent.LLM,
                provider="openai",
                api_key=OPENAI_KEY,
            )

    async def test_a_rotated_secret_reports_rather_than_returning_rubbish(
        self, async_session, monkeypatch
    ):
        """Rotating the secret without re-entering the keys breaks every managed
        call. Returning None sends the caller down the unmanaged path; returning
        a corrupted string would send a broken key to OpenAI."""
        staff = await _staff(async_session)
        await creds.set_credential(
            async_session,
            actor_user_id=staff.id,
            component=CostComponent.LLM,
            provider="openai",
            api_key=OPENAI_KEY,
        )

        monkeypatch.setattr(
            creds, "PLATFORM_CREDENTIAL_SECRET", Fernet.generate_key().decode()
        )

        assert (
            await creds.resolve_api_key(
                async_session, component=CostComponent.LLM, provider="openai"
            )
        ) is None

    async def test_the_screen_can_tell_whether_storage_works(self, monkeypatch):
        assert creds.encryption_is_configured() is True
        monkeypatch.setattr(creds, "PLATFORM_CREDENTIAL_SECRET", None)
        assert creds.encryption_is_configured() is False


@pytest.mark.asyncio
class TestValidation:
    async def test_telephony_keys_are_refused(self, async_session):
        """Carrier credentials belong on a telephony configuration, which already
        models per-account carrier accounts and the KYC attached to them."""
        staff = await _staff(async_session)
        with pytest.raises(creds.PlatformCredentialError, match="telephony"):
            await creds.set_credential(
                async_session,
                actor_user_id=staff.id,
                component=CostComponent.TELEPHONY,
                provider="plivo",
                api_key="whatever-key-here",
            )

    async def test_a_truncated_paste_is_refused(self, async_session):
        """A half-pasted key would be stored happily and then fail on a live
        call, far from whoever typed it."""
        staff = await _staff(async_session)
        with pytest.raises(creds.PlatformCredentialError, match="whole value"):
            await creds.set_credential(
                async_session,
                actor_user_id=staff.id,
                component=CostComponent.LLM,
                provider="openai",
                api_key="sk-",
            )

    async def test_the_provider_name_is_normalised(self, async_session):
        """So "OpenAI " typed into a form matches what the pipeline asks for."""
        staff = await _staff(async_session)
        await creds.set_credential(
            async_session,
            actor_user_id=staff.id,
            component=CostComponent.LLM,
            provider="  OpenAI ",
            api_key=OPENAI_KEY,
        )

        assert (
            await creds.resolve_api_key(
                async_session, component=CostComponent.LLM, provider="openai"
            )
        ) == OPENAI_KEY


@pytest.mark.asyncio
class TestLifecycle:
    async def test_rotating_replaces_rather_than_appending(self, async_session):
        """Unlike a rate, an old key has no history worth keeping — it is being
        revoked at the provider, and retaining it only widens a leak."""
        staff = await _staff(async_session)
        for key in ("sk-old-key-11112222", "sk-new-key-33334444"):
            await creds.set_credential(
                async_session,
                actor_user_id=staff.id,
                component=CostComponent.LLM,
                provider="openai",
                api_key=key,
            )

        listed = await creds.list_credentials(async_session)
        assert len(listed) == 1
        assert listed[0].masked_key == "••••4444"
        assert (
            await creds.resolve_api_key(
                async_session, component=CostComponent.LLM, provider="openai"
            )
        ) == "sk-new-key-33334444"

    async def test_one_vendor_can_serve_two_components_on_separate_keys(
        self, async_session
    ):
        """Sarvam does both STT and TTS, and an operator may want them on
        separate billing accounts."""
        staff = await _staff(async_session)
        await creds.set_credential(
            async_session,
            actor_user_id=staff.id,
            component=CostComponent.STT,
            provider="sarvam",
            api_key="sarvam-stt-key-0001",
        )
        await creds.set_credential(
            async_session,
            actor_user_id=staff.id,
            component=CostComponent.TTS,
            provider="sarvam",
            api_key="sarvam-tts-key-0002",
        )

        assert (
            await creds.resolve_api_key(
                async_session, component=CostComponent.STT, provider="sarvam"
            )
        ) == "sarvam-stt-key-0001"
        assert (
            await creds.resolve_api_key(
                async_session, component=CostComponent.TTS, provider="sarvam"
            )
        ) == "sarvam-tts-key-0002"

    async def test_deactivating_stops_it_being_used_but_keeps_it(self, async_session):
        staff = await _staff(async_session)
        await creds.set_credential(
            async_session,
            actor_user_id=staff.id,
            component=CostComponent.LLM,
            provider="openai",
            api_key=OPENAI_KEY,
        )

        await creds.set_active(
            async_session,
            component=CostComponent.LLM,
            provider="openai",
            is_active=False,
        )

        assert (
            await creds.resolve_api_key(
                async_session, component=CostComponent.LLM, provider="openai"
            )
        ) is None
        # Still listed, so it can be switched back on.
        assert len(await creds.list_credentials(async_session)) == 1

    async def test_a_provider_we_hold_no_key_for_resolves_to_none(self, async_session):
        """Not an error: the caller falls back to whatever the customer
        supplied, and most providers are unmanaged."""
        assert (
            await creds.resolve_api_key(
                async_session, component=CostComponent.LLM, provider="anthropic"
            )
        ) is None

    async def test_managed_providers_lists_what_can_be_offered(self, async_session):
        """Drives the customer model picker: only offer "managed" where we
        actually hold a working key."""
        staff = await _staff(async_session)
        await creds.set_credential(
            async_session,
            actor_user_id=staff.id,
            component=CostComponent.LLM,
            provider="openai",
            api_key=OPENAI_KEY,
        )
        await creds.set_credential(
            async_session,
            actor_user_id=staff.id,
            component=CostComponent.STT,
            provider="deepgram",
            api_key="dg-key-55556666",
        )
        await creds.set_active(
            async_session,
            component=CostComponent.STT,
            provider="deepgram",
            is_active=False,
        )

        offered = await creds.managed_providers(async_session)

        assert offered == {"llm": ["openai"]}


@pytest.mark.asyncio
class TestSpeechToSpeechSharesTheVendorKey:
    """A ``<vendor>_realtime`` provider is the same account as the vendor.

    OpenAI Realtime authenticates with your OpenAI key and Gemini Live with
    your Google key. Requiring a separate row would mean an admin pasting the
    same key twice, and the realtime tier breaking silently the day they
    rotated only one of them — the trap the embeddings component already avoids
    by sharing the LLM credential.

    Until this existed the managed speech-to-speech tier resolved no key at
    all: ``managed_resolution`` logs and *continues*, leaving the section as
    ``provider=decibyl``, so the call failed somewhere that read nothing like
    "nobody stored an openai_realtime key".
    """

    async def test_the_openai_key_serves_openai_realtime(self, async_session):
        staff = await _staff(async_session)
        await creds.set_credential(
            async_session,
            actor_user_id=staff.id,
            component=CostComponent.LLM,
            provider="openai",
            api_key=OPENAI_KEY,
        )

        resolved = await creds.resolve_api_key(
            async_session, component=CostComponent.LLM, provider="openai_realtime"
        )
        assert resolved == OPENAI_KEY

    async def test_an_exact_realtime_row_still_wins(self, async_session):
        """A deployment that already stored one under the realtime name keeps
        working, and keeps whatever value it put there."""
        staff = await _staff(async_session)
        await creds.set_credential(
            async_session,
            actor_user_id=staff.id,
            component=CostComponent.LLM,
            provider="openai",
            api_key=OPENAI_KEY,
        )
        await creds.set_credential(
            async_session,
            actor_user_id=staff.id,
            component=CostComponent.LLM,
            provider="openai_realtime",
            api_key="sk-a-different-realtime-key-0001",
        )

        resolved = await creds.resolve_api_key(
            async_session, component=CostComponent.LLM, provider="openai_realtime"
        )
        assert resolved == "sk-a-different-realtime-key-0001"

    async def test_only_the_realtime_suffix_is_stripped(self, async_session):
        """The dangerous generalisation. ``azure_speech`` is a different
        account from ``azure`` — a "everything before the underscore" rule
        would hand Azure Speech an Azure OpenAI key and fail at the vendor."""
        staff = await _staff(async_session)
        await creds.set_credential(
            async_session,
            actor_user_id=staff.id,
            component=CostComponent.LLM,
            provider="azure",
            api_key="azure-openai-key-1234",
        )

        assert (
            await creds.resolve_api_key(
                async_session, component=CostComponent.STT, provider="azure_speech"
            )
            is None
        )

    async def test_a_multi_word_vendor_falls_back_to_its_own_base(self, async_session):
        """``google_vertex_realtime`` must reach ``google_vertex``, not
        ``google`` — they are different projects with different keys."""
        staff = await _staff(async_session)
        await creds.set_credential(
            async_session,
            actor_user_id=staff.id,
            component=CostComponent.LLM,
            provider="google",
            api_key="plain-google-key-1111",
        )
        await creds.set_credential(
            async_session,
            actor_user_id=staff.id,
            component=CostComponent.LLM,
            provider="google_vertex",
            api_key="vertex-key-2222",
        )

        resolved = await creds.resolve_api_key(
            async_session,
            component=CostComponent.LLM,
            provider="google_vertex_realtime",
        )
        assert resolved == "vertex-key-2222"

    async def test_no_vendor_key_at_all_still_resolves_to_nothing(self, async_session):
        """The fallback must not invent a key from a different vendor."""
        staff = await _staff(async_session)
        await creds.set_credential(
            async_session,
            actor_user_id=staff.id,
            component=CostComponent.LLM,
            provider="google",
            api_key="plain-google-key-1111",
        )

        assert (
            await creds.resolve_api_key(
                async_session, component=CostComponent.LLM, provider="openai_realtime"
            )
            is None
        )
