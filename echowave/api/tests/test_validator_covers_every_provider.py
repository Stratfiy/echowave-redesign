"""Every registered provider must have a validator.

An unmapped provider is not skipped — it is judged invalid:

    validator = self._validator_map.get(provider)
    if not validator:
        return False

So a provider added to the registry but not to the validator map cannot be
saved at all. The customer sees "Failed to save model configuration" with no
indication of which slot is at fault, and the provider they just selected looks
broken. That is how Rumik shipped: the registry entry, the service factory
branch, the rate and the key vault all worked, and Save returned false.

Registering a provider is four or five separate edits, and this is the one with
no visible symptom until somebody tries to save. A test is cheaper than
remembering.
"""

import pytest

from api.services.configuration.check_validity import UserConfigurationValidator
from api.services.configuration.registry import REGISTRY, ServiceType

#: Providers whose validation is handled by a dedicated branch in
#: `_validate_service` before the map is consulted, so they legitimately have no
#: map entry. Each is here because its credential is not an api_key.
_HANDLED_BEFORE_THE_MAP = {
    "speaches",  # needs no key at all
    "google_vertex",  # service-account credentials
    "google_vertex_realtime",  # service-account credentials
    "aws_bedrock",  # AWS access/secret pair
}


def _registered_providers() -> set[str]:
    providers: set[str] = set()
    for service_type in (
        ServiceType.LLM,
        ServiceType.TTS,
        ServiceType.STT,
        ServiceType.REALTIME,
        ServiceType.EMBEDDINGS,
    ):
        for provider in REGISTRY.get(service_type, {}):
            providers.add(getattr(provider, "value", provider))
    return providers


@pytest.mark.parametrize("provider", sorted(_registered_providers()))
def test_every_registered_provider_can_be_validated(provider):
    """A provider you can select must be a provider you can save."""
    if provider in _HANDLED_BEFORE_THE_MAP:
        pytest.skip(f"{provider} is validated before the map is consulted")

    validator_map = UserConfigurationValidator()._validator_map
    assert provider in validator_map, (
        f"{provider!r} is registered and selectable, but has no validator. "
        f"Saving any configuration that uses it will fail with "
        f"'Invalid {provider} configuration' and no way to proceed. "
        f"Add an entry to _validator_map in check_validity.py."
    )


def test_rumik_specifically_is_covered():
    """The provider that found this hole."""
    assert "rumik" in UserConfigurationValidator()._validator_map


def test_an_unmapped_provider_is_treated_as_invalid():
    """Documents why the test above matters rather than being a formality.

    If this ever becomes permissive, the parametrised test above stops
    protecting anything and should be reconsidered rather than deleted.
    """
    validator = UserConfigurationValidator()
    assert validator._check_api_key("not-a-real-provider", "some-key", None) is False
