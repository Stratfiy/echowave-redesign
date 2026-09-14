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
keep on their release trains.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

import httpx
from loguru import logger

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
    """
    if isinstance(schema, dict):
        return {
            key: _strip_for_gemini(value)
            for key, value in schema.items()
            if key not in _GEMINI_UNSUPPORTED_SCHEMA_KEYS
        }
    if isinstance(schema, list):
        return [_strip_for_gemini(item) for item in schema]
    return schema


def _as_text(content: Any) -> str:
    """A tool result as a string, for vendors that will not take an object."""
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
            messages.append(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": entry["tool_call_id"],
                            "content": _as_text(entry["content"]),
                        }
                    ],
                }
            )

    payload: dict[str, Any] = {
        "model": model,
        "max_tokens": _MAX_OUTPUT_TOKENS,
        "system": system,
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
    return payload


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
    return ModelReply(text="".join(text_parts).strip(), tool_calls=tuple(calls))


# --- OpenAI -----------------------------------------------------------------


def _openai_request(
    *, model: str, system: str, conversation: Conversation, tools: list[dict[str, Any]]
) -> dict[str, Any]:
    messages: list[dict[str, Any]] = [{"role": "system", "content": system}]
    for entry in conversation.messages:
        role = entry["role"]
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
        text=(message.get("content") or "").strip(), tool_calls=tuple(calls)
    )


# --- Gemini -----------------------------------------------------------------


def _gemini_request(
    *, system: str, conversation: Conversation, tools: list[dict[str, Any]]
) -> dict[str, Any]:
    contents: list[dict[str, Any]] = []
    for entry in conversation.messages:
        role = entry["role"]
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
            content = entry["content"]
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
    return ModelReply(text="".join(text_parts).strip(), tool_calls=tuple(calls))


# --- the seam ---------------------------------------------------------------


async def complete(
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
        if response.status_code == 429:
            raise BuilderClientError(
                "The assistant is rate limited right now. Try again shortly."
            )
        if response.status_code in (401, 403):
            raise BuilderClientError(
                "The assistant's provider key was rejected. An administrator "
                "needs to check it in the provider keys screen."
            )
        raise BuilderClientError("The assistant hit an error. Try again in a moment.")

    try:
        return parse(response.json())
    except (ValueError, KeyError, TypeError) as exc:
        logger.error("Agent builder could not parse the {} reply: {}", provider, exc)
        raise BuilderClientError("The assistant replied in a form we could not read.")


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
    """One turn, word by word.

    Same request as :func:`complete`, and the same reply at the end -- text
    and any tool calls -- but ``on_text`` is called with the text so far as
    it grows, so a screen can show the reply forming. Tool calls are only
    whole once the stream ends and are returned then, never passed to
    ``on_text``. Gemini is not streamed here; it falls back to one request,
    which is a slower screen and not a wrong one.
    """
    tools = tools or []
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
    payload["stream"] = True

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
                    if response.status_code == 429:
                        raise BuilderClientError(
                            "The assistant is rate limited right now. Try again shortly."
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
    return ModelReply(text=state.text().strip(), tool_calls=state.tool_calls())
