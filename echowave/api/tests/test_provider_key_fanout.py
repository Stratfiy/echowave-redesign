"""One vendor account, one key, however many components it serves.

Sarvam and OpenAI each do speech-to-text, the language model and synthesis. A
customer holds one account with them and one key — so asking for it once per
component is asking them to do the registry's lookup by hand, three times, and
to notice when they get it wrong.

The fan-out is opt-in rather than automatic. The separate-key case is real: an
account can hold two keys with one vendor on separate billing, and quietly
overwriting the other component's key would cost more than the typing saved.
"""

from types import SimpleNamespace

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


class TestTheMapTheScreenRendersFrom:
    """The screens offer "also use this key for …" from a server-built map
    rather than a list in the page, so an offer cannot name a component the
    vendor does not serve — the way a hand-kept list drifts the first time a
    vendor gains one.

    The map is *groups*, not one list per vendor, because a vendor can
    authenticate two ways. Google serves all three components with two
    different secrets, and a flat list of three would offer to fan a Gemini key
    onto Cloud Speech.
    """

    def test_each_group_agrees_with_the_single_lookup(self):
        from api.services.configuration.registry import (
            components_sharing_credential,
            provider_credential_groups,
        )

        for provider, groups in provider_credential_groups().items():
            for group in groups:
                for component in group:
                    assert (
                        list(components_sharing_credential(provider, component))
                        == group
                    ), (provider, component)

    def test_a_one_secret_vendor_is_a_single_group(self):
        from api.services.configuration.registry import provider_credential_groups

        mapping = provider_credential_groups()
        assert mapping["sarvam"] == [["stt", "llm", "tts"]]
        assert mapping["deepgram"] == [["stt", "tts"]]
        assert mapping["groq"] == [["llm"]]

    def test_a_two_secret_vendor_is_two_groups(self):
        """Google, and the whole reason the shape is groups. A service-account
        JSON covers Cloud Speech and Cloud TTS; a Gemini key covers the
        language model; neither covers the other."""
        from api.services.configuration.registry import provider_credential_groups

        assert provider_credential_groups()["google"] == [["stt", "tts"], ["llm"]]

    def test_the_keys_are_strings_and_not_enum_members(self):
        """The registry is keyed by ``ServiceProviders`` members, and because
        that enum subclasses ``str`` every lookup with a plain string still
        succeeds — so leaking members out of here does not fail in Python. It
        fails on the wire, where ``str(member)`` is ``"ServiceProviders.SARVAM"``
        and a screen matching on ``"sarvam"`` finds nothing, silently offering
        no fan-out for exactly the vendors that need it most.
        """
        import json

        from api.services.configuration.registry import provider_credential_groups

        mapping = provider_credential_groups()
        assert all(type(provider) is str for provider in mapping)
        assert "sarvam" in json.loads(json.dumps(mapping))

    def test_the_managed_sentinel_is_not_offered_a_key(self):
        """``decibyl`` is registered for all three components but is the
        managed option, not a vendor. Offering to store a key against it is
        offering to store a key with ourselves."""
        from api.services.configuration.registry import provider_credential_groups

        assert "decibyl" not in provider_credential_groups()

    def test_no_group_is_empty(self):
        """An empty group would render a checkbox offering to store the key
        against no slots at all."""
        from api.services.configuration.registry import provider_credential_groups

        for provider, groups in provider_credential_groups().items():
            assert groups, provider
            assert all(group for group in groups), provider

    def test_the_groups_partition_what_the_vendor_serves(self):
        """Every component the vendor serves lands in exactly one group. A
        component in two groups would let one secret overwrite another."""
        from api.services.configuration.registry import (
            components_for_provider,
            provider_credential_groups,
        )

        for provider, groups in provider_credential_groups().items():
            flat = [component for group in groups for component in group]
            assert len(flat) == len(set(flat)), provider
            assert set(flat) == set(components_for_provider(provider)), provider


@pytest.mark.asyncio
class TestTheStaffVaultFansOutToo:
    """Decibyl's own keys had no fan-out while the customer's did.

    That asymmetry cost more than the customer's, not less: one Sarvam account
    serves every managed account on the platform, and a key stored against two
    of three components leaves the managed tier pointing at a provider we hold
    no key for. That failure surfaces at dial time, as the vendor's own 401, on
    a customer's call.
    """

    async def test_one_staff_entry_covers_every_slot_the_vendor_serves(
        self, db_session, async_session, monkeypatch
    ):
        import api.services.configuration.platform_credentials as creds

        monkeypatch.setattr(
            creds, "PLATFORM_CREDENTIAL_SECRET", Fernet.generate_key().decode()
        )

        for component in components_for_provider("sarvam"):
            await creds.set_credential(
                async_session,
                actor_user_id=None,
                component=component,
                provider="sarvam",
                api_key="platform-sarvam-000",
            )
        await async_session.flush()

        held = await creds.managed_providers(async_session)
        for component in ("stt", "llm", "tts"):
            assert "sarvam" in held.get(component, []), component

    async def test_the_same_key_is_stored_not_a_different_one_per_slot(
        self, db_session, async_session, monkeypatch
    ):
        """The point is one account. If the slots ended up holding different
        values the fan-out would be storing a typo three times."""
        import api.services.configuration.platform_credentials as creds

        monkeypatch.setattr(
            creds, "PLATFORM_CREDENTIAL_SECRET", Fernet.generate_key().decode()
        )

        for component in components_for_provider("sarvam"):
            await creds.set_credential(
                async_session,
                actor_user_id=None,
                component=component,
                provider="sarvam",
                api_key="platform-sarvam-000",
            )
        await async_session.flush()

        resolved = [
            await creds.resolve_api_key(
                async_session, component=component, provider="sarvam"
            )
            for component in ("stt", "llm", "tts")
        ]
        assert resolved == ["platform-sarvam-000"] * 3


