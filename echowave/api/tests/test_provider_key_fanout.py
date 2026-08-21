"""One vendor account, one key, however many components it serves.

Sarvam and OpenAI each do speech-to-text, the language model and synthesis. A
customer holds one account with them and one key — so asking for it once per
component is asking them to do the registry's lookup by hand, three times, and
to notice when they get it wrong.

The fan-out is opt-in rather than automatic. The separate-key case is real: an
account can hold two keys with one vendor on separate billing, and quietly
overwriting the other component's key would cost more than the typing saved.
"""

import pytest
from cryptography.fernet import Fernet

from api.services.configuration.registry import components_for_provider, known_providers


class TestWhichComponentsAVendorServes:
    def test_a_vendor_that_does_everything_reports_everything(self):
        assert components_for_provider("sarvam") == ("stt", "llm", "tts")
        assert components_for_provider("openai") == ("stt", "llm", "tts")

    def test_the_order_is_the_order_a_turn_flows_through(self):
        """Not alphabetical. The offer is rendered from this list, and
        "transcriber and voice" reads as a pipeline where "llm, stt, tts" reads
        as a dump of internal keys."""
        assert components_for_provider("openai") == ("stt", "llm", "tts")

    def test_a_two_component_vendor_reports_exactly_those_two(self):
        assert components_for_provider("deepgram") == ("stt", "tts")

    def test_a_single_component_vendor_reports_only_that_one(self):
        """The UI shows the offer only when this is non-trivial, so a vendor
        serving one slot must not produce a checkbox offering nothing."""
        assert components_for_provider("assemblyai") == ("stt",)
        assert components_for_provider("groq") == ("llm",)

    def test_an_unknown_vendor_is_empty_rather_than_an_error(self):
        """The caller is usually validating user input, where "serves nothing"
        is the answer and an exception would be a 500."""
        assert components_for_provider("not-a-vendor") == ()
        assert components_for_provider("") == ()
        assert components_for_provider(None) == ()

    def test_the_lookup_is_case_and_space_insensitive(self):
        """Providers arrive from a request body, not from an enum."""
        assert components_for_provider("  Sarvam ") == ("stt", "llm", "tts")


@pytest.fixture
def credential_secret(monkeypatch):
    """A throwaway encryption key, so storing one is possible in a test.

    Generated per run rather than committed: a fixed key in the repository is
    one that eventually protects something real.
    """
    import api.services.configuration.organization_credentials as creds

    monkeypatch.setattr(
        creds, "PLATFORM_CREDENTIAL_SECRET", Fernet.generate_key().decode()
    )


@pytest.mark.asyncio
class TestStoringTheKey:
    async def test_one_key_reaches_every_component_the_vendor_serves(
        self, db_session, async_session, credential_secret
    ):
        from api.db.models import OrganizationModel, UserModel
        from api.services.configuration import organization_credentials as creds

        org = OrganizationModel(provider_id="org-fanout", quota_decibyl_tokens=0)
        user = UserModel(provider_id="user-fanout")
        async_session.add_all([org, user])
        await async_session.flush()

        for component in components_for_provider("sarvam"):
            await creds.set_credential(
                async_session,
                organization_id=org.id,
                actor_user_id=user.id,
                component=component,
                provider="sarvam",
                api_key="sarvam-key-000000",
            )
        await async_session.flush()

        held = await creds.available_providers(async_session, organization_id=org.id)
        for component in ("stt", "llm", "tts"):
            assert "sarvam" in held.get(component, set()), component

    async def test_rotating_one_component_leaves_the_others_alone(
        self, db_session, async_session, credential_secret
    ):
        """Storing per component is what makes the separate-key case possible.
        The fan-out is a convenience on top of that, not a replacement for it."""
        from api.db.models import OrganizationModel, UserModel
        from api.services.configuration import organization_credentials as creds

        org = OrganizationModel(provider_id="org-rotate", quota_decibyl_tokens=0)
        user = UserModel(provider_id="user-rotate")
        async_session.add_all([org, user])
        await async_session.flush()

        for component in ("stt", "tts"):
            await creds.set_credential(
                async_session,
                organization_id=org.id,
                actor_user_id=user.id,
                component=component,
                provider="sarvam",
                api_key="original-key-0000",
            )
        await async_session.flush()

        await creds.set_credential(
            async_session,
            organization_id=org.id,
            actor_user_id=user.id,
            component="stt",
            provider="sarvam",
            api_key="rotated-key-00000",
        )
        await async_session.flush()

        held = await creds.available_providers(async_session, organization_id=org.id)
        assert "sarvam" in held.get("stt", set())
        assert "sarvam" in held.get("tts", set())


class TestTheProviderListLeadsTheKeyForm:
    """What ``known_providers`` answers, so the add-key screen can lead with
    the vendor rather than with "which component" — the question that made
    adding one Sarvam key feel like adding three."""

    def test_every_answer_agrees_with_components_for_provider(self):
        """One source of truth. If these two ever disagreed, the form would
        offer "apply to all" for a set of components the fan-out itself does
        not believe the vendor serves."""
        providers = known_providers()
        assert providers
        for provider, components in providers.items():
            assert components_for_provider(provider) == components

    def test_the_managed_sentinel_is_not_a_vendor(self):
        """Nobody pastes a key in under "decibyl" — it names the managed tier,
        not an account anyone holds. ``organization_credentials._normalise``
        refuses it on the customer side for the same reason."""
        assert "decibyl" not in known_providers()

    def test_a_multi_component_vendor_is_there_to_be_led_with(self):
        providers = known_providers()
        assert providers["sarvam"] == ("stt", "llm", "tts")
        assert providers["deepgram"] == ("stt", "tts")

    def test_names_are_plain_strings_not_enum_reprs(self):
        """The screen renders these directly. A key rendered as
        "ServiceProviders.SARVAM" instead of "sarvam" is this bug, seen once
        already when the registry's own enum members leaked into a response."""
        for provider in known_providers():
            assert provider == provider.lower()
            assert "ServiceProviders" not in provider
