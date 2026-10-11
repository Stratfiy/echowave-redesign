"""Every vendor's token usage in one shape.

Each vendor reports what a model call used in its own words, and the same
words do not mean the same thing:

* **Anthropic** (and Claude through AWS) reports ``input_tokens`` *net* of the
  cache: what the cache served is ``cache_read_input_tokens`` and what was
  written into it is ``cache_creation_input_tokens``, both outside the input.
* **Bedrock's Converse** says the same with camelCase names
  (``inputTokens``, ``cacheReadInputTokens``, ``cacheWriteInputTokens``).
* **OpenAI's Chat Completions** reports a ``prompt_tokens`` that already
  *contains* the cached part (``prompt_tokens_details.cached_tokens``), and
  reasoning inside ``completion_tokens``
  (``completion_tokens_details.reasoning_tokens``). Writes are free and not
  reported.
* **OpenAI's Responses API** says the same as ``input_tokens`` /
  ``input_tokens_details`` / ``output_tokens_details``.
* **Gemini** reports ``promptTokenCount`` containing
  ``cachedContentTokenCount``, and thinking (``thoughtsTokenCount``) *beside*
  ``candidatesTokenCount`` rather than inside it -- billed at the output rate.
* **The pipeline** (pipecat's ``LLMTokenUsage``, and ``usage_info["llm"]``)
  carries ``prompt_tokens``/``completion_tokens``/``cache_read_input_tokens``/
  ``cache_creation_input_tokens``/``reasoning_tokens``, where whether the
  prompt contains the cache depends on the vendor behind it
  (``billing.usage._CACHE_OUTSIDE_PROMPT_PROVIDERS``).

:func:`normalise` reads any of these into :class:`NormalisedUsage`, whose
``input_tokens`` is always the *uncached* input, so ``input + cache_read +
cache_write`` is always the whole prompt. :meth:`NormalisedUsage.as_usage_fields`
gives back the four fields ``model_usage`` and ``usage_info["llm"]`` have
always stored, in the vendor's own convention, so the one billing rule in
``usage.llm_split_items`` keeps applying unchanged.

Measures only. Nothing here charges anyone.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from api.services.billing.usage import _CACHE_OUTSIDE_PROMPT_PROVIDERS

#: The payload shapes :func:`normalise` knows, by name. ``auto`` guesses from
#: the keys, which is unambiguous for every vendor payload except the
#: pipeline's own, where the vendor decides the prompt convention.
SHAPES = (
    "anthropic",
    "bedrock",
    "openai",
    "openai_responses",
    "gemini",
    "pipeline",
    "auto",
)


def _count(value: Any) -> int:
    try:
        return max(int(value or 0), 0)
    except (TypeError, ValueError):
        return 0


def _get(payload: Any, *names: str) -> Any:
    """The first of ``names`` present on a dict or an object."""
    for name in names:
        if isinstance(payload, dict):
            if name in payload and payload[name] is not None:
                return payload[name]
        else:
            value = getattr(payload, name, None)
            if value is not None:
                return value
    return None


@dataclass(frozen=True)
class NormalisedUsage:
    """One model call's tokens, in one shape whatever the vendor.

    ``input_tokens`` is the input the cache did not serve; ``cache_read_tokens``
    is what it served and ``cache_write_tokens`` what was written into it.
    Their sum is the whole prompt. ``output_tokens`` includes reasoning;
    ``reasoning_tokens`` says how much of it was reasoning, where the vendor
    said. ``cache_outside_prompt`` records which convention the vendor used,
    so :meth:`as_usage_fields` can give its own numbers back.
    """

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    reasoning_tokens: int = 0
    cache_outside_prompt: bool = False

    @property
    def total_input_tokens(self) -> int:
        """The whole prompt: uncached, served from cache, and written."""
        return self.input_tokens + self.cache_read_tokens + self.cache_write_tokens

    @property
    def is_empty(self) -> bool:
        return not (
            self.input_tokens
            or self.output_tokens
            or self.cache_read_tokens
            or self.cache_write_tokens
        )

    def as_usage_fields(self) -> dict[str, int]:
        """The four fields ``model_usage`` and ``usage_info["llm"]`` store,
        in the vendor's own prompt convention. Cache counts appear only when
        non-zero, exactly as the builder client has always written them."""
        prompt = (
            self.input_tokens
            if self.cache_outside_prompt
            else self.input_tokens + self.cache_read_tokens + self.cache_write_tokens
        )
        out = {"prompt_tokens": prompt, "completion_tokens": self.output_tokens}
        if self.cache_read_tokens:
            out["cache_read_input_tokens"] = self.cache_read_tokens
        if self.cache_write_tokens:
            out["cache_creation_input_tokens"] = self.cache_write_tokens
        return out

    def as_dict(self) -> dict[str, int]:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cache_read_tokens": self.cache_read_tokens,
            "cache_write_tokens": self.cache_write_tokens,
            "reasoning_tokens": self.reasoning_tokens,
        }


def _guess(payload: Any) -> str:
    if _get(payload, "cache_creation_input_tokens") is not None and (
        _get(payload, "input_tokens") is not None
    ):
        return "anthropic"
    if _get(payload, "inputTokens", "cacheReadInputTokens") is not None:
        return "bedrock"
    if (
        _get(payload, "promptTokenCount", "prompt_token_count", "candidatesTokenCount")
        is not None
    ):
        return "gemini"
    if _get(payload, "input_tokens_details", "output_tokens_details") is not None:
        return "openai_responses"
    if _get(payload, "prompt_tokens_details", "completion_tokens_details") is not None:
        return "openai"
    if _get(payload, "input_tokens") is not None:
        # Anthropic with no cache activity reports no cache fields at all.
        return "anthropic"
    return "pipeline"


def normalise(
    payload: Any, *, shape: str = "auto", provider: str | None = None
) -> NormalisedUsage | None:
    """``payload`` -- a vendor's usage object or dict -- in one shape.

    ``shape`` names the vendor's format (see :data:`SHAPES`); ``auto`` reads
    it from the keys. ``provider`` is needed only for the ``pipeline`` shape,
    where the vendor behind the numbers decides whether the prompt count
    contains the cache. ``None`` for no payload at all.
    """
    if payload is None or isinstance(payload, (str, bytes, int, float)):
        return None
    if shape not in SHAPES:
        raise ValueError(f"Unknown usage shape {shape!r}")
    if shape == "auto":
        shape = _guess(payload)

    if shape == "anthropic":
        return NormalisedUsage(
            input_tokens=_count(_get(payload, "input_tokens")),
            output_tokens=_count(_get(payload, "output_tokens")),
            cache_read_tokens=_count(_get(payload, "cache_read_input_tokens")),
            cache_write_tokens=_count(_get(payload, "cache_creation_input_tokens")),
            cache_outside_prompt=True,
        )
    if shape == "bedrock":
        return NormalisedUsage(
            input_tokens=_count(_get(payload, "inputTokens", "input_tokens")),
            output_tokens=_count(_get(payload, "outputTokens", "output_tokens")),
            cache_read_tokens=_count(_get(payload, "cacheReadInputTokens")),
            cache_write_tokens=_count(_get(payload, "cacheWriteInputTokens")),
            cache_outside_prompt=True,
        )
    if shape == "openai":
        prompt = _count(_get(payload, "prompt_tokens"))
        details = _get(payload, "prompt_tokens_details") or {}
        cached = _count(_get(details, "cached_tokens"))
        out_details = _get(payload, "completion_tokens_details") or {}
        return NormalisedUsage(
            input_tokens=max(prompt - cached, 0),
            output_tokens=_count(_get(payload, "completion_tokens")),
            cache_read_tokens=min(cached, prompt) if prompt else cached,
            reasoning_tokens=_count(_get(out_details, "reasoning_tokens")),
        )
    if shape == "openai_responses":
        prompt = _count(_get(payload, "input_tokens"))
        details = _get(payload, "input_tokens_details") or {}
        cached = _count(_get(details, "cached_tokens"))
        out_details = _get(payload, "output_tokens_details") or {}
        return NormalisedUsage(
            input_tokens=max(prompt - cached, 0),
            output_tokens=_count(_get(payload, "output_tokens")),
            cache_read_tokens=min(cached, prompt) if prompt else cached,
            reasoning_tokens=_count(_get(out_details, "reasoning_tokens")),
        )
    if shape == "gemini":
        prompt = _count(_get(payload, "promptTokenCount", "prompt_token_count"))
        cached = _count(
            _get(payload, "cachedContentTokenCount", "cached_content_token_count")
        )
        thoughts = _count(_get(payload, "thoughtsTokenCount", "thoughts_token_count"))
        candidates = _count(
            _get(payload, "candidatesTokenCount", "candidates_token_count")
        )
        return NormalisedUsage(
            input_tokens=max(prompt - cached, 0),
            # Thinking is billed at the output rate and reported beside the
            # candidates, not inside them.
            output_tokens=candidates + thoughts,
            cache_read_tokens=min(cached, prompt) if prompt else cached,
            reasoning_tokens=thoughts,
        )

    # pipeline: the vendor behind it decides the prompt convention.
    outside = (provider or "") in _CACHE_OUTSIDE_PROMPT_PROVIDERS
    prompt = _count(_get(payload, "prompt_tokens"))
    cached = _count(_get(payload, "cache_read_input_tokens"))
    written = _count(_get(payload, "cache_creation_input_tokens"))
    if outside:
        uncached = prompt
    else:
        # The vendor's prompt contains whatever its cache served; a write is
        # not reported separately and is already inside the prompt.
        uncached = max(prompt - cached, 0)
        written = 0
    return NormalisedUsage(
        input_tokens=uncached,
        output_tokens=_count(_get(payload, "completion_tokens")),
        cache_read_tokens=cached,
        cache_write_tokens=written,
        reasoning_tokens=_count(_get(payload, "reasoning_tokens")),
        cache_outside_prompt=outside,
    )


__all__ = ["SHAPES", "NormalisedUsage", "normalise"]
