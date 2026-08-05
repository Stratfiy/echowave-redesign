"""The customer's own provider keys — the BYOK vault.

Two things are being tested, and only one of them is the happy path.

The first is that the vault does its job: store, rotate, deactivate, delete,
and hand the plaintext to the pipeline and to nobody else.

The second is **tenant isolation**, and it carries more weight than everything
else here combined. A provider API key is the most damaging row in the schema
to serve to the wrong account: it is a live credential with someone else's
money behind it. Every function in the service takes ``organization_id`` as a
required argument for that reason, and these tests exist to make sure it is
actually used as a filter rather than merely accepted.
"""

import pytest
from cryptography.fernet import Fernet

from api.db.models import OrganizationModel
from api.enums import CostComponent
from api.services.configuration import organization_credentials as creds

TEST_SECRET = Fernet.generate_key().decode()


@pytest.fixture(autouse=True)
def encryption(monkeypatch):
    """A real Fernet key, generated per run.

    Deliberately not a fixed literal: a committed key that also works in a
    deployment is how a test fixture becomes a production secret, which this
    repo has already had to fix once.
    """
    monkeypatch.setattr(creds, "PLATFORM_CREDENTIAL_SECRET", TEST_SECRET)


async def _org(async_session, slug: str) -> OrganizationModel:
    org = OrganizationModel(provider_id=f"org-{slug}", quota_decibyl_tokens=0)
    async_session.add(org)
    await async_session.flush()
    return org


@pytest.mark.asyncio
class TestStoringAKey:
    async def test_a_stored_key_can_be_resolved_for_the_pipeline(self, async_session):
        org = await _org(async_session, "store")
        await creds.set_credential(
            async_session,
            organization_id=org.id,
            actor_user_id=None,
            component=CostComponent.LLM,
            provider="openai",
            api_key="sk-test-abcd1234",
        )

        resolved = await creds.resolve_api_key(
            async_session,
            organization_id=org.id,
            component=CostComponent.LLM,
            provider="openai",
        )
        assert resolved == "sk-test-abcd1234"

    async def test_the_key_is_not_stored_in_plaintext(self, async_session):
        """The whole reason keys left the configuration JSON."""
        org = await _org(async_session, "cipher")
        await creds.set_credential(
            async_session,
            organization_id=org.id,
            actor_user_id=None,
            component=CostComponent.LLM,
            provider="openai",
            api_key="sk-test-abcd1234",
        )

        stored = await creds.list_credentials(async_session, organization_id=org.id)
        from sqlalchemy import select

        from api.db.models import OrganizationProviderCredentialModel

        row = await async_session.scalar(
            select(OrganizationProviderCredentialModel).where(
                OrganizationProviderCredentialModel.organization_id == org.id
            )
        )
        assert "sk-test-abcd1234" not in row.encrypted_key
        assert stored[0].masked_key == "••••1234"

    async def test_the_plaintext_is_never_in_the_listing(self, async_session):
        org = await _org(async_session, "masked")
        await creds.set_credential(
            async_session,
            organization_id=org.id,
            actor_user_id=None,
            component=CostComponent.TTS,
            provider="elevenlabs",
            api_key="el-secret-value-9876",
        )

        listed = await creds.list_credentials(async_session, organization_id=org.id)
        assert all("secret" not in str(vars(c)) for c in listed)

    async def test_rotating_replaces_the_key_in_place(self, async_session):
        """One row per provider. The old key is being revoked at the vendor, so
        keeping it would only widen what a database leak exposes."""
        org = await _org(async_session, "rotate")
        for key in ("sk-first-00001111", "sk-second-22223333"):
            await creds.set_credential(
                async_session,
                organization_id=org.id,
                actor_user_id=None,
                component=CostComponent.LLM,
                provider="openai",
                api_key=key,
            )

        listed = await creds.list_credentials(async_session, organization_id=org.id)
        assert len(listed) == 1
        resolved = await creds.resolve_api_key(
            async_session,
            organization_id=org.id,
            component=CostComponent.LLM,
            provider="openai",
        )
        assert resolved == "sk-second-22223333"

    async def test_the_same_vendor_can_hold_separate_keys_per_component(
        self, async_session
    ):
        """Sarvam does both STT and TTS, and an account may well bill them to
        separate vendor accounts."""
        org = await _org(async_session, "two-components")
        await creds.set_credential(
            async_session,
            organization_id=org.id,
            actor_user_id=None,
            component=CostComponent.STT,
            provider="sarvam",
            api_key="sarvam-stt-1111",
        )
        await creds.set_credential(
            async_session,
            organization_id=org.id,
            actor_user_id=None,
            component=CostComponent.TTS,
            provider="sarvam",
            api_key="sarvam-tts-2222",
        )

        stt = await creds.resolve_api_key(
            async_session,
            organization_id=org.id,
            component=CostComponent.STT,
            provider="sarvam",
        )
        tts = await creds.resolve_api_key(
            async_session,
            organization_id=org.id,
            component=CostComponent.TTS,
            provider="sarvam",
        )
        assert (stt, tts) == ("sarvam-stt-1111", "sarvam-tts-2222")


