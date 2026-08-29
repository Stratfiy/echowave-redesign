"""Any OpenAI-compatible endpoint, as a named option.

Pointing the `openai` provider at your own `base_url` already worked. Nobody
could tell, which is the entire gap this closes: Vapi and Bolna both list
"Custom LLM" and a customer with a gateway or a fine-tune looks for that name,
not for a field on somebody else's provider.

The risk in a new provider is never the happy path. It is the wiring that gets
half-done -- a validator map entry, a pricing row, a key check -- and fails
somewhere that does not say why.
"""

import ast
import re
from pathlib import Path

import pydantic
import pytest

from api.services.configuration.registry import (
    REGISTRY,
    CustomLLMConfiguration,
    ServiceProviders,
    ServiceType,
)

SERVICE_FACTORY = (
    Path(__file__).resolve().parents[1] / "services" / "pipecat" / "service_factory.py"
)
#: Read rather than imported: the module pulls in a vendor SDK per provider,
#: and every question here is about what the source says.
CHECK_VALIDITY = (
    Path(__file__).resolve().parents[1] / "services" / "configuration" / "check_validity.py"
)


def _valid(**overrides):
    return CustomLLMConfiguration(
        **{"model": "my-model", "base_url": "https://gw.example.com/v1", **overrides}
    )


class TestTheEndpointIsTheConfiguration:
    def test_it_is_registered_as_an_llm_provider(self):
        registered = {
            (p.value if hasattr(p, "value") else p) for p in REGISTRY[ServiceType.LLM]
        }
        assert ServiceProviders.CUSTOM_LLM.value in registered

    def test_a_stored_section_actually_validates(self):
        """Registering is not the same as being usable.

        `@register_llm` puts a class in REGISTRY, which is what fills the
        provider dropdown -- but a saved configuration is parsed through the
        `LLMConfig` discriminated union, which is written by hand. A class in
        one and not the other is offered on screen and then refused on save.
        """
        from pydantic import TypeAdapter

        from api.services.configuration.registry import LLMConfig

        section = TypeAdapter(LLMConfig).validate_python(
            {
                "provider": "custom_llm",
                "model": "my-model",
                "base_url": "https://gw.example.com/v1",
                "api_key": "sk-x",
            }
        )

        assert isinstance(section, CustomLLMConfiguration)

    def test_base_url_is_required_at_save_time(self):
        """Not at dial time, where it becomes a call that connects and dies."""
        with pytest.raises(pydantic.ValidationError):
            CustomLLMConfiguration(model="my-model")

    def test_an_endpoint_may_need_no_key(self):
        assert _valid().api_key is None

    def test_it_carries_the_generation_controls(self):
        config = _valid(temperature=0.3, max_tokens=250)

        assert config.temperature == 0.3
        assert config.max_tokens == 250


class TestItCannotBeBilledAsSomethingElse:
    def test_the_platform_key_option_is_refused(self):
        """The quiet failure, not the loud one.

        The factory builds this with the OpenAI service class, so usage on a
        platform key would be attributed to OpenAI and metered at OpenAI's rate
        -- for an endpoint we neither run nor pay for. That is a margin figure
        nobody checks, so it has to be refused where somebody reads the error.
        """
        with pytest.raises(pydantic.ValidationError, match="your own key"):
            _valid(use_platform_key=True)

    def test_a_customer_key_still_works(self):
        assert _valid(api_key="sk-whatever").api_key == "sk-whatever"


class TestTheWiringIsComplete:
    def test_key_validation_knows_this_provider(self):
        """An unmapped provider returns False, i.e. "invalid key", and a
        perfectly good configuration cannot be saved."""
        assert re.search(
            r"ServiceProviders\.CUSTOM_LLM\.value: self\._check_\w+",
            CHECK_VALIDITY.read_text(),
        )

    def test_it_is_exempt_from_requiring_an_api_key(self):
        """It shares the no-key path with Speaches. Without that, a valid
        keyless endpoint falls through to the generic "key missing" branch."""
        text = CHECK_VALIDITY.read_text()
        exemption = re.search(
            r"if provider in \((?P<body>[^)]*)\):", text, re.DOTALL
        )
        assert exemption, "The no-API-key branch has moved."
        assert "CUSTOM_LLM" in exemption.group("body")

    def test_no_validator_entry_points_at_a_method_that_does_not_exist(self):
        """The map is built in __init__, so a stale name is an AttributeError
        at construction rather than a test failure somewhere useful."""
        text = CHECK_VALIDITY.read_text()
        referenced = set(re.findall(r"self\.(_check_\w+)", text))
        defined = {
            n.name for n in ast.walk(ast.parse(text)) if isinstance(n, ast.FunctionDef)
        }

        assert not (referenced - defined), sorted(referenced - defined)

    def test_the_factory_builds_it_and_guards_the_url(self):
        branch = re.search(
            r"ServiceProviders\.CUSTOM_LLM\.value:(?P<body>.*?)(?=\n    elif )",
            SERVICE_FACTORY.read_text(),
            re.DOTALL,
        )
        assert branch, "No CUSTOM_LLM branch in the LLM factory"
        body = branch.group("body")

        assert "_validate_runtime_service_url(base_url" in body, (
            "A customer-supplied URL we have never seen is exactly what the "
            "SSRF guard is for."
        )
        assert "_llm_tuning(temperature, max_tokens)" in body
