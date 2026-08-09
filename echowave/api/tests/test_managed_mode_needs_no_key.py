"""Managed mode: the customer holds no key, and the screen can price it.

Choosing "Decibyl" *is* choosing not to hold an API key. Two things used to
contradict that, and both presented as the customer's fault:

- the model-override screen demanded an API key and validated it against a
  managed service that no longer exists, so every save failed with "Invalid
  ServiceProviders.DECIBYL API key" — a key they were never issued and cannot
  obtain;
- managed mode showed no price at all, while BYOK beside it showed a
  per-minute breakdown, which made the managed option look like the expensive
  one.

These tests pin both, because both are the kind of thing a refactor quietly
reintroduces.
"""

from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient

from api.schemas.ai_model_configuration import (
    DecibylManagedAIModelConfiguration,
    OrganizationAIModelConfigurationV2,
    compile_ai_model_configuration_v2,
)
from api.services.configuration import managed_tiers
from api.services.configuration.check_validity import UserConfigurationValidator
from api.services.configuration.registry import ServiceProviders


class TestNoCustomerKeyIsRequired:
    def test_a_managed_configuration_saves_without_an_api_key(self):
        """The whole point of the mode. Requiring a key here made it
        unreachable — you could look at the tab but never leave it."""
        configuration = DecibylManagedAIModelConfiguration()
        assert configuration.api_key == ""

    def test_a_key_written_by_the_old_ui_still_loads(self):
        """Stored configurations carry a service key from when this meant a
        hosted product. They must keep parsing, not 500 the settings page."""
        configuration = DecibylManagedAIModelConfiguration(api_key="mps_legacy_value")
        assert configuration.api_key == "mps_legacy_value"

    def test_it_compiles_to_decibyl_sections_the_resolver_will_rewrite(self):
        compiled = compile_ai_model_configuration_v2(
            OrganizationAIModelConfigurationV2(
                version=2, mode="decibyl", decibyl=DecibylManagedAIModelConfiguration()
            )
        )
        for section in (compiled.llm, compiled.stt, compiled.tts, compiled.embeddings):
            assert section is not None
            assert section.provider == ServiceProviders.DECIBYL

    def test_validation_no_longer_calls_a_service_that_does_not_exist(self):
        """This used to POST the key to the retired managed service. Every
        call failed, and the failure read as a bad key rather than a dead
        dependency."""
        validator = UserConfigurationValidator()
        assert validator._check_decibyl_api_key("default", "") is True
        assert validator._check_decibyl_api_key("default", "anything") is True

    def test_a_real_provider_on_use_platform_key_needs_no_customer_key_either(self):
        """The direct path (a real vendor, run on Decibyl's key) has the same
        'no customer key to validate' shape as provider=decibyl. Without the
        bypass, this would hit that vendor's own validator against an empty
        string and fail -- exactly the bug this whole file exists to pin."""
        from api.services.configuration.registry import OpenAILLMService

        validator = UserConfigurationValidator()
        section = OpenAILLMService(model="gpt-4o", api_key="", use_platform_key=True)
        assert validator._validate_service(section, "llm") == []

    def test_a_real_provider_without_the_flag_is_still_validated_normally(
        self, monkeypatch
    ):
        """Confirms the bypass is scoped to use_platform_key and does not
        accidentally swallow validation for an ordinary BYOK section. Patched
        before construction: _validator_map captures a bound method at
        __init__ time, so patching the instance afterward would not affect
        the reference already stored in the map."""
        from api.services.configuration.registry import OpenAILLMService

        called = {}

        def fake_openai_check(self, provider, api_key, service_config=None):
            called["ran"] = True
            return False

        monkeypatch.setattr(
            UserConfigurationValidator, "_check_openai_api_key", fake_openai_check
        )
        validator = UserConfigurationValidator()
        section = OpenAILLMService(model="gpt-4o", api_key="sk-bad")
        result = validator._validate_service(section, "llm")
        assert called.get("ran") is True
        assert result and "Invalid" in result[0]["message"]


class TestTheScreenCanPriceIt:
    """The estimator is keyed by real provider and model. "decibyl" is
    neither, so the defaults payload has to say what the tiers resolve to or
    the managed tab cannot show a number."""

    async def _client(self):
        from api.app import app
        from api.services.auth.depends import get_user_with_selected_organization

        @asynccontextmanager
        async def _ctx():
            async def _override():
                # The handler never reads the user — it returns static
                # capability metadata. Overriding the dependency is only about
                # getting past the gate.
                return SimpleNamespace(id=1, selected_organization_id=1)

            app.dependency_overrides[get_user_with_selected_organization] = _override
            try:
                async with AsyncClient(
                    transport=ASGITransport(app=app), base_url="http://test"
                ) as client:
                    yield client
            finally:
                app.dependency_overrides.pop(get_user_with_selected_organization, None)

        return _ctx()

    @pytest.mark.asyncio
    async def test_defaults_name_the_provider_behind_each_tier(self):
        async with await self._client() as client:
            response = await client.get(
                "/api/v1/organizations/model-configurations/v2/defaults"
            )
        assert response.status_code == 200

        upstream = response.json()["decibyl"]["upstream"]
        for component in ("stt", "llm", "tts"):
            assert upstream[component]["provider"]
            assert upstream[component]["model"]
            # Naming "decibyl" here would be circular and unpriceable.
            assert upstream[component]["provider"] != ServiceProviders.DECIBYL.value

    @pytest.mark.asyncio
    async def test_it_reports_the_mapping_rather_than_restating_it(self):
        """Moving a tier is an env change, not a release. If this payload were
        hardcoded the screen would quote a stack we no longer run."""
        async with await self._client() as client:
            response = await client.get(
                "/api/v1/organizations/model-configurations/v2/defaults"
            )
        upstream = response.json()["decibyl"]["upstream"]

        for component in ("stt", "llm", "tts"):
            resolved = managed_tiers.resolve(component, "default")
            assert upstream[component]["provider"] == resolved.provider
            assert upstream[component]["model"] == resolved.model