@pytest.mark.asyncio
class TestWhatCannotBeStored:
    async def test_a_key_for_decibyl_is_refused(self, async_session):
        """ "decibyl" is how a slot says *managed* — it names our key, not one
        the customer holds. A BYOK credential under that name would shadow the
        managed path and make the two sources indistinguishable at resolution."""
        org = await _org(async_session, "no-decibyl")
        with pytest.raises(creds.OrganizationCredentialError, match="managed"):
            await creds.set_credential(
                async_session,
                organization_id=org.id,
                actor_user_id=None,
                component=CostComponent.LLM,
                provider="decibyl",
                api_key="does-not-matter",
            )

    async def test_a_telephony_key_is_refused(self, async_session):
        """Carrier credentials live on telephony_configurations, which models
        the per-account carrier relationship and the KYC attached to it."""
        org = await _org(async_session, "no-telephony")
        with pytest.raises(creds.OrganizationCredentialError, match="telephony"):
            await creds.set_credential(
                async_session,
                organization_id=org.id,
                actor_user_id=None,
                component=CostComponent.TELEPHONY,
                provider="plivo",
                api_key="plivo-key-1234",
            )

    async def test_a_truncated_paste_is_refused(self, async_session):
        """Catches the commonest real mistake: pasting a visible prefix from a
        vendor dashboard rather than the whole value."""
        org = await _org(async_session, "short")
        with pytest.raises(creds.OrganizationCredentialError, match="whole value"):
            await creds.set_credential(
                async_session,
                organization_id=org.id,
                actor_user_id=None,
                component=CostComponent.LLM,
                provider="openai",
                api_key="sk-1",
            )

    async def test_storing_without_an_encryption_secret_raises(
        self, async_session, monkeypatch
    ):
        """A deployment that forgot to configure the secret must fail loudly.
        A default would mean every key encrypted under a published value."""
        monkeypatch.setattr(creds, "PLATFORM_CREDENTIAL_SECRET", "")
        org = await _org(async_session, "no-secret")
        with pytest.raises(creds.OrganizationCredentialError, match="not set"):
            await creds.set_credential(
                async_session,
                organization_id=org.id,
                actor_user_id=None,
                component=CostComponent.LLM,
                provider="openai",
                api_key="sk-test-abcd1234",
            )


