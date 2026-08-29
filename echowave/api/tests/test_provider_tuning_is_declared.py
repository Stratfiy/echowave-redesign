"""Voice tuning belongs in the provider class, not in the factory branch.

`stability=0.8` and `similarity_boost=0.75` were literals inside
`create_tts_service`'s ElevenLabs branch. Nothing was wrong with the values --
what was wrong is that changing how a customer's voice sounds meant editing
Python and redeploying, and no screen could show what it was set to.

A field declared on the provider class is reachable three ways at once: the
config validates it, `_byok_provider_schemas` serves its range to the form,
and the form renders a bounded number as a slider without knowing anything
about the provider. So the declaration is the feature.
"""

import re
from pathlib import Path

from api.services.configuration.registry import REGISTRY, ServiceType

#: Resolved by path rather than by importing the module: these checks only read
#: its source text, and importing it drags in aiohttp and every vendor SDK the
#: factory can build.
SERVICE_FACTORY = (
    Path(__file__).resolve().parents[1] / "services" / "pipecat" / "service_factory.py"
)

#: The literals the ElevenLabs branch passed before these became fields.
#: Declaring them must not change how any existing call sounds -- a stored
#: config has no value for them, so the field default is what it runs on.
ELEVENLABS_SHIPPED_DEFAULTS = {
    "stability": 0.8,
    "similarity_boost": 0.75,
    "style": 0.0,
}


def _properties(service_type: ServiceType, provider: str) -> dict:
    for name, cls in REGISTRY[service_type].items():
        value = name.value if hasattr(name, "value") else name
        if value == provider:
            return cls.model_json_schema()["properties"]
    raise AssertionError(f"{provider} is not registered for {service_type}")


class TestElevenLabsVoiceShapingIsConfigurable:
    def test_each_field_is_declared_with_a_range(self):
        props = _properties(ServiceType.TTS, "elevenlabs")

        for field in ELEVENLABS_SHIPPED_DEFAULTS:
            assert field in props, f"{field} is not declared on the provider class"
            assert props[field]["minimum"] == 0.0
            assert props[field]["maximum"] == 1.0

    def test_the_defaults_are_what_the_factory_used_to_hardcode(self):
        """Otherwise this is a voice change wearing a refactor's clothes."""
        props = _properties(ServiceType.TTS, "elevenlabs")

        for field, shipped in ELEVENLABS_SHIPPED_DEFAULTS.items():
            assert props[field]["default"] == shipped

    def test_the_factory_no_longer_hardcodes_them(self):
        source = SERVICE_FACTORY.read_text()

        for field, shipped in ELEVENLABS_SHIPPED_DEFAULTS.items():
            assert not re.search(rf"\n\s+{field}={shipped}\b", source), (
                f"{field} is still passed as a literal. Read it off the config "
                "with getattr so the declared field actually reaches the call."
            )

    def test_the_factory_falls_back_to_the_shipped_value(self):
        """A managed tier class carries no tuning fields at all.

        After managed_resolution a section answers provider == "elevenlabs"
        while being a Decibyl tier class with no `stability` attribute. Reading
        it directly raises AttributeError mid-call -- the failure mode _carry()
        exists to prevent -- so each read needs the shipped value as fallback.
        """
        source = SERVICE_FACTORY.read_text()

        for field, shipped in ELEVENLABS_SHIPPED_DEFAULTS.items():
            assert re.search(
                rf'getattr\(\s*user_config\.tts,\s*"{field}",\s*{shipped}\s*\)',
                source,
            ), f"{field} must be read with getattr(..., {shipped}) as fallback"


class TestBoundedNumbersReachTheFormAsRanges:
    """The form renders any number with a minimum and a maximum as a slider.

    That rule is why adding a provider costs a class and not a UI branch, so
    it is worth asserting that the classes actually carry the bounds.
    """

    def test_every_declared_speed_is_bounded(self):
        unbounded = []
        for service_type in (ServiceType.TTS, ServiceType.STT, ServiceType.LLM):
            for name, cls in REGISTRY[service_type].items():
                props = cls.model_json_schema()["properties"]
                for field, prop in props.items():
                    if prop.get("type") != "number":
                        continue
                    # gt=/lt= emit exclusive* instead; the form reads both and
                    # starts the track one step inside an exclusive bound.
                    has_lower = "minimum" in prop or "exclusiveMinimum" in prop
                    has_upper = "maximum" in prop or "exclusiveMaximum" in prop
                    if has_lower and has_upper:
                        continue
                    unbounded.append(f"{service_type.name}.{name}.{field}")

        assert not unbounded, (
            f"Numeric fields with no declared range: {sorted(unbounded)}. "
            "Give each a ge=/gt= and le=/lt= so the form can render a slider "
            "and the server rejects out-of-range values before the call starts."
        )

    def test_an_exclusive_bound_is_still_a_bound(self):
        """MiniMax rejects temperature 0, so the field is gt=0, not ge=0.

        The constraint is right; it is the reader that has to cope. Left
        unhandled this one field rendered as a free-text box while every
        sibling got a slider.
        """
        prop = _properties(ServiceType.LLM, "minimax")["temperature"]

        assert prop.get("exclusiveMinimum") == 0.0
        assert prop["maximum"] == 2.0

    def test_a_sample_of_providers_carry_their_own_ranges(self):
        """The ranges differ per provider, which is the point."""
        assert _properties(ServiceType.TTS, "cartesia")["speed"]["maximum"] == 1.5
        assert _properties(ServiceType.TTS, "sarvam")["speed"]["minimum"] == 0.5
        assert _properties(ServiceType.TTS, "elevenlabs")["speed"]["maximum"] == 2.0


class TestRegisteringIsNotEnough:
    """Every registered provider must also be parseable as a stored section.

    `@register_llm` fills the provider dropdown; the `LLMConfig` union is what
    a saved configuration is validated against, and it is maintained by hand.
    A provider in one and not the other can be chosen on screen and then fails
    on save with a discriminator error that names nothing useful.
    """

    def test_every_registered_llm_is_in_the_union(self):
        import typing

        from api.services.configuration.registry import LLMConfig

        in_union = {
            member.model_fields["provider"].default
            for member in typing.get_args(typing.get_args(LLMConfig)[0])
        }
        in_union = {p.value if hasattr(p, "value") else p for p in in_union}
        registered = {
            (p.value if hasattr(p, "value") else p) for p in REGISTRY[ServiceType.LLM]
        }

        missing = registered - in_union
        assert not missing, (
            f"Registered but not in LLMConfig: {sorted(missing)}. The provider "
            "appears in the dropdown and then fails to save."
        )
