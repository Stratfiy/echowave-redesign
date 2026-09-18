"""The platform's own key is checked against the models we sell on it.

A customer's key is validated against the agent configuration that names its
model. The platform's key had no agent, so it was only ever asked "do you
work" -- and a managed tier naming a model our plan does not include would
pass that question and then refuse every managed call on the tier as a 403 at
dial time.
"""

from __future__ import annotations

from unittest.mock import patch

from api.services.configuration import key_validation, managed_tiers


class TestEveryTierIsReachable:
    def test_every_tier_on_sale_resolves_to_a_provider_and_model(self):
        # The readiness question is only as good as the list it walks. If this
        # ever came back empty the check would pass vacuously.
        tiers = managed_tiers.every_tier()
        assert tiers
        for component, tier in tiers:
            resolved = managed_tiers.resolve(component, tier)
            assert resolved.provider
            assert resolved.model

    def test_it_matches_the_per_component_lists(self):
        for component, tier in managed_tiers.every_tier():
            assert tier in managed_tiers.tiers_for(component)


class TestTheModelReachesTheProbe:
    async def test_the_model_is_handed_to_the_validator(self):
        # The whole point: without this the probe reads the model off a
        # service configuration that does not exist on the platform sweep,
        # finds None, and skips the entitlement question entirely.
        seen: dict[str, object] = {}

        class _Validator:
            _validator_map = {"elevenlabs": object()}

            def _check_api_key(self, provider, api_key, service_config=None):
                seen["provider"] = provider
                seen["model"] = getattr(service_config, "model", None)
                return True

        with patch.object(key_validation, "_validator", return_value=_Validator()):
            result = await key_validation.validate_key(
                "elevenlabs", "sk-test", model="eleven_flash_v2_5"
            )

        assert seen["provider"] == "elevenlabs"
        assert seen["model"] == "eleven_flash_v2_5"
        assert result.outcome == "valid"

    async def test_no_model_means_no_configuration_is_invented(self):
        # A caller with nothing to say about the model must not have one
        # guessed for it: the probe's other questions read the same object.
        seen: dict[str, object] = {}

        class _Validator:
            _validator_map = {"openai": object()}

            def _check_api_key(self, provider, api_key, service_config=None):
                seen["config"] = service_config
                return True

        with patch.object(key_validation, "_validator", return_value=_Validator()):
            await key_validation.validate_key("openai", "sk-test")

        assert seen["config"] is None
