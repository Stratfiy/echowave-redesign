"""The registry and the discriminated unions must list the same providers.

There are two records of which providers exist, and they are maintained
differently. `REGISTRY` is populated by the `@register_*` decorators, so adding
a config class updates it automatically. `LLMConfig`, `TTSConfig`, `STTConfig`,
`RealtimeConfig` and `EmbeddingsConfig` are hand-written `Union`s, so adding a
class does *not* update them.

Miss the union and the provider is selectable everywhere — picker, form, key
vault, rate card — right up until something parses a saved configuration, at
which point Pydantic rejects the tag:

    Input tag 'rumik' found using 'provider' does not match any of the expected
    tags: <ServiceProviders.DEEPGRAM: 'deepgram'>, ...

That error names every provider except the one you chose, and appears on a
screen that gives no hint which of four tabs caused it. Rumik shipped that way.

Both directions matter. A class in the registry but not the union cannot be
parsed; a class in the union but not the registry is worse, because it is a
provider the picker will never offer and nobody will notice is dead.
"""

import typing

import pytest

from api.services.configuration import registry
from api.services.configuration.registry import REGISTRY, ServiceType

#: union attribute name → the service type whose registry it should mirror.
_UNIONS: dict[str, ServiceType] = {
    "LLMConfig": ServiceType.LLM,
    "TTSConfig": ServiceType.TTS,
    "STTConfig": ServiceType.STT,
    "RealtimeConfig": ServiceType.REALTIME,
    "EmbeddingsConfig": ServiceType.EMBEDDINGS,
}


def _union_members(union_name: str) -> set[type]:
    """The config classes inside an Annotated[Union[...], Field(discriminator)]."""
    annotated = getattr(registry, union_name)
    inner = typing.get_args(annotated)[0]
    return set(typing.get_args(inner))


def _registry_members(service_type: ServiceType) -> set[type]:
    return set(REGISTRY.get(service_type, {}).values())


@pytest.mark.parametrize("union_name,service_type", sorted(_UNIONS.items()))
def test_every_registered_class_is_in_the_union(union_name, service_type):
    missing = _registry_members(service_type) - _union_members(union_name)
    assert not missing, (
        f"{', '.join(sorted(c.__name__ for c in missing))} "
        f"{'is' if len(missing) == 1 else 'are'} registered for "
        f"{service_type.name} but missing from {union_name}. The provider will "
        f'be selectable and then fail to parse with "Input tag ... does not '
        f'match any of the expected tags". Add it to the Union in registry.py.'
    )


@pytest.mark.parametrize("union_name,service_type", sorted(_UNIONS.items()))
def test_every_union_member_is_registered(union_name, service_type):
    orphans = _union_members(union_name) - _registry_members(service_type)
    assert not orphans, (
        f"{', '.join(sorted(c.__name__ for c in orphans))} "
        f"{'is' if len(orphans) == 1 else 'are'} in {union_name} but not "
        f"registered for {service_type.name}. Nothing will ever offer it, so "
        f"it is dead configuration surface. Add the @register_* decorator or "
        f"remove it from the Union."
    )


def test_rumik_is_in_the_tts_union():
    """The provider that found this hole."""
    names = {c.__name__ for c in _union_members("TTSConfig")}
    assert "RumikTTSConfiguration" in names


def test_the_unions_are_shaped_the_way_this_test_assumes():
    # If the Annotated[Union[...]] shape ever changes, every assertion above
    # would compare empty sets and pass while checking nothing.
    for union_name in _UNIONS:
        members = _union_members(union_name)
        assert members, (
            f"{union_name} parsed as empty — the test is not checking anything"
        )