@pytest.mark.asyncio
class TestTheStaffEndpoint:
    """Through the route, not the service.

    The service layer was already right; what was wrong lived between it and
    the screen. The map went over the wire keyed by ``ServiceProviders``
    members, so the page looked up ``"sarvam"``, found nothing, and offered no
    fan-out — silently, on exactly the vendors that need it. A service-level
    test cannot see that, because the enum subclasses ``str`` and compares
    equal to it in Python.
    """

    async def _client(self, user):
        from contextlib import asynccontextmanager

        from httpx import ASGITransport, AsyncClient

        from api.app import app
        from api.services.auth.depends import get_superuser

        @asynccontextmanager
        async def _ctx():
            async def _override():
                return user

            app.dependency_overrides[get_superuser] = _override
            try:
                async with AsyncClient(
                    transport=ASGITransport(app=app), base_url="http://test"
                ) as client:
                    yield client
            finally:
                app.dependency_overrides.pop(get_superuser, None)

        return _ctx()

    async def test_the_map_arrives_keyed_by_the_name_the_screen_uses(
        self, db_session, async_session
    ):
        from api.db.models import UserModel

        user = UserModel(provider_id="staff-map")
        async_session.add(user)
        await async_session.flush()

        async with await self._client(user) as client:
            response = await client.get("/api/v1/admin/provider-keys")

        assert response.status_code == 200
        mapping = response.json()["provider_components"]
        assert mapping["sarvam"] == [["stt", "llm", "tts"]]
        # Google arrives as two groups, which is what lets the screen offer a
        # fan-out on the speech pair and none on the language model.
        assert mapping["google"] == [["stt", "tts"], ["llm"]]
        assert not any(name.startswith("ServiceProviders.") for name in mapping)

    async def test_one_save_reports_every_slot_it_landed_on(
        self, db_session, async_session, monkeypatch
    ):
        """``applied_to`` is what tells the operator the third slot was
        written. Without it a fan-out that quietly stored one component looks
        identical to one that stored three."""
        import api.services.configuration.platform_credentials as creds
        from api.db.models import UserModel

        monkeypatch.setattr(
            creds, "PLATFORM_CREDENTIAL_SECRET", Fernet.generate_key().decode()
        )
        user = UserModel(provider_id="staff-save")
        async_session.add(user)
        await async_session.flush()

        async with await self._client(user) as client:
            response = await client.put(
                "/api/v1/admin/provider-keys",
                json={
                    "component": "stt",
                    "provider": "sarvam",
                    "api_key": "platform-sarvam-999",
                    "apply_to_all_components": True,
                },
            )

        assert response.status_code == 200
        assert response.json()["applied_to"] == ["stt", "llm", "tts"]

    async def test_without_the_flag_only_the_named_slot_is_written(
        self, db_session, async_session, monkeypatch
    ):
        """The separate-key case. Defaulting the flag on in the form is a
        convenience; the API must not assume it."""
        import api.services.configuration.platform_credentials as creds
        from api.db.models import UserModel

        monkeypatch.setattr(
            creds, "PLATFORM_CREDENTIAL_SECRET", Fernet.generate_key().decode()
        )
        user = UserModel(provider_id="staff-single")
        async_session.add(user)
        await async_session.flush()

        async with await self._client(user) as client:
            response = await client.put(
                "/api/v1/admin/provider-keys",
                json={
                    "component": "tts",
                    "provider": "sarvam",
                    "api_key": "platform-sarvam-111",
                },
            )

        assert response.status_code == 200
        assert response.json()["applied_to"] == ["tts"]


