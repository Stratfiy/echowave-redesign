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

from api.services.configuration.registry import components_for_provider


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