@pytest.mark.asyncio
class TestTenantIsolation:
    """The reason organization_id is a required argument everywhere."""

    async def test_one_account_cannot_resolve_anothers_key(self, async_session):
        mine = await _org(async_session, "mine")
        theirs = await _org(async_session, "theirs")
        await creds.set_credential(
            async_session,
            organization_id=theirs.id,
            actor_user_id=None,
            component=CostComponent.LLM,
            provider="openai",
            api_key="sk-theirs-88889999",
        )

        resolved = await creds.resolve_api_key(
            async_session,
            organization_id=mine.id,
            component=CostComponent.LLM,
            provider="openai",
        )
        assert resolved is None

    async def test_one_account_cannot_list_anothers_keys(self, async_session):
        mine = await _org(async_session, "list-mine")
        theirs = await _org(async_session, "list-theirs")
        await creds.set_credential(
            async_session,
            organization_id=theirs.id,
            actor_user_id=None,
            component=CostComponent.LLM,
            provider="openai",
            api_key="sk-theirs-88889999",
        )

        assert (
            await creds.list_credentials(async_session, organization_id=mine.id) == []
        )

    async def test_one_account_cannot_delete_anothers_key(self, async_session):
        mine = await _org(async_session, "del-mine")
        theirs = await _org(async_session, "del-theirs")
        await creds.set_credential(
            async_session,
            organization_id=theirs.id,
            actor_user_id=None,
            component=CostComponent.LLM,
            provider="openai",
            api_key="sk-theirs-88889999",
        )

        with pytest.raises(creds.OrganizationCredentialError):
            await creds.delete_credential(
                async_session,
                organization_id=mine.id,
                component=CostComponent.LLM,
                provider="openai",
            )

        # And theirs survived the attempt.
        assert (
            await creds.resolve_api_key(
                async_session,
                organization_id=theirs.id,
                component=CostComponent.LLM,
                provider="openai",
            )
            == "sk-theirs-88889999"
        )

    async def test_two_accounts_hold_independent_keys_for_one_provider(
        self, async_session
    ):
        """The ordinary case, and the one the unique constraint has to allow:
        the same vendor, the same component, two different customers."""
        first = await _org(async_session, "first")
        second = await _org(async_session, "second")
        await creds.set_credential(
            async_session,
            organization_id=first.id,
            actor_user_id=None,
            component=CostComponent.LLM,
            provider="openai",
            api_key="sk-first-1111",
        )
        await creds.set_credential(
            async_session,
            organization_id=second.id,
            actor_user_id=None,
            component=CostComponent.LLM,
            provider="openai",
            api_key="sk-second-2222",
        )

        assert (
            await creds.resolve_api_key(
                async_session,
                organization_id=first.id,
                component=CostComponent.LLM,
                provider="openai",
            )
            == "sk-first-1111"
        )
        assert (
            await creds.resolve_api_key(
                async_session,
                organization_id=second.id,
                component=CostComponent.LLM,
                provider="openai",
            )
            == "sk-second-2222"
        )


@pytest.mark.asyncio
class TestTakingAProviderOutOfService:
    async def test_a_deactivated_key_does_not_resolve(self, async_session):
        """Used while rotating at the vendor: agents pointing at it fail over to
        managed rather than authenticating with a key being revoked."""
        org = await _org(async_session, "deactivate")
        await creds.set_credential(
            async_session,
            organization_id=org.id,
            actor_user_id=None,
            component=CostComponent.LLM,
            provider="openai",
            api_key="sk-test-abcd1234",
        )
        await creds.set_active(
            async_session,
            organization_id=org.id,
            component=CostComponent.LLM,
            provider="openai",
            is_active=False,
        )

        assert (
            await creds.resolve_api_key(
                async_session,
                organization_id=org.id,
                component=CostComponent.LLM,
                provider="openai",
            )
            is None
        )

    async def test_a_deactivated_key_is_still_listed(self, async_session):
        """Deactivating is not deleting — the screen has to show it so it can
        be switched back on."""
        org = await _org(async_session, "still-listed")
        await creds.set_credential(
            async_session,
            organization_id=org.id,
            actor_user_id=None,
            component=CostComponent.LLM,
            provider="openai",
            api_key="sk-test-abcd1234",
        )
        await creds.set_active(
            async_session,
            organization_id=org.id,
            component=CostComponent.LLM,
            provider="openai",
            is_active=False,
        )

        listed = await creds.list_credentials(async_session, organization_id=org.id)
        assert len(listed) == 1 and listed[0].is_active is False

    async def test_re_entering_a_key_reactivates_it(self, async_session):
        """Pasting a key is an unambiguous statement that you want it used."""
        org = await _org(async_session, "reactivate")
        await creds.set_credential(
            async_session,
            organization_id=org.id,
            actor_user_id=None,
            component=CostComponent.LLM,
            provider="openai",
            api_key="sk-test-abcd1234",
        )
        await creds.set_active(
            async_session,
            organization_id=org.id,
            component=CostComponent.LLM,
            provider="openai",
            is_active=False,
        )
        await creds.set_credential(
            async_session,
            organization_id=org.id,
            actor_user_id=None,
            component=CostComponent.LLM,
            provider="openai",
            api_key="sk-new-55556666",
        )

        assert (
            await creds.resolve_api_key(
                async_session,
                organization_id=org.id,
                component=CostComponent.LLM,
                provider="openai",
            )
            == "sk-new-55556666"
        )


