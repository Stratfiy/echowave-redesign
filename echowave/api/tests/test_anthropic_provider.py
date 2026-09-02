"""Claude, as a BYOK LLM provider.

The platform could already reach most vendors and not the one whose models a
lot of teams standardise on. Adding it is four lines of registry and one
factory branch — which is exactly why it is worth a test file.

The risk in a new provider is never the happy path. It is the wiring that gets
half-done: a class registered but missing from the discriminated union is
offered in the dropdown and then refused on save; a provider missing from the
key-validation map reads as "invalid key" for a key that is perfectly good.
Both failures point at the customer's key rather than at us, and neither says
what is actually wrong. Each one has a test here.
"""

import ast
import re
from pathlib import Path

import pydantic
import pytest

from api.services.configuration.registry import (
    REGISTRY,
    AnthropicLLMConfiguration,
    LLMConfig,
    ServiceProviders,
    ServiceType,
    known_providers,
)

#: Read rather than imported: the module pulls in a vendor SDK per provider,
#: and every question here is about what the source says.
CHECK_VALIDITY = (
    Path(__file__).resolve().parents[1]
    / "services"
    / "configuration"
    / "check_validity.py"
)


def _valid(**overrides):
    return AnthropicLLMConfiguration(
        **{"model": "claude-haiku-4-5", "api_key": "sk-ant-test", **overrides}
    )


class TestItIsOfferedAndAccepted:
    def test_it_is_registered_as_an_llm_provider(self):
        registered = {
            (p.value if hasattr(p, "value") else p) for p in REGISTRY[ServiceType.LLM]
        }
        assert ServiceProviders.ANTHROPIC.value in registered

    def test_a_stored_section_actually_validates(self):
        """Registering is not the same as being usable.

        ``@register_llm`` puts a class in REGISTRY, which is what fills the
        provider dropdown — but a saved configuration is parsed through the
        ``LLMConfig`` discriminated union, which is written by hand. A class in
        one and not the other is offered on screen and then refused on save.
        """
        adapter = pydantic.TypeAdapter(LLMConfig)
        parsed = adapter.validate_python(
            {
                "provider": "anthropic",
                "model": "claude-haiku-4-5",
                "api_key": "sk-ant-test",
            }
        )

        assert parsed.provider == ServiceProviders.ANTHROPIC
        assert parsed.model == "claude-haiku-4-5"

    def test_it_appears_on_the_provider_keys_screen(self):
        """`known_providers()` is derived from the registries rather than kept
        by hand, so this asserts the derivation actually reaches the new entry
        — that is what puts it on the screen with no UI change."""
        assert known_providers().get("anthropic") == ("llm",)

    def test_an_api_key_is_required(self):
        """Anthropic has no keyless path — unlike a self-hosted endpoint. A
        configuration saved without one would fail on the first call instead."""
        with pytest.raises(pydantic.ValidationError):
            AnthropicLLMConfiguration(model="claude-haiku-4-5")

    def test_it_carries_the_generation_controls(self):
        cfg = _valid(temperature=0.4)
        assert cfg.temperature == 0.4


class TestTheModelIdentifiers:
    def test_the_default_is_the_low_latency_model(self):
        """This is a voice product. The caller hears every millisecond of the
        first token, so the default has to be the fastest of the family, not
        the most capable."""
        assert _valid().model == "claude-haiku-4-5"

    def test_no_offered_model_carries_a_date_suffix(self):
        """Anthropic's current identifiers are complete as they stand.
        Appending a date produces a model that does not exist — which surfaces
        as a vendor 404 on the first call, long after the key was saved and
        reported valid."""
        examples = AnthropicLLMConfiguration.model_fields["model"].json_schema_extra[
            "examples"
        ]

        assert examples, "The offered model list has gone missing."
        for model in examples:
            assert not re.search(r"-\d{8}$", model), model

    def test_a_model_outside_the_list_is_still_accepted(self):
        """`allow_custom_input` is on, so a model released after this list was
        written does not need a deploy to be usable."""
        assert _valid(model="claude-something-newer").model == "claude-something-newer"


