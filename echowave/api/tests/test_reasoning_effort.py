"""How hard a reasoning model may think, and who decides.

Every gpt-5 model was sent ``reasoning_effort="minimal"``, hardcoded, with
nothing able to change it. The cost landed somewhere nobody looked: a model
that does not deliberate answers in words where it should have called a tool,
and in a workflow every step change *is* a tool call. Measured on the Kriti
Labs agent, gpt-5-mini at minimal effort never left its first node in two of
two calls; gpt-4.1-mini, which takes no effort setting and so was never
throttled, transitioned in four of five.

The tests that matter are the two ends: the value reaches the model, and a
model that does not understand the parameter is never sent it.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from api.services.pipecat import reasoning_effort
from api.services.pipecat.service_factory import create_llm_service_from_provider


def built(model="gpt-5-mini", **kwargs):
    """The settings the factory hands OpenAI for this model."""
    with patch("api.services.pipecat.service_factory.OpenAILLMService") as service:
        create_llm_service_from_provider(
            provider="openai", model=model, api_key="k", **kwargs
        )
    return service.call_args.kwargs["settings"]


class TestTheDefaultIsNoLongerMinimal:
    def test_minimal_is_not_what_an_unconfigured_agent_gets(self):
        """The old hardcoded value, and the one measured failing to emit node
        transitions. Keeping it as the default would fix nothing."""
        assert reasoning_effort.DEFAULT != "minimal"

    def test_the_default_is_the_cheapest_level_that_still_deliberates(self):
        """Not medium either: this runs on a phone call and thinking time is
        dead air. The throttle existed for a real reason."""
        assert reasoning_effort.DEFAULT == "low"
        assert reasoning_effort.LEVELS.index("low") == 1

    def test_minimal_is_still_available_to_anyone_who_wants_it(self):
        assert "minimal" in reasoning_effort.LEVELS


class TestResolvingWhatWasConfigured:
    @pytest.mark.parametrize("level", reasoning_effort.LEVELS)
    def test_a_real_level_survives(self, level):
        assert reasoning_effort.resolve(level) == level

    @pytest.mark.parametrize("raw", [None, "", "turbo", 3, True, "  "])
    def test_anything_else_falls_back_rather_than_failing_the_call(self, raw):
        """This sits on the path that builds a live call. A typo in a stored
        configuration should cost the agent its preferred setting, not its
        ability to answer the phone."""
        assert reasoning_effort.resolve(raw) == reasoning_effort.DEFAULT

    def test_case_and_padding_are_forgiven(self):
        assert reasoning_effort.resolve("  HIGH ") == "high"


class TestOnlyModelsThatTakeItAreSentIt:
    """Sending the parameter to a model that does not understand it is a 400
    on the first turn of a live call."""

    @pytest.mark.parametrize("model", ["gpt-5", "gpt-5-mini", "gpt-5-nano"])
    def test_the_reasoning_models_take_it(self, model):
        assert reasoning_effort.takes_reasoning_effort(model) is True

    @pytest.mark.parametrize("model", ["gpt-4.1", "gpt-4.1-mini", "gpt-4o", "", None])
    def test_everything_else_does_not(self, model):
        assert reasoning_effort.takes_reasoning_effort(model) is False

    def test_a_non_reasoning_model_is_built_without_the_setting(self):
        settings = built(model="gpt-4.1-mini")

        assert "reasoning_effort" not in (getattr(settings, "extra", None) or {})


class TestItReachesTheModel:
    def test_an_unconfigured_agent_gets_the_new_default(self):
        settings = built()

        assert settings.extra["reasoning_effort"] == reasoning_effort.DEFAULT

    @pytest.mark.parametrize("level", reasoning_effort.LEVELS)
    def test_a_configured_agent_gets_what_it_asked_for(self, level):
        settings = built(reasoning_effort=level)

        assert settings.extra["reasoning_effort"] == level

    def test_an_operator_may_still_choose_the_old_behaviour(self):
        settings = built(reasoning_effort="minimal")

        assert settings.extra["reasoning_effort"] == "minimal"

    def test_temperature_is_still_withheld_from_these_models(self):
        """They reject it outright, which is why this branch exists at all.

        NOT_GIVEN is the point: the field is absent from the request rather
        than sent as null, which the vendor would also refuse."""
        from pipecat.services.settings import is_given

        settings = built(reasoning_effort="high")

        assert not is_given(settings.temperature)


class TestTheSlotCarriesIt:
    def test_the_agents_configured_value_reaches_the_factory(self):
        """It rides on the model slot beside temperature and max_tokens, so a
        per-agent choice is possible at all."""
        from api.services.pipecat.service_factory import create_llm_service

        user_config = SimpleNamespace(
            llm=SimpleNamespace(
                provider="openai",
                model="gpt-5-mini",
                api_key="k",
                temperature=None,
                max_tokens=None,
                reasoning_effort="high",
            )
        )

        with patch("api.services.pipecat.service_factory.OpenAILLMService") as service:
            create_llm_service(user_config)

        assert service.call_args.kwargs["settings"].extra["reasoning_effort"] == "high"
