"""Turn a workflow run's recorded usage into costable line items.

The pipeline writes ``workflow_runs.usage_info`` in the shape produced by
``PipelineMetricsAggregator``::

    {
      "llm": {"<processor>|||<model>": {"prompt_tokens": .., "completion_tokens": ..}},
      "tts": {"<processor>|||<model>": <characters>},
      "stt": {"<processor>|||<model>": <seconds>},
      "call_duration_seconds": <seconds>,
    }

Only LLM tokens and TTS characters are actually measured per call today. The
STT bucket exists in the aggregator but nothing ever populates it, and
telephony minutes are not tracked at all — so those components normally yield
no usage here. That is deliberate: the cost engine reports usage it cannot
price rather than inventing a number, and a component with no measurement
simply produces no line. See DASHBOARD.md, "Not built yet".
"""

from __future__ import annotations

from typing import Any

from api.enums import CostComponent
from api.services.billing.cost_engine import UsageItem

# Processor class names carry the provider, e.g. "DeepgramSTTService" or
# "DograhLLMService". Strip the service suffix and lower-case what remains.
# Longest first: "DograhFluxSTTService" must match "FluxSTTService" before the
# shorter "STTService", or the provider comes out as "dograhflux".
_SERVICE_SUFFIXES = (
    "FluxSTTService",
    "LLMService",
    "STTService",
    "TTSService",
    "Service",
)


def provider_from_processor(processor: str) -> str:
    """Best-effort provider name from a pipeline processor class name.

    An unrecognised name is returned lower-cased rather than guessed at. It
    will simply have no rate on file, so the cost engine reports it as uncosted
    instead of pricing it wrongly.
    """
    name = (processor or "").strip()
    for suffix in _SERVICE_SUFFIXES:
        if name.endswith(suffix):
            name = name[: -len(suffix)]
            break
    return name.lower() or "unknown"


def _split_key(key: str) -> str:
    """usage_info keys are ``"<processor>|||<model>"``."""
    return (key or "").split("|||", 1)[0]


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
        tokens = _as_int(value.get("prompt_tokens")) + _as_int(
            value.get("completion_tokens")
        )
        if tokens:
            items.append(
                UsageItem(
                    component=CostComponent.LLM,
                    provider=provider_from_processor(_split_key(key)),
                    quantity=tokens,
                )
            )

    for key, value in _as_mapping(usage_info.get("tts")).items():
        characters = _as_int(value)
        if characters:
            items.append(
                UsageItem(
                    component=CostComponent.TTS,
                    provider=provider_from_processor(_split_key(key)),
                    quantity=characters,
                )
            )

    for key, value in _as_mapping(usage_info.get("stt")).items():
        seconds = _as_int(value)
        if seconds:
            items.append(
                UsageItem(
                    component=CostComponent.STT,
                    provider=provider_from_processor(_split_key(key)),
                    quantity=seconds,
                )
            )

    return tuple(items)


def billable_seconds_from_usage_info(usage_info: dict[str, Any] | None) -> int:
    """Connected call duration in seconds, as recorded by the pipeline."""
    return _as_int(_as_mapping(usage_info).get("call_duration_seconds"))
