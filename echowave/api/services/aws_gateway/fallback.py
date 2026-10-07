"""The fallback brain: a Bedrock model that answers when Claude cannot.

Claude erroring or timing out used to fail the turn, or move it to another
vendor only when that vendor's key was installed and only on out-of-credit
or a rate limit. With ``aws_fallback_brain`` on and ``BEDROCK_FALLBACK_MODEL``
configured (Amazon Nova Pro, or an open-weight model on Bedrock) and enabled
for the account, the same turn is asked there instead.

Three promises, each tested:

* **It says so.** ``ModelReply.fallback_model`` names the backup model, and
  every surface a person reads (Decibyl's reply, the builder, Studio) adds
  :data:`NOTE`, so a person reading Auto's answer knows a backup model wrote
  it. A stand-in passing itself off as
  Claude is the "fake success" the launch rules forbid.
* **It is recorded.** Usage is written under the backup model's own provider
  and id (``aws_bedrock``), with the feature marked ``:fallback``, so the
  token report shows how often Claude was not the one answering.
* **It never hides a missing setup.** A fallback that is not configured or
  not enabled does nothing and the original error stands.

Only the platform's managed brain falls back. A workspace on its own key is
never moved onto ours (BYOK-1), for the same reason ``client._fallback_model``
gives.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from loguru import logger

from api import constants
from api.services.aws_gateway import bedrock, config

#: What the reply says when the backup model wrote it.
NOTE = "(Claude was unavailable just now, so a backup model answered this one.)"

#: The output ceiling for a stand-in turn; the builder's own is 4096.
MAX_TOKENS = 4096


def available() -> bool:
    return config.fallback_status().available


def model_id() -> str:
    return constants.BEDROCK_FALLBACK_MODEL


def _text_of(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, dict):
        content = {k: v for k, v in content.items() if not str(k).startswith("_")}
    try:
        return json.dumps(content, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return str(content)


def to_converse(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The builder's vendor-neutral transcript as Converse messages.

    Converse wants strictly alternating roles, so consecutive entries of one
    role are merged; tool results ride in a user turn as ``toolResult``
    blocks. Pictures are left out with a line saying so -- the stand-in is
    for keeping a conversation going, not for re-reading an attachment.
    """
    out: list[dict[str, Any]] = []

    def push(role: str, blocks: list[dict[str, Any]]) -> None:
        if not blocks:
            return
        if out and out[-1]["role"] == role:
            out[-1]["content"].extend(blocks)
        else:
            out.append({"role": role, "content": list(blocks)})

    for entry in messages:
        role = entry.get("role")
        if role == "user":
            text = _text_of(entry.get("content"))
            if isinstance(entry.get("content"), dict) and entry["content"].get(
                "_images"
            ):
                text += (
                    "\n[An image was attached; it is not shown to the backup model.]"
                )
            push("user", [{"text": text or " "}])
        elif role == "assistant":
            blocks: list[dict[str, Any]] = []
            if entry.get("content"):
                blocks.append({"text": str(entry["content"])})
            for call in entry.get("tool_calls") or []:
                blocks.append(
                    {
                        "toolUse": {
                            "toolUseId": call["id"],
                            "name": call["name"],
                            "input": call.get("arguments") or {},
                        }
                    }
                )
            push("assistant", blocks)
        elif role == "tool":
            push(
                "user",
                [
                    {
                        "toolResult": {
                            "toolUseId": entry.get("tool_call_id", ""),
                            "content": [{"text": _text_of(entry.get("content"))}],
                            "status": "success",
                        }
                    }
                ],
            )
    # Converse requires the first message to be the user's.
    while out and out[0]["role"] != "user":
        out.pop(0)
    return out


def parse(response: dict[str, Any]) -> tuple[str, list[dict[str, Any]]]:
    """Text and tool calls (``id``, ``name``, ``arguments``) from a reply."""
    blocks = ((response.get("output") or {}).get("message") or {}).get("content") or []
    text: list[str] = []
    calls: list[dict[str, Any]] = []
    for block in blocks:
        if not isinstance(block, dict):
            continue
        if "text" in block:
            text.append(block["text"])
        elif "toolUse" in block:
            use = block["toolUse"] or {}
            calls.append(
                {
                    "id": use.get("toolUseId", ""),
                    "name": use.get("name", ""),
                    "arguments": use.get("input") or {},
                }
            )
    return "".join(text).strip(), calls


def with_note(text: str) -> str:
    return f"{text}\n\n{NOTE}" if text else NOTE


async def answer(
    *,
    system: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    why: str,
) -> tuple[str, list[dict[str, Any]], dict[str, int] | None]:
    """Ask the backup model the same turn. Returns text, tool calls and
    usage. Raises :class:`bedrock.BedrockError` when it fails
    too, which the caller turns back into the original error."""
    from api.services.billing import model_usage

    model = model_id()
    logger.warning(
        "Claude could not answer ({}); the backup model {} is answering this turn.",
        why,
        model,
    )
    response = await asyncio.wait_for(
        bedrock.converse(
            model_id=model,
            messages=to_converse(messages),
            system=system,
            tools=tools,
            max_tokens=MAX_TOKENS,
        ),
        timeout=constants.BEDROCK_FALLBACK_TIMEOUT_SECONDS,
    )
    text, calls = parse(response)
    usage = bedrock.converse_usage(response)
    _, feature = model_usage.current()
    with model_usage.labelled(f"{feature}:fallback"[:64]):
        await model_usage.record(
            provider=config.USAGE_PROVIDER[config.BEDROCK], model=model, usage=usage
        )
    # The note goes on at the surface a person reads (Decibyl's reply, the
    # builder and Studio), not here: this client also serves callers that
    # parse the text, and a sentence after their JSON would break them.
    return text, calls, usage
