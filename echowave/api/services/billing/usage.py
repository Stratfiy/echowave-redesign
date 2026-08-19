"""Turn a workflow run's recorded usage into costable line items.

The pipeline writes ``workflow_runs.usage_info`` in the shape produced by
``PipelineMetricsAggregator``::

    {
      "llm": {"<processor>|||<model>": {"prompt_tokens": .., "completion_tokens": ..}},
      "tts": {"<processor>|||<model>": <characters>},
      "stt": {"<processor>|||<model>": <seconds>},
      "telephony": {"<provider>": <connected seconds>},
      "call_duration_seconds": <seconds>,
      "key_sources": {"llm"|"stt"|"tts": "byok"|"managed"},
    }

All four cost components are measured: LLM tokens and TTS characters from the
pipeline's metrics aggregator, STT seconds from the same aggregator's
transcription frames, and telephony seconds from the provider status callback.

A component the pipeline did not record simply produces no line. That is
deliberate — the cost engine reports usage it cannot price rather than
inventing a number.

``key_sources`` records, per component, whether the call ran on the account's
own provider key. A "byok" component produces no line at all here (see
``usage_items_from_usage_info``) — the account already paid that vendor
directly, so a Decibyl receipt for the same usage would be a double charge.
Telephony has no key-ownership concept and always produces a line regardless.
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


def key_sources_from_usage_info(usage_info: dict[str, Any] | None) -> dict[str, str]:
    """Which components on this run used the account's own key.

    ``{"llm"|"stt"|"tts": "byok"|"managed"}``, as recorded by the pipeline at
    call start (see ``PipelineMetricsAggregator.register_key_sources``). A
    component absent from this mapping -- calls made before this was tracked,
    or telephony, which has no key-ownership concept -- is treated as managed
    everywhere it's consulted below. That is the safe default: it reproduces
    the old behaviour of charging for it rather than silently discounting
    usage no one confirmed was BYOK.
    """
    return {
        k: v
        for k, v in _as_mapping(_as_mapping(usage_info).get("key_sources")).items()
        if v in ("byok", "managed")
    }


def byok_platform_tier(usage_info: dict[str, Any] | None) -> str:
    """Which BYOK tier this call falls in: ``managed``, ``stt`` or ``tts``.

    The tier decides how much the platform fee is uplifted, and it is cut on
    **which** component the customer brought rather than **how many**. That is
    the whole point: the three are not worth remotely the same to us. On a
    typical Indic minute the markup margin is about $0.014 on speech synthesis,
    $0.002 on transcription and $0.0005 on the language model. Counting
    components would price a customer who brings their cheap LLM key the same
    as one who brings the expensive voice.

    * ``tts``     — the customer's own voice. The expensive one, and the only
                    case that materially changes what we earn on a call.
    * ``stt``     — the customer's own transcription, with the voice still
                    ours.
    * ``managed`` — everything we care about is ours.

    The language model is deliberately not a tier. It is worth about a
    twentieth of a cent, so charging for it would cost the account more in fee
    than we lose in margin — the arithmetic that made the customer's bill go
    *up* when they brought their own key, which is not a bill anyone can
    defend.

    A run whose key sources were never recorded reads as ``managed``. That is
    the safe direction: it bills what the account was already paying rather
    than inventing an uplift from facts nobody captured.
    """
    key_sources = key_sources_from_usage_info(usage_info)
    if key_sources.get("tts") == "byok":
        return "tts"
    if key_sources.get("stt") == "byok":
        return "stt"
    return "managed"


def usage_items_from_usage_info(
    usage_info: dict[str, Any] | None,
) -> tuple[UsageItem, ...]:
    """Extract costable usage from a run's ``usage_info``.

    LLM quantity is total tokens (prompt + completion); TTS is characters; STT
    is seconds. Zero-quantity entries are dropped — a line item costing nothing
    only adds noise to a receipt.

    A component the account ran on its own key produces no line at all: it
    already paid the vendor directly, so pricing it again here would charge
    twice for the same usage. See ``key_sources_from_usage_info``.
    """
    usage_info = usage_info or {}
    key_sources = key_sources_from_usage_info(usage_info)
    items: list[UsageItem] = []

    if key_sources.get("llm") != "byok":
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

    if key_sources.get("tts") != "byok":
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

    if key_sources.get("stt") != "byok":
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
    # by provider — there is no model dimension to a phone call, and no BYOK
    # concept either: Decibyl always fronts the telephony cost.
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