class TestTheWiringIsComplete:
    def test_key_validation_knows_this_provider(self):
        """An unmapped provider returns False, i.e. "invalid key", and a
        perfectly good configuration cannot be saved."""
        assert re.search(
            r"ServiceProviders\.ANTHROPIC\.value: self\._check_\w+",
            CHECK_VALIDITY.read_text(),
        )

    def test_no_validator_entry_points_at_a_method_that_does_not_exist(self):
        """The map is built in __init__, so a stale name is an AttributeError
        at construction rather than a test failure somewhere useful."""
        text = CHECK_VALIDITY.read_text()
        referenced = set(re.findall(r"self\.(_check_\w+)", text))
        defined = {
            n.name for n in ast.walk(ast.parse(text)) if isinstance(n, ast.FunctionDef)
        }

        assert not (referenced - defined), sorted(referenced - defined)

    def test_the_sdk_ships_with_every_install_path(self):
        """The Anthropic SDK arrives through pipecat's optional `anthropic`
        extra. Miss one install path and the provider works locally and
        raises ModuleNotFoundError in the image that actually takes calls.
        """
        root = Path(__file__).resolve().parents[2]
        paths = [
            root / "api" / "Dockerfile",
            root / "scripts" / "setup_requirements.sh",
            root / "scripts" / "setup_pipecat.sh",
            root / "scripts" / "setup_requirements.ps1",
        ]

        for path in paths:
            # Only lines that actually install. The Dockerfile also mentions
            # `pipecat[webrtc]` in a comment, and matching that instead reads
            # as a missing extra on a file that is perfectly correct.
            installs = [
                line
                for line in path.read_text().splitlines()
                if "pipecat[" in line and "pip install" in line
            ]
            assert installs, f"{path.name}: no pipecat install line found"

            for line in installs:
                extras = re.search(r"pipecat\[([^\]]+)\]", line)
                assert extras, f"{path.name}: install line has no extras list"
                assert "anthropic" in extras.group(1).split(","), path.name

    def test_the_factory_builds_it(self):
        """A provider the factory does not know falls through to whatever the
        final `else` does, which is not this vendor."""
        factory = (
            Path(__file__).resolve().parents[1]
            / "services"
            / "pipecat"
            / "service_factory.py"
        ).read_text()

        assert "ServiceProviders.ANTHROPIC.value" in factory
        assert "AnthropicLLMService(" in factory


class TestItIsPricedBeforeItIsSold:
    """The platform-key path is only safe once these rows exist.

    `REMAINING-WORK.md` records what an empty price book does: it reports 100%
    margin rather than an error, on every call, silently. A provider offered on
    Decibyl's own key with no rate is a call we pay for and do not bill.
    """

    def _rows(self):
        from api.services.billing.default_rates import LLM_RATES

        return [r for r in LLM_RATES if r.provider == "anthropic"]

    def test_every_offered_model_carries_its_own_rate(self):
        """A model in the picker with no row falls through to the provider-wide
        fallback, which is Haiku. Opus is 5x Haiku, so that is not a thin
        margin — it is selling the call at a fifth of cost."""
        priced = {r.model for r in self._rows() if r.model}
        offered = set(
            AnthropicLLMConfiguration.model_fields["model"].json_schema_extra[
                "examples"
            ]
        )

        assert not (offered - priced), sorted(offered - priced)

    def test_there_is_exactly_one_provider_wide_fallback(self):
        """Two rows for the same (provider, model) key is an ambiguous price,
        and which one wins depends on seed order rather than on a decision."""
        wide = [r for r in self._rows() if not r.model]
        assert len(wide) == 1, [r.basis for r in wide]

    def test_the_fallback_is_the_cheapest_of_the_family(self):
        """The file's stated rule: an unpriced model under-reports rather than
        over-reports, so a surprise on the invoice is a pleasant one."""
        rows = self._rows()
        wide = next(r for r in rows if not r.model)
        named = [r.usd_per_unit for r in rows if r.model]

        assert wide.usd_per_unit == min(named)

    def test_the_rates_are_not_provisional(self):
        """`carrier_rates` refuses a managed path while a rate carries the
        provisional marker. These are published list prices, so they must not
        carry it — a provisional rate here would block the very tier this
        exists to enable."""
        assert not any(r.provisional for r in self._rows())

    def test_price_ordering_matches_the_family(self):
        """Cheapest to dearest, as Anthropic publishes them. An inverted pair
        here means a tier that reads as an upgrade bills as a discount."""
        by_model = {r.model: r.usd_per_unit for r in self._rows() if r.model}

        assert (
            by_model["claude-haiku-4-5"]
            < by_model["claude-sonnet-5"]
            < by_model["claude-sonnet-4-6"]
            < by_model["claude-opus-5"]
        )
