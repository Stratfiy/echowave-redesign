"""One tool-calling loop over three vendors that disagree about everything.

The builder has to work on whichever key the operator installed, so the chat
loop cannot be written against one vendor's SDK. This module is the seam: a
single :class:`BuilderTurn` in, a single :class:`ModelReply` out, and three
adapters that translate.

The three wire formats differ in every part that matters, and each difference
below has bitten somebody:

**Where the system prompt goes.** Anthropic takes a top-level ``system``;
OpenAI wants a message with ``role: "system"`` in the list; Gemini calls it
``systemInstruction`` and puts it beside ``contents`` rather than inside.

**What a tool result is.** Anthropic sends results back as a ``user`` message
containing ``tool_result`` blocks. OpenAI has a dedicated ``tool`` role keyed
by ``tool_call_id``. Gemini uses a ``function`` role carrying a
``functionResponse`` whose payload must be an object — a bare string is
rejected.

**How arguments arrive.** Anthropic and Gemini hand back parsed objects;
OpenAI hands back a JSON *string* that has to be decoded, and which is
occasionally malformed when the model is cut off mid-call.

**What the schema may contain.** Gemini rejects several JSON Schema keywords
the other two accept, so tool schemas are stripped for it rather than written
to its lowest common denominator everywhere.

No vendor SDKs: the repository already talks to model vendors over httpx, and
three SDKs for three shapes of the same request would be three more things to
keep on their release trains. The one exception is Claude on AWS
(``CLAUDE_BACKEND``, ``services/aws_gateway``): SigV4 signing is the SDK's
job, so Claude Platform on AWS and Bedrock go through ``anthropic``'s own AWS
clients with the same Messages body this module already builds.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field, replace
from typing import Any

import httpx
from loguru import logger

from api.services.aws_gateway import claude as aws_claude
from api.services.billing import cache_metrics, llm_usage, model_usage

#: Vendors this loop can drive. Values match the provider names used in the
#: platform credential store, so the key an operator installs selects the
#: adapter with no mapping table in between.
ANTHROPIC = "anthropic"
OPENAI = "openai"
GOOGLE = "google"

SUPPORTED_PROVIDERS = (ANTHROPIC, OPENAI, GOOGLE)

#: Long enough for a model that is thinking, short enough that a wedged request
#: does not hold a worker for the length of a session.
_TIMEOUT_SECONDS = 120.0

#: Anthropic requires this header and versions its API through it rather than
#: through the URL.
_ANTHROPIC_VERSION = "2023-06-01"

#: A ceiling on the reply, not a target. The builder answers in prose and tool
#: calls, neither of which is long; the limit exists so a runaway generation
#: costs one bounded request instead of an open-ended one.
_MAX_OUTPUT_TOKENS = 4096

#: JSON Schema keywords Gemini's function declarations reject. Stripped rather
#: than avoided everywhere, so Anthropic and OpenAI still get the fuller schema
#: and validate arguments better.
_GEMINI_UNSUPPORTED_SCHEMA_KEYS = frozenset(
    {"additionalProperties", "$schema", "title", "default", "examples"}
)


class BuilderClientError(RuntimeError):
    """The model could not be reached, or replied with something unusable."""


class ProviderOutOfCredit(BuilderClientError):
    """The vendor refused the turn because the account behind the key has no
    credit left -- Anthropic's "credit balance is too low", OpenAI's
    ``insufficient_quota``, Google's billing quota. Not a rate limit: waiting
    does not fix it, a top-up or another vendor does."""

    def __init__(self, provider: str):
        super().__init__(
            "The AI provider's account is out of credit. An administrator needs "
            "to top it up, or add another provider key in the provider keys screen."
        )
        self.provider = provider


class ProviderRateLimited(BuilderClientError):
    """The vendor refused the turn for sending too much too fast (HTTP 429).

    A starter-tier key allows only so many tokens a minute, and one Decibyl
    turn with a large attachment and a long thread can be most of a minute's
    allowance. Waiting a few seconds usually clears it; so does another
    vendor.
    """

    def __init__(self, provider: str, retry_after: float | None = None):
        super().__init__("The assistant is rate limited right now. Try again shortly.")
        self.provider = provider
        self.retry_after = retry_after


#: The longest a turn waits in place for a rate limit to clear before it
#: tries another vendor instead. The person is watching the reply form.
RATE_LIMIT_WAIT_SECONDS = 8.0
#: The longest a turn waits when no other vendor can answer it. A slow reply
#: beats an error, and per-minute token limits clear within a minute.
RATE_LIMIT_LAST_WAIT_SECONDS = 30.0
#: How long a vendor that rate-limited a turn is tried last.
RATE_LIMITED_FOR_SECONDS = 2 * 60


def _retry_after(headers: Any) -> float | None:
    """Seconds until the vendor will take the request: ``retry-after``, or
    OpenAI's ``x-ratelimit-reset-*`` ("18.008s", "1m30s", "250ms"), which it
    sends on a tokens-per-minute limit instead."""
    if headers is None:
        return None
    try:
        value = headers.get("retry-after")
        if value is not None:
            return float(value)
    except (TypeError, ValueError):
        pass
    waits = [
        _duration(headers.get(name))
        for name in ("x-ratelimit-reset-tokens", "x-ratelimit-reset-requests")
    ]
    known = [w for w in waits if w is not None]
    return max(known) if known else None


_DURATION_PART = re.compile(r"(\d+(?:\.\d+)?)(ms|s|m|h)")
_DURATION_UNIT = {"ms": 0.001, "s": 1.0, "m": 60.0, "h": 3600.0}


def _duration(value: Any) -> float | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    parts = _DURATION_PART.findall(text)
    if not parts or "".join(n + u for n, u in parts) != text:
        return None
    return sum(float(n) * _DURATION_UNIT[u] for n, u in parts)


#: How long a vendor that said "out of credit" is skipped before it is tried
#: again. Long enough not to pay a failed request on every turn, short enough
#: that a top-up is picked up without a deploy.
EXHAUSTED_FOR_SECONDS = 30 * 60
_exhausted_until: dict[str, float] = {}

_CREDIT_PHRASES = (
    "credit balance is too low",
    "insufficient_quota",
    "exceeded your current quota",
    "billing",
    "purchase credits",
)


def _out_of_credit(status: int, body: str) -> bool:
    if status not in (400, 402, 403, 429):
        return False
    text = (body or "").lower()
    return any(phrase in text for phrase in _CREDIT_PHRASES)


def mark_exhausted(provider: str, seconds: float = EXHAUSTED_FOR_SECONDS) -> None:
    until = time.monotonic() + seconds
    # Never shorten a longer mark: out of credit outlasts a rate limit.
    _exhausted_until[provider] = max(until, _exhausted_until.get(provider, 0.0))


def is_exhausted(provider: str) -> bool:
    until = _exhausted_until.get(provider)
    if until is None:
        return False
    if time.monotonic() >= until:
        _exhausted_until.pop(provider, None)
        return False
    return True


async def _fallback_model(provider: str, api_key: str) -> tuple[str, str, str] | None:
    """Another vendor the platform holds a key for, as (provider, model, key).

    Only for a turn that ran on the platform's own key: a workspace that
    brought its own key (BYOK) is never moved onto Decibyl's keys here,
    because that would bill it for a turn it asked to run on its own
    account. None when there is nowhere to go.
    """
    from api import constants
    from api.db import db_client
    from api.enums import CostComponent
    from api.services.configuration import platform_credentials

    try:
        async with db_client.async_session() as session:
            own = await platform_credentials.resolve_api_key(
                session, component=CostComponent.LLM, provider=provider
            )
            if own != api_key and not aws_claude.is_aws_key(api_key):
                return None
            order = [
                p for p in constants.AGENT_BUILDER_PROVIDER_PREFERENCE if p != provider
            ]
            order += [
                p for p in SUPPORTED_PROVIDERS if p not in order and p != provider
            ]
            for candidate in order:
                if candidate not in SUPPORTED_PROVIDERS or is_exhausted(candidate):
                    continue
                model = constants.AGENT_BUILDER_MODELS.get(candidate)
                if not model:
                    continue
                key = (
                    aws_claude.platform_credential(model)
                    if candidate == ANTHROPIC
                    else None
                ) or await platform_credentials.resolve_api_key(
                    session, component=CostComponent.LLM, provider=candidate
                )
                if key:
                    return candidate, model, key
    except Exception as exc:  # noqa: BLE001 - no fallback is the old behaviour
        logger.warning("Could not look for a fallback to {}: {}", provider, exc)
    return None


@dataclass(frozen=True)
class ToolCall:
    """A tool the model wants run, normalised across vendors."""

    #: Vendor-assigned identifier, needed to correlate the result back. Gemini
    #: assigns none, so the adapter synthesises one from the tool name.
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class ModelReply:
    """What came back: prose, tool calls, or both."""

    text: str
    tool_calls: tuple[ToolCall, ...] = ()
    #: What the vendor said the call used, in the pipeline's shape
    #: (``prompt_tokens``, ``completion_tokens``, and the cache counts when
    #: there were any). None when the vendor said nothing, which is not the
    #: same as saying nothing was used.
    usage: dict[str, int] | None = None
    #: The backup model's id when the fallback brain answered instead of the
    #: model asked for (``services/aws_gateway/fallback.py``); empty otherwise.
    fallback_model: str = ""

    @property
    def wants_tools(self) -> bool:
        return bool(self.tool_calls)


@dataclass
class Conversation:
    """The running transcript, in this module's own shape.

    Kept vendor-neutral so a session is not tied to the provider it started on
    — an operator who swaps the installed key mid-session gets a working
    conversation rather than a decode error.

    Entries are dicts with a ``role`` of ``user``, ``assistant`` or ``tool``.
    Assistant entries may carry ``tool_calls``; tool entries carry
    ``tool_call_id``, ``name`` and ``content``.
    """

    messages: list[dict[str, Any]] = field(default_factory=list)

    def add_user(self, text: str) -> None:
        self.messages.append({"role": "user", "content": text})

    def add_assistant(self, reply: ModelReply) -> None:
        entry: dict[str, Any] = {"role": "assistant", "content": reply.text}
        if reply.tool_calls:
            entry["tool_calls"] = [
                {"id": c.id, "name": c.name, "arguments": c.arguments}
                for c in reply.tool_calls
            ]
        self.messages.append(entry)

    def add_tool_result(self, call: ToolCall, result: Any) -> None:
        self.messages.append(
            {
                "role": "tool",
                "tool_call_id": call.id,
                "name": call.name,
                "content": result,
            }
        )


# --- schema helpers ---------------------------------------------------------


def _strip_for_gemini(schema: Any) -> Any:
    """Remove keywords Gemini's function declarations reject.

    Recursive because the offending keys appear on nested object properties,
    not only at the top level — which is where this was first wrong.

    The keys of a ``properties`` map are field names, not keywords: a field
    called ``title`` or ``default`` (a calendar event's title, say) is kept.
    Stripping it left ``required`` naming a field that no longer existed, and
    Gemini refuses the whole request for that.
    """
    if isinstance(schema, dict):
        stripped: dict[str, Any] = {}
        for key, value in schema.items():
            if key in _GEMINI_UNSUPPORTED_SCHEMA_KEYS:
                continue
            if key == "properties" and isinstance(value, dict):
                stripped[key] = {
                    name: _strip_for_gemini(field) for name, field in value.items()
                }
            else:
                stripped[key] = _strip_for_gemini(value)
        # A connected app's schema can also list a required field it never
        # defines. Anthropic and OpenAI let that pass; Gemini does not.
        required = stripped.get("required")
        if isinstance(required, list):
            defined = stripped.get("properties")
            kept = [
                name
                for name in required
                if isinstance(defined, dict) and name in defined
            ]
            if kept:
                stripped["required"] = kept
            else:
                del stripped["required"]
        return stripped
    if isinstance(schema, list):
        return [_strip_for_gemini(item) for item in schema]
    return schema


#: The key a tool result carries pictures under: a list of
#: ``{"media_type": "image/png", "data": <base64>, "label": "..."}``. Studio's
#: design review returns screenshots this way. Each adapter below sends them
#: the way its vendor accepts pictures, and never as text.
IMAGES_KEY = "_images"


def _split_images(content: Any) -> tuple[Any, list[dict[str, Any]]]:
    """``content`` without its pictures, and the pictures."""
    if isinstance(content, dict) and isinstance(content.get(IMAGES_KEY), list):
        images = [
            image
            for image in content[IMAGES_KEY]
            if isinstance(image, dict) and image.get("data") and image.get("media_type")
        ]
        rest = {key: value for key, value in content.items() if key != IMAGES_KEY}
        return rest, images
    return content, []


def _as_text(content: Any) -> str:
    """A tool result as a string, for vendors that will not take an object."""
    content, _ = _split_images(content)
    if isinstance(content, str):
        return content
    try:
        return json.dumps(content, default=str)
    except (TypeError, ValueError):
        return str(content)


# --- Anthropic --------------------------------------------------------------


def _anthropic_request(
    *, model: str, system: str, conversation: Conversation, tools: list[dict[str, Any]]
) -> dict[str, Any]:
    messages: list[dict[str, Any]] = []
    for entry in conversation.messages:
        role = entry["role"]
        if role == "user":
            messages.append({"role": "user", "content": entry["content"]})
        elif role == "assistant":
            blocks: list[dict[str, Any]] = []
            if entry.get("content"):
                blocks.append({"type": "text", "text": entry["content"]})
            for call in entry.get("tool_calls", []):
                blocks.append(
                    {
                        "type": "tool_use",
                        "id": call["id"],
                        "name": call["name"],
                        "input": call["arguments"],
                    }
                )
            # An assistant turn with neither text nor tool calls is not a legal
            # message; skip rather than send an empty content array.
            if blocks:
                messages.append({"role": "assistant", "content": blocks})
        elif role == "tool":
            # Tool results come back as a *user* turn here, which is the
            # difference most likely to be got wrong when porting from OpenAI.
            _, images = _split_images(entry["content"])
            result: Any = _as_text(entry["content"])
            if images:
                # A tool result may hold pictures directly.
                result = [{"type": "text", "text": result}] + [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": image["media_type"],
                            "data": image["data"],
                        },
                    }
                    for image in images
                ]
            messages.append(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": entry["tool_call_id"],
                            "content": result,
                        }
                    ],
                }
            )

    payload: dict[str, Any] = {
        "model": model,
        "max_tokens": _MAX_OUTPUT_TOKENS,
        # Marked cacheable rather than sent as a bare string. The system
        # prompt is the one part of this request that is byte-identical on
        # every turn of every thread in every account, so it is the block
        # with the most to gain and nothing to lose: a cache miss costs the
        # same as the string did.
        #
        # Only this block. The per-turn context -- the team, the memory, the
        # knowledge base -- rides in the user message and changes every turn,
        # and marking something that changes buys a write and never a read.
        "system": [
            {
                "type": "text",
                "text": system,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        "messages": messages,
    }
    if tools:
        payload["tools"] = [
            {
                "name": t["name"],
                "description": t["description"],
                "input_schema": t["parameters"],
            }
            for t in tools
        ]
    if _cache_v2():
        _mark_conversation_tail(messages)
    return payload


def _cache_v2() -> bool:
    """Whether ``cache_v2`` is on for the account this call is for."""
    from api.services import features

    organization_id, _ = model_usage.current()
    try:
        return features.is_on("cache_v2", organization_id)
    except Exception:  # noqa: BLE001 - unknown is off: the request as before
        return False


def _mark_conversation_tail(messages: list[dict[str, Any]]) -> None:
    """A second cache breakpoint, on the last block of the last message
    (``cache_v2``).

    The system block's breakpoint caches the tools and the system prompt and
    nothing after them. Decibyl's per-turn context rides in the latest user
    message, and a turn that reads a connected app sends that same message
    again on every round -- re-billed in full each time. A breakpoint at the
    tail lets the next round read everything up to here from the cache. The
    cost is a cache write on a turn that has no second round, which is why
    this is behind a flag and measured before it is on anywhere.

    The model sees exactly the same content; only the request is marked.
    """
    if not messages:
        return
    last = messages[-1]
    content = last.get("content")
    if isinstance(content, str):
        if not content.strip():
            return
        last["content"] = [
            {"type": "text", "text": content, "cache_control": {"type": "ephemeral"}}
        ]
        return
    if isinstance(content, list) and content and isinstance(content[-1], dict):
        block = content[-1]
        if block.get("type") == "text" and not str(block.get("text") or "").strip():
            return
        # A new list: a user turn's list is the conversation's own object,
        # and marking it there would carry the mark into the next request.
        last["content"] = [
            *content[:-1],
            {**block, "cache_control": {"type": "ephemeral"}},
        ]


# --- usage ------------------------------------------------------------------
#
# Each vendor reports usage its own way. All three are read through the one
# normaliser (``billing.llm_usage``) and handed back in the shape the pipeline
# writes to ``usage_info["llm"]``, so the one vendor rule in
# ``billing.usage.llm_split_items`` -- Anthropic's input is net of its cache,
# OpenAI's and Google's include it -- splits both the same way.


def _count(value: Any) -> int:
    try:
        return max(int(value or 0), 0)
    except (TypeError, ValueError):
        return 0


def _usage_fields(usage: Any, shape: str) -> dict[str, int] | None:
    if not isinstance(usage, dict):
        return None
    normalised = llm_usage.normalise(usage, shape=shape)
    return normalised.as_usage_fields() if normalised is not None else None


def _anthropic_usage(usage: Any) -> dict[str, int] | None:
    return _usage_fields(usage, "anthropic")


def _openai_usage(usage: Any) -> dict[str, int] | None:
    return _usage_fields(usage, "openai")


def _gemini_usage(usage: Any) -> dict[str, int] | None:
    # Thinking is billed at the output rate and reported beside the
    # candidates, not inside them; the normaliser adds it to the output.
    return _usage_fields(usage, "gemini")


#: Where each vendor's reply body keeps its usage, and in which shape.
_BODY_USAGE = {
    ANTHROPIC: ("usage", "anthropic"),
    OPENAI: ("usage", "openai"),
    GOOGLE: ("usageMetadata", "gemini"),
}


def _normalised_from_body(provider: str, body: Any) -> llm_usage.NormalisedUsage | None:
    key, shape = _BODY_USAGE.get(provider, ("usage", "auto"))
    raw = body.get(key) if isinstance(body, dict) else None
    return llm_usage.normalise(raw, shape=shape) if isinstance(raw, dict) else None


async def _record_usage(
    *,
    provider: str,
    model: str,
    usage: dict[str, int] | None,
    normalised: llm_usage.NormalisedUsage | None,
    system: str,
    tools: list[dict[str, Any]] | None,
) -> None:
    """Both records of one call: ``model_usage`` (what it used, as it always
    has been) and ``llm_call_usage`` (what prompt it sent and what the cache
    did with it). Neither ever raises."""
    await model_usage.record(provider=provider, model=model, usage=usage)
    if normalised is None and usage:
        normalised = llm_usage.normalise(usage, shape="pipeline", provider=provider)
    await cache_metrics.record_direct(
        provider=provider,
        model=model,
        usage=normalised,
        system=system,
        tools=tools,
    )


def _anthropic_parse(body: dict[str, Any]) -> ModelReply:
    text_parts: list[str] = []
    calls: list[ToolCall] = []
    for block in body.get("content", []) or []:
        if block.get("type") == "text":
            text_parts.append(block.get("text", ""))
        elif block.get("type") == "tool_use":
            calls.append(
                ToolCall(
                    id=block.get("id", ""),
                    name=block.get("name", ""),
                    arguments=block.get("input") or {},
                )
            )
    return ModelReply(
        text="".join(text_parts).strip(),
        tool_calls=tuple(calls),
        usage=_anthropic_usage(body.get("usage")),
    )


# --- OpenAI -----------------------------------------------------------------


def _openai_request(
    *, model: str, system: str, conversation: Conversation, tools: list[dict[str, Any]]
) -> dict[str, Any]:
    messages: list[dict[str, Any]] = [{"role": "system", "content": system}]
    # A tool message cannot hold a picture, and nothing but tool messages may
    # follow the assistant turn that called them -- so pictures wait here and
    # go in one user turn after the last result of the round.
    pending: list[dict[str, Any]] = []

    def flush() -> None:
        if not pending:
            return
        parts: list[dict[str, Any]] = [
            {"type": "text", "text": "The pictures the tool results above refer to."}
        ]
        parts += [
            {
                "type": "image_url",
                "image_url": {
                    "url": f"data:{image['media_type']};base64,{image['data']}"
                },
            }
            for image in pending
        ]
        messages.append({"role": "user", "content": parts})
        pending.clear()

    for entry in conversation.messages:
        role = entry["role"]
        if role != "tool":
            flush()
        if role == "user":
            messages.append({"role": "user", "content": entry["content"]})
        elif role == "assistant":
            message: dict[str, Any] = {
                "role": "assistant",
                "content": entry.get("content") or None,
            }
            if entry.get("tool_calls"):
                message["tool_calls"] = [
                    {
                        "id": call["id"],
                        "type": "function",
                        "function": {
                            "name": call["name"],
                            # Arguments go back as a JSON *string*, the same
                            # way they arrived.
                            "arguments": json.dumps(call["arguments"], default=str),
                        },
                    }
                    for call in entry["tool_calls"]
                ]
            messages.append(message)
        elif role == "tool":
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": entry["tool_call_id"],
                    "content": _as_text(entry["content"]),
                }
            )
            pending.extend(_split_images(entry["content"])[1])
    flush()

    payload: dict[str, Any] = {
        "model": model,
        "max_completion_tokens": _MAX_OUTPUT_TOKENS,
        "messages": messages,
    }
    if tools:
        payload["tools"] = [
            {
                "type": "function",
                "function": {
                    "name": t["name"],
                    "description": t["description"],
                    "parameters": t["parameters"],
                },
            }
            for t in tools
        ]
    return payload


def _openai_parse(body: dict[str, Any]) -> ModelReply:
    choices = body.get("choices") or []
    if not choices:
        raise BuilderClientError("OpenAI returned no choices")
    message = choices[0].get("message") or {}

    calls: list[ToolCall] = []
    for call in message.get("tool_calls") or []:
        function = call.get("function") or {}
        raw = function.get("arguments") or "{}"
        try:
            arguments = json.loads(raw) if isinstance(raw, str) else (raw or {})
        except json.JSONDecodeError:
            # A truncated generation produces unparseable arguments. Passing an
            # empty object lets the tool reject it with a message the model can
            # act on, which recovers; raising here would end the session.
            logger.warning(
                "OpenAI tool call {} had unparseable arguments; treating as empty",
                function.get("name"),
            )
            arguments = {}
        calls.append(
            ToolCall(
                id=call.get("id", ""),
                name=function.get("name", ""),
                arguments=arguments if isinstance(arguments, dict) else {},
            )
        )

    return ModelReply(
        text=(message.get("content") or "").strip(),
        tool_calls=tuple(calls),
        usage=_openai_usage(body.get("usage")),
    )


# --- Gemini -----------------------------------------------------------------


def _gemini_request(
    *, system: str, conversation: Conversation, tools: list[dict[str, Any]]
) -> dict[str, Any]:
    contents: list[dict[str, Any]] = []
    # Same as OpenAI's: pictures follow the round's function responses as one
    # user turn of inline data.
    pending: list[dict[str, Any]] = []

    def flush() -> None:
        if not pending:
            return
        contents.append(
            {
                "role": "user",
                "parts": [{"text": "The pictures the tool results above refer to."}]
                + [
                    {
                        "inlineData": {
                            "mimeType": image["media_type"],
                            "data": image["data"],
                        }
                    }
                    for image in pending
                ],
            }
        )
        pending.clear()

    for entry in conversation.messages:
        role = entry["role"]
        if role != "tool":
            flush()
        if role == "user":
            contents.append({"role": "user", "parts": [{"text": entry["content"]}]})
        elif role == "assistant":
            parts: list[dict[str, Any]] = []
            if entry.get("content"):
                parts.append({"text": entry["content"]})
            for call in entry.get("tool_calls", []):
                parts.append(
                    {
                        "functionCall": {
                            "name": call["name"],
                            "args": call["arguments"],
                        }
                    }
                )
            if parts:
                contents.append({"role": "model", "parts": parts})
        elif role == "tool":
            content, images = _split_images(entry["content"])
            pending.extend(images)
            contents.append(
                {
                    "role": "function",
                    "parts": [
                        {
                            "functionResponse": {
                                "name": entry["name"],
                                # Must be an object. A bare string is rejected,
                                # so anything scalar is wrapped.
                                "response": (
                                    content
                                    if isinstance(content, dict)
                                    else {"result": _as_text(content)}
                                ),
                            }
                        }
                    ],
                }
            )

    flush()
    payload: dict[str, Any] = {
        "contents": contents,
        "systemInstruction": {"parts": [{"text": system}]},
        "generationConfig": {"maxOutputTokens": _MAX_OUTPUT_TOKENS},
    }
    if tools:
        payload["tools"] = [
            {
                "functionDeclarations": [
                    {
                        "name": t["name"],
                        "description": t["description"],
                        "parameters": _strip_for_gemini(t["parameters"]),
                    }
                    for t in tools
                ]
            }
        ]
    return payload


def _gemini_parse(body: dict[str, Any]) -> ModelReply:
    candidates = body.get("candidates") or []
    if not candidates:
        raise BuilderClientError("Gemini returned no candidates")
    parts = (candidates[0].get("content") or {}).get("parts") or []

    text_parts: list[str] = []
    calls: list[ToolCall] = []
    for index, part in enumerate(parts):
        if "text" in part:
            text_parts.append(part["text"])
        elif "functionCall" in part:
            call = part["functionCall"]
            name = call.get("name", "")
            calls.append(
                ToolCall(
                    # Gemini assigns no call id, so one is synthesised. It only
                    # has to be unique within the turn, which is what the index
                    # guarantees.
                    id=f"{name}-{index}",
                    name=name,
                    arguments=call.get("args") or {},
                )
            )
    return ModelReply(
        text="".join(text_parts).strip(),
        tool_calls=tuple(calls),
        usage=_gemini_usage(body.get("usageMetadata")),
    )


# --- the seam ---------------------------------------------------------------


async def _complete_once(
    *,
    provider: str,
    model: str,
    api_key: str,
    system: str,
    conversation: Conversation,
    tools: list[dict[str, Any]],
) -> ModelReply:
    """One turn against whichever vendor's key is installed.

    ``tools`` are in OpenAI's shape — ``{"name", "description", "parameters"}``
    — because it is the one all three can be derived from without loss.

    Raises :class:`BuilderClientError` for anything the caller should show the
    user rather than retry blindly. The vendor's own error text is logged but
    never returned: it can quote the request, and the request contains the
    account's prompts.
    """
    if provider == ANTHROPIC and aws_claude.is_aws_key(api_key):
        return await _aws_complete_once(
            model=model,
            api_key=api_key,
            system=system,
            conversation=conversation,
            tools=tools,
        )
    if provider == ANTHROPIC:
        url = "https://api.anthropic.com/v1/messages"
        headers = {
            "x-api-key": api_key,
            "anthropic-version": _ANTHROPIC_VERSION,
            "content-type": "application/json",
        }
        payload = _anthropic_request(
            model=model, system=system, conversation=conversation, tools=tools
        )
        parse = _anthropic_parse
    elif provider == OPENAI:
        url = "https://api.openai.com/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "content-type": "application/json",
        }
        payload = _openai_request(
            model=model, system=system, conversation=conversation, tools=tools
        )
        parse = _openai_parse
    elif provider == GOOGLE:
        # The key rides in a header rather than the query string so it does not
        # land in access logs or a proxy's URL history.
        url = (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"{model}:generateContent"
        )
        headers = {"x-goog-api-key": api_key, "content-type": "application/json"}
        payload = _gemini_request(system=system, conversation=conversation, tools=tools)
        parse = _gemini_parse
    else:
        raise BuilderClientError(f"Unsupported builder provider {provider!r}")

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as client:
            response = await client.post(url, headers=headers, json=payload)
    except httpx.HTTPError as exc:
        logger.error("Agent builder could not reach {}: {}", provider, exc)
        raise BuilderClientError(
            "The assistant could not be reached just now. Try again in a moment."
        ) from exc

    if response.status_code >= 400:
        # Logged in full, returned as a summary: the vendor's message can quote
        # the request body back, and the request body is the account's prompts.
        logger.error(
            "Agent builder {} returned {}: {}",
            provider,
            response.status_code,
            response.text[:2000],
        )
        if _out_of_credit(response.status_code, response.text):
            raise ProviderOutOfCredit(provider)
        if response.status_code == 429:
            raise ProviderRateLimited(provider, _retry_after(response.headers))
        if response.status_code in (401, 403):
            raise BuilderClientError(
                "The assistant's provider key was rejected. An administrator "
                "needs to check it in the provider keys screen."
            )
        raise BuilderClientError("The assistant hit an error. Try again in a moment.")

    try:
        body = response.json()
        reply = parse(body)
    except (ValueError, KeyError, TypeError) as exc:
        logger.error("Agent builder could not parse the {} reply: {}", provider, exc)
        raise BuilderClientError("The assistant replied in a form we could not read.")
    await _record_usage(
        provider=provider,
        model=model,
        usage=reply.usage,
        normalised=_normalised_from_body(provider, body),
        system=system,
        tools=tools,
    )
    return reply


# --- streaming ---------------------------------------------------------------


def _sse_events(text: str):
    """The JSON payloads of a server-sent-event body, in order.

    Both vendors send ``data: {...}`` lines separated by blank lines; OpenAI
    ends with ``data: [DONE]``. Anything that is not JSON is skipped rather
    than raised on: a keepalive comment is not a broken stream.
    """
    for block in text.split("\n\n"):
        for line in block.splitlines():
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if not data or data == "[DONE]":
                continue
            try:
                yield json.loads(data)
            except json.JSONDecodeError:
                continue


class _StreamState:
    """Text and tool calls, accumulated across a stream's events.

    Both vendors send a tool call as a start (id, name) followed by
    fragments of its JSON arguments, keyed by an index; the arguments are
    only parseable once the stream ends. Text arrives as deltas and is
    complete at every point, which is why it can be shown as it grows and
    a tool call cannot.
    """

    def __init__(self) -> None:
        self.parts: list[str] = []
        self.calls: dict[int, dict[str, Any]] = {}
        self._usage: dict[str, int] | None = None
        self._normalised: llm_usage.NormalisedUsage | None = None

    def usage(self) -> dict[str, int] | None:
        return self._usage

    def normalised(self) -> llm_usage.NormalisedUsage | None:
        """The same usage in one shape, with reasoning where reported."""
        return self._normalised

    def text(self) -> str:
        return "".join(self.parts)

    def tool_calls(self) -> tuple[ToolCall, ...]:
        out = []
        for index in sorted(self.calls):
            call = self.calls[index]
            raw = call.get("arguments") or "{}"
            try:
                arguments = json.loads(raw) if raw.strip() else {}
            except json.JSONDecodeError:
                logger.warning(
                    "Streamed tool call {} had unparseable arguments; treating as empty",
                    call.get("name"),
                )
                arguments = {}
            out.append(
                ToolCall(
                    id=call.get("id") or f"call_{index}",
                    name=call.get("name", ""),
                    arguments=arguments if isinstance(arguments, dict) else {},
                )
            )
        return tuple(out)

    def _call(self, index: int) -> dict[str, Any]:
        return self.calls.setdefault(index, {"id": "", "name": "", "arguments": ""})

    def anthropic(self, event: dict[str, Any]) -> bool:
        """Apply one event. Returns whether the text grew."""
        kind = event.get("type")
        index = int(event.get("index") or 0)
        # Anthropic sends the input side when the message starts and the
        # output count, cumulative, on each message_delta.
        if kind == "message_start":
            raw = (event.get("message") or {}).get("usage")
            self._usage = _anthropic_usage(raw)
            self._normalised = (
                llm_usage.normalise(raw, shape="anthropic")
                if isinstance(raw, dict)
                else None
            )
            return False
        if kind == "message_delta" and isinstance(event.get("usage"), dict):
            base = dict(self._usage or {"prompt_tokens": 0})
            base["completion_tokens"] = _count(event["usage"].get("output_tokens"))
            self._usage = base
            self._normalised = replace(
                self._normalised
                or llm_usage.NormalisedUsage(cache_outside_prompt=True),
                output_tokens=base["completion_tokens"],
            )
            return False
        if kind == "content_block_start":
            block = event.get("content_block") or {}
            if block.get("type") == "tool_use":
                call = self._call(index)
                call["id"] = block.get("id", "")
                call["name"] = block.get("name", "")
            return False
        if kind != "content_block_delta":
            return False
        delta = event.get("delta") or {}
        if delta.get("type") == "text_delta" and delta.get("text"):
            self.parts.append(delta["text"])
            return True
        if delta.get("type") == "input_json_delta":
            self._call(index)["arguments"] += delta.get("partial_json") or ""
        return False

    def openai(self, event: dict[str, Any]) -> bool:
        # With stream_options.include_usage, the last chunk carries the usage
        # and an empty choices list.
        if isinstance(event.get("usage"), dict):
            self._usage = _openai_usage(event["usage"])
            self._normalised = llm_usage.normalise(event["usage"], shape="openai")
        choices = event.get("choices") or []
        if not choices:
            return False
        delta = choices[0].get("delta") or {}
        grew = False
        if delta.get("content"):
            self.parts.append(delta["content"])
            grew = True
        for piece in delta.get("tool_calls") or []:
            call = self._call(int(piece.get("index") or 0))
            if piece.get("id"):
                call["id"] = piece["id"]
            function = piece.get("function") or {}
            if function.get("name"):
                call["name"] = function["name"]
            call["arguments"] += function.get("arguments") or ""
        return grew


def _stream_payload(provider: str, payload: dict[str, Any]) -> dict[str, Any]:
    """The request made streaming. OpenAI only reports a stream's usage when
    asked to, in a final chunk; without this there is nothing to meter."""
    out = {**payload, "stream": True}
    if provider == OPENAI:
        out["stream_options"] = {"include_usage": True}
    return out


async def _stream_once(
    *,
    provider: str,
    model: str,
    api_key: str,
    system: str,
    conversation: Conversation,
    on_text: Callable[[str], Awaitable[None]],
    tools: list[dict[str, Any]] | None = None,
) -> ModelReply:
    """One turn, word by word.

    Same request as :func:`complete`, and the same reply at the end -- text
    and any tool calls -- but ``on_text`` is called with the text so far as
    it grows, so a screen can show the reply forming. Tool calls are only
    whole once the stream ends and are returned then, never passed to
    ``on_text``. Gemini is not streamed here; it falls back to one request,
    which is a slower screen and not a wrong one.
    """
    tools = tools or []
    if provider == ANTHROPIC and aws_claude.is_aws_key(api_key):
        return await _aws_stream_once(
            model=model,
            api_key=api_key,
            system=system,
            conversation=conversation,
            on_text=on_text,
            tools=tools,
        )
    if provider == ANTHROPIC:
        url = "https://api.anthropic.com/v1/messages"
        headers = {
            "x-api-key": api_key,
            "anthropic-version": _ANTHROPIC_VERSION,
            "content-type": "application/json",
        }
        payload = _anthropic_request(
            model=model, system=system, conversation=conversation, tools=tools
        )
        state = _StreamState()
        apply = state.anthropic
    elif provider == OPENAI:
        url = "https://api.openai.com/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "content-type": "application/json",
        }
        payload = _openai_request(
            model=model, system=system, conversation=conversation, tools=tools
        )
        state = _StreamState()
        apply = state.openai
    else:
        return await complete(
            provider=provider,
            model=model,
            api_key=api_key,
            system=system,
            conversation=conversation,
            tools=tools,
        )
    payload = _stream_payload(provider, payload)

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as client:
            async with client.stream(
                "POST", url, headers=headers, json=payload
            ) as response:
                if response.status_code >= 400:
                    body = (await response.aread()).decode("utf-8", "replace")
                    logger.error(
                        "Agent builder {} returned {} on stream: {}",
                        provider,
                        response.status_code,
                        body[:2000],
                    )
                    if _out_of_credit(response.status_code, body):
                        raise ProviderOutOfCredit(provider)
                    if response.status_code == 429:
                        raise ProviderRateLimited(
                            provider, _retry_after(response.headers)
                        )
                    if response.status_code in (401, 403):
                        raise BuilderClientError(
                            "The assistant's provider key was rejected. An administrator "
                            "needs to check it in the provider keys screen."
                        )
                    raise BuilderClientError(
                        "The assistant hit an error. Try again in a moment."
                    )
                buffer = ""
                async for chunk in response.aiter_text():
                    buffer += chunk
                    # Events end on a blank line; keep a partial one for the
                    # next chunk rather than parsing half a JSON object.
                    head, sep, tail = buffer.rpartition("\n\n")
                    if not sep:
                        continue
                    buffer = tail
                    grew = False
                    for event in _sse_events(head):
                        grew = apply(event) or grew
                    if grew:
                        await on_text(state.text())
                for event in _sse_events(buffer):
                    apply(event)
    except httpx.HTTPError as exc:
        logger.error("Agent builder could not stream from {}: {}", provider, exc)
        raise BuilderClientError(
            "The assistant could not be reached just now. Try again in a moment."
        ) from exc
    usage = state.usage()
    await _record_usage(
        provider=provider,
        model=model,
        usage=usage,
        normalised=state.normalised(),
        system=system,
        tools=tools,
    )
    return ModelReply(
        text=state.text().strip(), tool_calls=state.tool_calls(), usage=usage
    )


# --- Claude on AWS -------------------------------------------------------------
#
# Claude Platform on AWS and Amazon Bedrock speak the Messages API, so the body
# is ``_anthropic_request``'s and the reply is read by ``_anthropic_parse`` and
# ``_StreamState.anthropic`` -- only the door differs. The SDK's AWS clients
# sign each request with the instance role; its typed errors are mapped onto
# this module's so the rate-limit and fallback handling below is unchanged.


def _aws_error(exc: Exception, model: str) -> BuilderClientError:
    import anthropic

    from api.services.aws_gateway import config as aws_config

    status = getattr(exc, "status_code", None)
    logger.error("Claude on AWS ({}) failed: {} {}", model, status, exc)
    if isinstance(exc, anthropic.RateLimitError):
        return ProviderRateLimited(ANTHROPIC, _retry_after(exc.response.headers))
    if isinstance(
        exc, (anthropic.PermissionDeniedError, anthropic.AuthenticationError)
    ):
        # On AWS a 403 is the role lacking the action, the workspace id being
        # wrong, or Bedrock model access not being granted. The last is the
        # account's state today; record it so the screens say "needs setup".
        if aws_config.claude_backend() == aws_config.BEDROCK:
            aws_config.mark_not_authorized(model, f"HTTP {status}")
        return BuilderClientError(
            "Claude on AWS refused this request. An administrator needs to "
            "finish its setup (IAM role, workspace or model access)."
        )
    if isinstance(exc, (anthropic.APITimeoutError, anthropic.APIConnectionError)):
        return BuilderClientError(
            "The assistant could not be reached just now. Try again in a moment."
        )
    return BuilderClientError("The assistant hit an error. Try again in a moment.")


async def _aws_complete_once(
    *,
    model: str,
    api_key: str,
    system: str,
    conversation: Conversation,
    tools: list[dict[str, Any]],
) -> ModelReply:
    payload = aws_claude.adapt_payload(
        api_key,
        _anthropic_request(
            model=model, system=system, conversation=conversation, tools=tools
        ),
    )
    try:
        sdk = aws_claude.async_client(api_key, timeout=_TIMEOUT_SECONDS)
        message = await sdk.messages.create(**payload)
    except Exception as exc:  # noqa: BLE001 - every SDK failure is mapped
        raise _aws_error(exc, payload["model"]) from exc
    body = message.model_dump() if hasattr(message, "model_dump") else dict(message)
    reply = _anthropic_parse(body)
    await _record_usage(
        provider=aws_claude.usage_provider(api_key),
        model=payload["model"],
        usage=reply.usage,
        normalised=_normalised_from_body(ANTHROPIC, body),
        system=system,
        tools=tools,
    )
    return reply


async def _aws_stream_once(
    *,
    model: str,
    api_key: str,
    system: str,
    conversation: Conversation,
    on_text: Callable[[str], Awaitable[None]],
    tools: list[dict[str, Any]],
) -> ModelReply:
    payload = aws_claude.adapt_payload(
        api_key,
        _anthropic_request(
            model=model, system=system, conversation=conversation, tools=tools
        ),
    )
    state = _StreamState()
    try:
        sdk = aws_claude.async_client(api_key, timeout=_TIMEOUT_SECONDS)
        events = await sdk.messages.create(**payload, stream=True)
        async for event in events:
            data = event.model_dump() if hasattr(event, "model_dump") else dict(event)
            if state.anthropic(data):
                await on_text(state.text())
    except Exception as exc:  # noqa: BLE001 - every SDK failure is mapped
        raise _aws_error(exc, payload["model"]) from exc
    usage = state.usage()
    await _record_usage(
        provider=aws_claude.usage_provider(api_key),
        model=payload["model"],
        usage=usage,
        normalised=state.normalised(),
        system=system,
        tools=tools,
    )
    return ModelReply(
        text=state.text().strip(), tool_calls=state.tool_calls(), usage=usage
    )


# --- the fallback brain -----------------------------------------------------------


async def _platform_claude(provider: str, api_key: str) -> bool:
    """Whether this turn ran on the platform's own Claude -- the only turns the
    fallback brain may answer. A workspace's own key is never moved (BYOK-1)."""
    if provider != ANTHROPIC:
        return False
    if aws_claude.is_aws_key(api_key):
        return True
    from api.db import db_client
    from api.enums import CostComponent
    from api.services.configuration import platform_credentials

    try:
        async with db_client.async_session() as session:
            own = await platform_credentials.resolve_api_key(
                session, component=CostComponent.LLM, provider=ANTHROPIC
            )
    except Exception as exc:  # noqa: BLE001 - unknown is "not ours"
        logger.warning("Could not tell whose Claude key this turn used: {}", exc)
        return False
    return bool(own) and own == api_key


async def _fallback_brain(
    error: BuilderClientError,
    *,
    provider: str,
    api_key: str,
    system: str,
    conversation: Conversation,
    tools: list[dict[str, Any]],
    on_text: Callable[[str], Awaitable[None]] | None = None,
) -> ModelReply:
    """The backup model's answer to this turn, or ``error`` raised again.

    Off unless ``aws_fallback_brain`` is on and its model is configured and
    enabled; then only for the platform's own Claude."""
    from api.services.aws_gateway import bedrock, fallback

    if not fallback.available() or not await _platform_claude(provider, api_key):
        raise error
    try:
        text, calls, usage = await fallback.answer(
            system=system,
            messages=conversation.messages,
            tools=tools,
            why=type(error).__name__,
        )
    except (bedrock.BedrockError, asyncio.TimeoutError) as exc:
        logger.error("The backup model could not answer either: {}", exc)
        raise error from exc
    if on_text is not None and text:
        await on_text(text)
    return ModelReply(
        text=text,
        tool_calls=tuple(
            ToolCall(id=c["id"], name=c["name"], arguments=c["arguments"])
            for c in calls
        ),
        usage=usage,
        fallback_model=fallback.model_id(),
    )


# --- falling back when a vendor is out of credit ------------------------------


async def complete(
    *,
    provider: str,
    model: str,
    api_key: str,
    system: str,
    conversation: Conversation,
    tools: list[dict[str, Any]],
) -> ModelReply:
    """One turn; see :func:`_complete`. When the platform's Claude still
    cannot answer after every vendor fallback, the fallback brain may
    (``services/aws_gateway/fallback.py``), and its reply says so."""
    try:
        return await _complete(
            provider=provider,
            model=model,
            api_key=api_key,
            system=system,
            conversation=conversation,
            tools=tools,
        )
    except BuilderClientError as exc:
        return await _fallback_brain(
            exc,
            provider=provider,
            api_key=api_key,
            system=system,
            conversation=conversation,
            tools=tools,
        )


async def _complete(
    *,
    provider: str,
    model: str,
    api_key: str,
    system: str,
    conversation: Conversation,
    tools: list[dict[str, Any]],
) -> ModelReply:
    """One turn against whichever vendor's key is installed.

    ``tools`` are in OpenAI's shape — ``{"name", "description", "parameters"}``
    — because it is the one all three can be derived from without loss.

    If the vendor says its account is out of credit and the platform holds a
    key for another vendor, the same turn is asked there instead, and the
    empty vendor is skipped for a while (``is_exhausted``). Out of credit
    with nowhere to go raises :class:`ProviderOutOfCredit`, which says so.

    Raises :class:`BuilderClientError` for anything the caller should show the
    user rather than retry blindly. The vendor's own error text is logged but
    never returned: it can quote the request, and the request contains the
    account's prompts.
    """
    try:
        return await _complete_once(
            provider=provider,
            model=model,
            api_key=api_key,
            system=system,
            conversation=conversation,
            tools=tools,
        )
    except ProviderRateLimited as exc:
        return await _after_rate_limit(
            exc,
            provider,
            model,
            api_key,
            lambda p, m, k: _complete_once(
                provider=p,
                model=m,
                api_key=k,
                system=system,
                conversation=conversation,
                tools=tools,
            ),
        )
    except ProviderOutOfCredit:
        mark_exhausted(provider)
        other = await _fallback_model(provider, api_key)
        if other is None:
            raise
        logger.warning(
            "{} is out of credit; answering this turn on {}", provider, other[0]
        )
        with cache_metrics.retrying():
            return await _complete_once(
                provider=other[0],
                model=other[1],
                api_key=other[2],
                system=system,
                conversation=conversation,
                tools=tools,
            )


async def stream(
    *,
    provider: str,
    model: str,
    api_key: str,
    system: str,
    conversation: Conversation,
    on_text: Callable[[str], Awaitable[None]],
    tools: list[dict[str, Any]] | None = None,
) -> ModelReply:
    """One turn, word by word; see :func:`_stream`. The fallback brain may
    answer when the platform's Claude cannot, exactly as in :func:`complete`."""
    try:
        return await _stream(
            provider=provider,
            model=model,
            api_key=api_key,
            system=system,
            conversation=conversation,
            on_text=on_text,
            tools=tools,
        )
    except BuilderClientError as exc:
        return await _fallback_brain(
            exc,
            provider=provider,
            api_key=api_key,
            system=system,
            conversation=conversation,
            tools=tools or [],
            on_text=on_text,
        )


async def _stream(
    *,
    provider: str,
    model: str,
    api_key: str,
    system: str,
    conversation: Conversation,
    on_text: Callable[[str], Awaitable[None]],
    tools: list[dict[str, Any]] | None = None,
) -> ModelReply:
    """One turn, word by word; see :func:`_stream_once`. Falls back to
    another vendor on out-of-credit exactly as :func:`_complete` does."""
    try:
        return await _stream_once(
            provider=provider,
            model=model,
            api_key=api_key,
            system=system,
            conversation=conversation,
            on_text=on_text,
            tools=tools,
        )
    except ProviderRateLimited as exc:
        return await _after_rate_limit(
            exc,
            provider,
            model,
            api_key,
            lambda p, m, k: _stream_once(
                provider=p,
                model=m,
                api_key=k,
                system=system,
                conversation=conversation,
                on_text=on_text,
                tools=tools,
            ),
        )
    except ProviderOutOfCredit:
        mark_exhausted(provider)
        other = await _fallback_model(provider, api_key)
        if other is None:
            raise
        logger.warning(
            "{} is out of credit; answering this turn on {}", provider, other[0]
        )
        with cache_metrics.retrying():
            return await _stream_once(
                provider=other[0],
                model=other[1],
                api_key=other[2],
                system=system,
                conversation=conversation,
                on_text=on_text,
                tools=tools,
            )


async def _after_rate_limit(
    exc: ProviderRateLimited,
    provider: str,
    model: str,
    api_key: str,
    run: Callable[[str, str, str], Awaitable[ModelReply]],
) -> ModelReply:
    """A 429: wait briefly and ask the same vendor once more; if it is still
    limited, ask another vendor the platform holds a key for. The limited
    vendor is tried last for the next couple of minutes, so the turns after
    this one do not queue behind it. With no other vendor able to answer, a
    limit that clears within RATE_LIMIT_LAST_WAIT_SECONDS is waited out once;
    otherwise the rate limit is raised.

    Every call made from here is a second attempt, and is recorded as one
    (``cache_metrics.retrying``) so the cost of a task counts it.
    """
    with cache_metrics.retrying():
        return await _retry_after_rate_limit(exc, provider, model, api_key, run)


async def _retry_after_rate_limit(
    exc: ProviderRateLimited,
    provider: str,
    model: str,
    api_key: str,
    run: Callable[[str, str, str], Awaitable[ModelReply]],
) -> ModelReply:
    wait = exc.retry_after if exc.retry_after is not None else 3.0
    if wait <= RATE_LIMIT_WAIT_SECONDS:
        await asyncio.sleep(max(0.5, wait))
        try:
            return await run(provider, model, api_key)
        except ProviderRateLimited:
            pass
        waited = True
    else:
        waited = False
    mark_exhausted(provider, RATE_LIMITED_FOR_SECONDS)
    other = await _fallback_model(provider, api_key)
    if other is not None:
        logger.warning(
            "{} is rate limited; answering this turn on {}", provider, other[0]
        )
        try:
            return await run(other[0], other[1], other[2])
        except BuilderClientError as fallback_error:
            logger.warning(
                "{} could not take the turn either: {}", other[0], fallback_error
            )
    # Nowhere else to go: wait out the limit once rather than fail the turn.
    if waited or wait > RATE_LIMIT_LAST_WAIT_SECONDS:
        raise exc
    await asyncio.sleep(wait)
    return await run(provider, model, api_key)