class TestTheVendorWithTwoWaysToAuthenticate:
    """Serving a component and authenticating the same way are different
    questions, and the fan-out has to ask the second one.

    Google is the case. Gemini takes an API key; Cloud Speech and Cloud
    Text-to-Speech take a service-account JSON, and their ``api_key`` field is
    inherited from the base config and documented right there as unused. A
    fan-out built on "which components does this vendor serve" would offer to
    store a Gemini key against two services that cannot read it, and it would
    fail at dial time on a real call rather than at save time in front of the
    person who typed it.

    The answer is not to exclude Google. It is that Google has *two* groups —
    and the speech one is a real fan-out, because one service account covers
    both Cloud Speech and Cloud TTS.
    """

    def test_a_gemini_key_fans_out_to_nothing(self):
        from api.services.configuration.registry import (
            components_for_provider,
            components_sharing_credential,
        )

        assert components_for_provider("google") == ("stt", "llm", "tts")
        assert components_sharing_credential("google", "llm") == ("llm",)

    def test_a_service_account_fans_out_across_both_speech_slots(self):
        """The half that was simply missing before: one service account is one
        secret, and pasting it twice is the registry's lookup done by hand."""
        from api.services.configuration.registry import components_sharing_credential

        assert components_sharing_credential("google", "stt") == ("stt", "tts")
        assert components_sharing_credential("google", "tts") == ("stt", "tts")

    def test_the_two_groups_never_overlap(self):
        """If they did, storing a Gemini key would overwrite the service
        account, or the other way round."""
        from api.services.configuration.registry import components_sharing_credential

        speech = set(components_sharing_credential("google", "stt"))
        language = set(components_sharing_credential("google", "llm"))
        assert speech.isdisjoint(language)

    def test_each_google_slot_names_the_field_its_secret_belongs_in(self):
        from api.services.configuration.registry import credential_field_for

        assert credential_field_for("google", "stt") == "credentials"
        assert credential_field_for("google", "tts") == "credentials"
        assert credential_field_for("google", "llm") == "api_key"

    def test_the_vendors_that_do_share_one_key_are_unaffected(self):
        """The guard must not quietly disqualify the vendors it was built for."""
        from api.services.configuration.registry import components_sharing_credential

        assert components_sharing_credential("sarvam", "llm") == ("stt", "llm", "tts")
        assert components_sharing_credential("openai", "llm") == ("stt", "llm", "tts")
        assert components_sharing_credential("deepgram", "stt") == ("stt", "tts")
        assert components_sharing_credential("elevenlabs", "stt") == ("stt", "tts")

    def test_a_vendor_that_does_not_serve_the_slot_gets_no_group(self):
        from api.services.configuration.registry import (
            components_sharing_credential,
            credential_field_for,
        )

        assert components_sharing_credential("groq", "tts") == ()
        assert credential_field_for("groq", "tts") is None
        assert credential_field_for("not-a-vendor", "llm") is None

    def test_the_marker_defaults_so_a_new_vendor_is_not_silently_dropped(self):
        """A new configuration class that never thinks about this should
        behave like every other vendor, not vanish from the fan-out."""
        from api.services.configuration.registry import BaseServiceConfiguration

        assert BaseServiceConfiguration.credential_field == "api_key"


@pytest.mark.asyncio
class TestTheSecretLandsInTheFieldTheVendorReads:
    """The half that made managed Google speech impossible.

    Resolution used to write every stored secret to ``section.api_key``. For
    almost every vendor that is right. For Google Cloud Speech and Cloud
    Text-to-Speech it authenticated nothing: those services read
    ``credentials``, and ``api_key`` is inherited from the base config and
    documented there as unused.

    Nothing failed at save time. The screen showed a key installed, the
    configuration validated, and the call failed at dial time with Google's own
    error — on a customer's call, on the managed tier, which is the tier for
    accounts that chose not to hold a key precisely so this would not be their
    problem.
    """

    async def _store(self, session, *, component, provider, secret):
        from api.services.configuration import platform_credentials as creds

        await creds.set_credential(
            session,
            actor_user_id=None,
            component=component,
            provider=provider,
            api_key=secret,
            label=None,
        )
        await session.flush()

    async def test_a_google_speech_secret_lands_in_credentials(
        self, db_session, async_session, monkeypatch
    ):
        import api.services.configuration.platform_credentials as creds
        from api.services.configuration.managed_resolution import apply
        from api.services.configuration.registry import GoogleSTTConfiguration

        monkeypatch.setattr(
            creds, "PLATFORM_CREDENTIAL_SECRET", Fernet.generate_key().decode()
        )
        service_account = '{"type":"service_account","project_id":"decibyl"}'
        await self._store(
            async_session, component="stt", provider="google", secret=service_account
        )

        section = GoogleSTTConfiguration(api_key="", use_platform_key=True)
        effective = SimpleNamespace(stt=section, llm=None, tts=None)
        await apply(effective)

        assert section.credentials == service_account
        # And not smuggled into the field the vendor ignores.
        assert section.api_key in ("", None)

    async def test_an_ordinary_vendor_still_lands_in_api_key(
        self, db_session, async_session, monkeypatch
    ):
        """The change must not move everyone else's secret."""
        import api.services.configuration.platform_credentials as creds
        from api.services.configuration.managed_resolution import apply
        from api.services.configuration.registry import SarvamSTTConfiguration

        monkeypatch.setattr(
            creds, "PLATFORM_CREDENTIAL_SECRET", Fernet.generate_key().decode()
        )
        await self._store(
            async_session, component="stt", provider="sarvam", secret="sarvam-key-00000"
        )

        section = SarvamSTTConfiguration(api_key="", use_platform_key=True)
        effective = SimpleNamespace(stt=section, llm=None, tts=None)
        await apply(effective)

        assert section.api_key == "sarvam-key-00000"
