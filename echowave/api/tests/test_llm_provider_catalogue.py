"""The LLM provider catalogue, and the two ways adding one goes wrong.

Adding a vendor is four lines of registry and a factory branch. The failures
are never in those lines — they are in the wiring that gets half-done, and both
kinds point at the customer rather than at us:

* a class in ``REGISTRY`` but missing from the ``LLMConfig`` union is offered
  in the dropdown and then refused on save;
* a provider missing from the key-validation map reads as "invalid key" for a
  key that is perfectly good.

The third check here is about money rather than wiring. Several of these
vendors carry rates taken from a published price list and never confirmed
against an invoice, marked ``provisional``. That marker gates nothing outside
telephony today, so nothing stops a managed tier resolving to one — and a
managed tier is precisely where an unconfirmed cost becomes our loss instead of
a BYOK customer's non-event. The test below is what stops it.
"""

import ast
import re
from pathlib import Path

import pydantic
import pytest

from api.services.billing.default_rates import DEFAULT_RATES
from api.services.configuration import managed_tiers
from api.services.configuration.registry import (
    REGISTRY,
    LLMConfig,
    ServiceType,
    known_providers,
)
from api.services.pipecat import service_factory

CHECK_VALIDITY = (
    Path(__file__).resolve().parents[1]
    / "services"
    / "configuration"
    / "check_validity.py"
)

#: Added as one batch. Each is OpenAI-wire-compatible, which is why they share
#: a key validator rather than each guessing at a different models endpoint.
NEW_PROVIDERS = ["cerebras", "deepseek", "mistral", "fireworks"]


@pytest.mark.parametrize("provider", NEW_PROVIDERS)
class TestEachProviderIsFullyWired:
    def test_it_is_registered_as_an_llm_provider(self, provider):
        registered = {
            (p.value if hasattr(p, "value") else p) for p in REGISTRY[ServiceType.LLM]
        }
        assert provider in registered

    def test_a_stored_configuration_validates(self, provider):
        """REGISTRY fills the dropdown; the union parses what was saved. A
        class in one and not the other is offered and then refused."""
        parsed = pydantic.TypeAdapter(LLMConfig).validate_python(
            {"provider": provider, "model": "some-model", "api_key": "k"}
        )
        assert parsed.provider.value == provider

    def test_it_appears_on_the_provider_keys_screen(self, provider):
        assert "llm" in (known_providers().get(provider) or ())

    def test_key_validation_knows_it(self, provider):
        """Unmapped means every key is reported invalid."""
        assert re.search(
            rf"ServiceProviders\.{provider.upper()}\.value: self\._check_\w+",
            CHECK_VALIDITY.read_text(),
        )

    def test_the_factory_builds_it(self, provider):
        """A provider the factory does not know falls through to whatever the
        final ``else`` does, which is not this vendor."""
        source = Path(service_factory.__file__).read_text()
        assert f"ServiceProviders.{provider.upper()}.value" in source

    def test_it_carries_a_rate(self, provider):
        """An unpriced provider bills at whatever the fallback lookup finds,
        which for a vendor with no rows at all is nothing."""
        assert any(r.provider == provider for r in DEFAULT_RATES)


class TestTheValidatorMapStaysHonest:
    def test_no_entry_points_at_a_method_that_does_not_exist(self):
        """The map is built in ``__init__``, so a stale name is an
        AttributeError at construction rather than a test failure somewhere
        useful."""
        text = CHECK_VALIDITY.read_text()
        referenced = set(re.findall(r"self\.(_check_\w+)", text))
        defined = {
            n.name for n in ast.walk(ast.parse(text)) if isinstance(n, ast.FunctionDef)
        }
        assert not (referenced - defined), sorted(referenced - defined)


class TestNoManagedTierRunsOnAnUnconfirmedPrice:
    """The one check here that is about money rather than wiring.

    A BYOK customer on a provisionally-priced model costs us nothing — they pay
    their own vendor. A *managed* customer on one is billed cost × markup
    against a number nobody has checked, and if the real price is higher we sell
    the call at a loss on every minute, with the markup faithfully applied to
    the wrong figure and nothing anywhere reporting an error.

    So provisional pricing and the managed tier must not meet. Offering a
    vendor BYOK the day it is added, and promoting it to a tier only once its
    rate has been confirmed against an invoice, is the whole point of the flag.
    """

    def _provisional_llm_providers(self) -> set[str]:
        return {
            r.provider
            for r in DEFAULT_RATES
            if r.provisional and r.component.value == "llm"
        }

    def test_the_new_batch_is_marked_provisional(self):
        """These figures came off published price pages, not invoices. If a
        later change confirms one, drop the flag in that same change — this
        assertion is the reminder."""
        assert set(NEW_PROVIDERS) <= self._provisional_llm_providers()

    def test_no_managed_llm_tier_resolves_to_a_provisional_provider(self):
        provisional = self._provisional_llm_providers()
        offending = []

        for tier in managed_tiers.LLM_TIERS:
            upstream = managed_tiers._defaults()[("llm", tier)]
            if upstream.provider in provisional:
                offending.append((tier, upstream.provider, upstream.model))

        assert not offending, (
            "A managed tier resolves to a provisionally-priced model — "
            "confirm the rate against an invoice and drop the flag before "
            f"pointing a tier at it: {offending}"
        )