@pytest.mark.asyncio
class TestWhatThePickerIsOffered:
    async def test_available_providers_lists_only_what_is_usable(self, async_session):
        """A slot should only offer "my own key" for a provider the account
        actually holds a working key for. Offering it and failing at dial time
        is the version of this that wastes a call."""
        org = await _org(async_session, "available")
        await creds.set_credential(
            async_session,
            organization_id=org.id,
            actor_user_id=None,
            component=CostComponent.LLM,
            provider="openai",
            api_key="sk-test-abcd1234",
        )
        await creds.set_credential(
            async_session,
            organization_id=org.id,
            actor_user_id=None,
            component=CostComponent.STT,
            provider="deepgram",
            api_key="dg-test-abcd1234",
        )
        await creds.set_credential(
            async_session,
            organization_id=org.id,
            actor_user_id=None,
            component=CostComponent.TTS,
            provider="elevenlabs",
            api_key="el-test-abcd",
        )
        await creds.set_active(
            async_session,
            organization_id=org.id,
            component=CostComponent.TTS,
            provider="elevenlabs",
            is_active=False,
        )

        available = await creds.available_providers(
            async_session, organization_id=org.id
        )
        assert available == {"llm": ["openai"], "stt": ["deepgram"]}

    async def test_a_key_that_will_not_decrypt_resolves_to_none(
        self, async_session, monkeypatch
    ):
        """The secret was rotated without the keys being re-entered. Returning
        None rather than raising keeps the failure to the one slot, and the
        error log is what explains it."""
        org = await _org(async_session, "rotated-secret")
        await creds.set_credential(
            async_session,
            organization_id=org.id,
            actor_user_id=None,
            component=CostComponent.LLM,
            provider="openai",
            api_key="sk-test-abcd1234",
        )

        monkeypatch.setattr(
            creds, "PLATFORM_CREDENTIAL_SECRET", Fernet.generate_key().decode()
        )
        assert (
            await creds.resolve_api_key(
                async_session,
                organization_id=org.id,
                component=CostComponent.LLM,
                provider="openai",
            )
            is None
        )


class TestAMaskedKeyIsNotAKey:
    """A masked value must not stop the vault fallback from firing.

    Responses mask stored keys before they leave the API, and both save paths
    refuse to persist a masked value — so this should never happen. If it ever
    does, treating the mask as a real key is the worst outcome available: the
    vault is never consulted and the call reaches the vendor with a string of
    asterisks, failing on their authentication error three layers from anything
    that explains it.
    """

    def test_a_masked_string_does_not_count_as_a_key(self):
        from types import SimpleNamespace

        from api.services.configuration.byok_resolution import _has_own_key

        assert not _has_own_key(SimpleNamespace(api_key="****************cdef"))

    def test_a_real_key_still_counts(self):
        from types import SimpleNamespace

        from api.services.configuration.byok_resolution import _has_own_key

        assert _has_own_key(SimpleNamespace(api_key="sk-real-key-value"))

    def test_a_masked_entry_in_a_key_list_does_not_count(self):
        """Providers that rotate several keys carry a list. One masked entry
        should not make the whole section look authenticated."""
        from types import SimpleNamespace

        from api.services.configuration.byok_resolution import _has_own_key

        assert not _has_own_key(SimpleNamespace(api_key=["****abcd", "  "]))
        assert _has_own_key(SimpleNamespace(api_key=["****abcd", "sk-real"]))

    def test_empty_and_missing_are_not_keys(self):
        from types import SimpleNamespace

        from api.services.configuration.byok_resolution import _has_own_key

        assert not _has_own_key(SimpleNamespace(api_key=""))
        assert not _has_own_key(SimpleNamespace(api_key=None))
        assert not _has_own_key(SimpleNamespace())
