"""Turn a workflow run's recorded usage into costable line items.

The pipeline writes ``workflow_runs.usage_info`` in the shape produced by
``PipelineMetricsAggregator``::

    {
      "llm": {"<processor>|||<model>": {"prompt_tokens": .., "completion_tokens": ..}},
      "tts": {"<processor>|||<model>": <characters>},
      "stt": {"<processor>|||<model>": <seconds>},
      "telephony": {"<provider>": <connected seconds>},
      "call_duration_seconds": <seconds>,
    }

All four cost components are measured: LLM tokens and TTS characters from the
pipeline's metrics aggregator, STT seconds from the same aggregator's
transcription frames, and telephony seconds from the provider status callback.

A component the pipeline did not record simply produces no line. That is
deliberate — the cost engine reports usage it cannot price rather than
inventing a number.
"""

from __future__ import annotations

import re
from typing import Any

from api.enums import CostComponent
from api.services.billing.cost_engine import UsageItem

# Processor class names carry the provider, e.g. "DeepgramSTTService" or
# "DograhLLMService". Strip the service suffix and lower-case what remains.
# Longest first: "DograhFluxSTTService" must match "FluxSTTService" before the
# shorter "STTService", or the provider comes out as "dograhflux".
# Pipecat appends "#<n>" to every processor instance name.
_INSTANCE_SUFFIX = re.compile(r"#\d+$")

_SERVICE_SUFFIXES = (
    "FluxSTTService",
    "LLMService",
    "STTService",
    "TTSService",
    "Service",
)


def provider_from_processor(processor: str) -> str:
    """Best-effort provider name from a pipeline processor class name.

    Pipecat names every processor instance ``f"{ClassName}#{n}"`` (see
    ``BaseObject.__init__``), so the real value arriving here is
    ``"OpenAILLMService#0"``, not ``"OpenAILLMService"``. The instance suffix
    has to come off before the service suffix can match — without that strip
    nothing matched, every provider resolved to ``openaillmservice#0``, no rate
    was ever found, and the whole receipt came back uncosted with margin at
    100%.

    An unrecognised name is returned lower-cased rather than guessed at. It
    will simply have no rate on file, so the cost engine reports it as uncosted
    instead of pricing it wrongly.
    """
    name = _INSTANCE_SUFFIX.sub("", (processor or "").strip())
    for suffix in _SERVICE_SUFFIXES:
        if name.endswith(suffix):
            name = name[: -len(suffix)]
            break
    return name.lower() or "unknown"


def _split_key(key: str) -> tuple[str, str]:
    """Split a ``"<processor>|||<model>"`` usage key into both halves.

    The model half used to be thrown away, which meant every OpenAI LLM call
    priced at one rate whether it ran on gpt-4o or gpt-4o-mini — models more
    than an order of magnitude apart. It is carried through now so the rate
    can be resolved against the model that was actually used.
    """
    processor, _, model = (key or "").partition("|||")
    return processor, model.strip().lower()


def _as_int(value: Any) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError):
        return 0
    return max(result, 0)


def _as_mapping(value: Any) -> dict[str, Any]:
    """Coerce a usage_info bucket to a mapping.

    ``usage_info`` is free-form JSON written by the pipeline. A malformed
    bucket must not take down post-call processing for the whole run.
    """
    return value if isinstance(value, dict) else {}


def usage_items_from_usage_info(
    usage_info: dict[str, Any] | None,
) -> tuple[UsageItem, ...]:
    """Extract costable usage from a run's ``usage_info``.

    LLM quantity is total tokens (prompt + completion); TTS is characters; STT
    is seconds. Zero-quantity entries are dropped — a line item costing nothing
    only adds noise to a receipt.
    """
    usage_info = usage_info or {}
    items: list[UsageItem] = []

    for key, value in _as_mapping(usage_info.get("llm")).items():
        if not isinstance(value, dict):
            continue
        processor, model = _split_key(key)
        tokens = _as_int(value.get("prompt_tokens")) + _as_int(
            value.get("completion_tokens")
        )
        if tokens:
            items.append(
                UsageItem(
                    component=CostComponent.LLM,
                    provider=provider_from_processor(processor),
                    model=model,
                    quantity=tokens,
                )
            )

    for key, value in _as_mapping(usage_info.get("tts")).items():
        processor, model = _split_key(key)
        characters = _as_int(value)
        if characters:
            items.append(
                UsageItem(
                    component=CostComponent.TTS,
                    provider=provider_from_processor(processor),
                    model=model,
                    quantity=characters,
                )
            )

    for key, value in _as_mapping(usage_info.get("stt")).items():
        processor, model = _split_key(key)
        seconds = _as_int(value)
        if seconds:
            items.append(
                UsageItem(
                    component=CostComponent.STT,
                    provider=provider_from_processor(processor),
                    model=model,
                    quantity=seconds,
                )
            )

    # Telephony is recorded by the status callback as connected seconds, keyed
    # by provider — there is no model dimension to a phone call.
    for key, value in _as_mapping(usage_info.get("telephony")).items():
        processor, _ = _split_key(key)
        seconds = _as_int(value)
        if seconds:
            items.append(
                UsageItem(
                    component=CostComponent.TELEPHONY,
                    provider=provider_from_processor(processor),
                    quantity=seconds,
                )
            )

    return tuple(items)


def billable_seconds_from_usage_info(usage_info: dict[str, Any] | None) -> int:
    """Connected call duration in seconds, as recorded by the pipeline."""
    return _as_int(_as_mapping(usage_info).get("call_duration_seconds"))
