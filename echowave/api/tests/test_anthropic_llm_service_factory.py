"""What the call pipeline sends to Claude.

The newer Claude models refuse ``temperature`` with a 400, and the factory used
to send ``0.1`` to every one of them -- so a voice agent on Sonnet 5 or Opus 5
failed on its first turn. They take an effort level instead, and on a call that
is the agent's reasoning-effort setting, ``low`` unless somebody raised it.
"""

from pipecat.services.anthropic.llm import AnthropicLLMService

from api.services.pipecat import reasoning_effort
from api.services.pipecat.service_factory import create_llm_service_from_provider


def _given(value) -> bool:
    return value is not None and type(value).__name__ not in (
        "NotGiven",
        "_NotGiven",
    )


def _build(model: str, **kwargs) -> AnthropicLLMService:
    service = create_llm_service_from_provider(
        "anthropic", model, "sk-ant-test", **kwargs
    )
    assert isinstance(service, AnthropicLLMService)
    return service


def test_haiku_keeps_its_temperature_and_takes_no_effort():
    settings = _build("claude-haiku-4-5")._settings
    assert settings.temperature == 0.1
    assert "output_config" not in settings.extra


def test_sonnet_5_5_gets_no_sampling_and_low_effort():
    settings = _build("claude-sonnet-5-5", temperature=0.7)._settings
    assert not _given(settings.temperature)
    assert settings.extra["output_config"] == {"effort": "low"}


def test_the_agents_effort_reaches_opus():
    settings = _build("claude-opus-5-5", reasoning_effort="high")._settings
    assert settings.extra["output_config"] == {"effort": "high"}


def test_minimal_is_openais_word_and_becomes_low():
    assert reasoning_effort.claude_effort("minimal") == "low"


def test_which_models_refuse_sampling():
    assert reasoning_effort.claude_rejects_sampling("claude-opus-4-8")
    assert reasoning_effort.claude_rejects_sampling("claude-sonnet-5")
    assert not reasoning_effort.claude_rejects_sampling("claude-haiku-4-5")
    assert not reasoning_effort.claude_rejects_sampling("claude-sonnet-4-6")
    assert reasoning_effort.claude_takes_effort("claude-sonnet-4-6")
    assert not reasoning_effort.claude_takes_effort("claude-haiku-4-5")
