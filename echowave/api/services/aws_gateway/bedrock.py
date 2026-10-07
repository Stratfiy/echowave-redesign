"""Amazon Bedrock's runtime, for the models that are not Claude.

Two operations, both on ``bedrock-runtime``:

* **Converse** for chat-shaped work (the fallback brain, the cheap tier). One
  request shape across Nova and the open-weight models, tools included:
  ``messages`` of ``{"role", "content": [blocks]}``, ``system`` as a list of
  text blocks, ``toolConfig.tools[].toolSpec`` with an ``inputSchema.json``,
  and a reply at ``output.message.content`` with ``usage.inputTokens`` and
  ``usage.outputTokens``.
* **InvokeModel** for embeddings, whose bodies are the model vendor's own.

Credentials come from the standard AWS chain -- the instance role on EC2 --
so nothing here takes a key. The client is made by :func:`runtime_client`,
which tests replace with a fake: nothing in the test suite reaches AWS.

AWS refusing a model (access not granted, agreement not accepted) is turned
into :class:`BedrockNotAuthorized` and recorded with
``config.mark_not_authorized``, so the choice shows "needs setup" everywhere
from that moment rather than failing the same way on every request.
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from loguru import logger

from api import constants
from api.services.aws_gateway import config


class BedrockError(RuntimeError):
    """A Bedrock call failed. The message is safe to log, never to show."""


class BedrockNotAuthorized(BedrockError):
    """AWS refused the model: model access is not enabled for this account."""


class BedrockThrottled(BedrockError):
    pass


#: Error codes AWS uses when the account may not invoke the model.
_NOT_AUTHORIZED_CODES = frozenset(
    {"AccessDeniedException", "UnrecognizedClientException"}
)
_THROTTLED_CODES = frozenset({"ThrottlingException", "ServiceQuotaExceededException"})


@asynccontextmanager
async def runtime_client(region: str | None = None) -> AsyncIterator[Any]:
    """A ``bedrock-runtime`` client on the AWS credential chain."""
    import aioboto3

    session = aioboto3.Session()
    async with session.client(
        "bedrock-runtime", region_name=region or constants.BEDROCK_REGION
    ) as client:
        yield client


def _error_code(exc: Exception) -> str:
    response = getattr(exc, "response", None)
    if isinstance(response, dict):
        return str((response.get("Error") or {}).get("Code") or "")
    return ""


def _translate(model_id: str, exc: Exception) -> BedrockError:
    code = _error_code(exc)
    if code in _NOT_AUTHORIZED_CODES:
        config.mark_not_authorized(model_id, code)
        return BedrockNotAuthorized(f"{model_id}: {code}")
    if code in _THROTTLED_CODES:
        return BedrockThrottled(f"{model_id}: {code}")
    logger.error("Bedrock call to {} failed: {} {}", model_id, code, exc)
    return BedrockError(f"{model_id}: {code or type(exc).__name__}")


# --- Converse ---------------------------------------------------------------


def converse_usage(response: dict[str, Any]) -> dict[str, int] | None:
    """Converse's usage in the pipeline's shape (``prompt_tokens`` ...)."""
    usage = response.get("usage")
    if not isinstance(usage, dict):
        return None
    out = {
        "prompt_tokens": max(int(usage.get("inputTokens") or 0), 0),
        "completion_tokens": max(int(usage.get("outputTokens") or 0), 0),
    }
    if usage.get("cacheReadInputTokens"):
        out["cache_read_input_tokens"] = int(usage["cacheReadInputTokens"])
    if usage.get("cacheWriteInputTokens"):
        out["cache_creation_input_tokens"] = int(usage["cacheWriteInputTokens"])
    return out


async def converse(
    *,
    model_id: str,
    messages: list[dict[str, Any]],
    system: str | None = None,
    tools: list[dict[str, Any]] | None = None,
    max_tokens: int = 1024,
    temperature: float | None = None,
) -> dict[str, Any]:
    """One Converse request. Raises :class:`BedrockError` subclasses.

    ``tools`` are in this codebase's OpenAI-like shape (``name``,
    ``description``, ``parameters``) and become Converse ``toolSpec``s.
    """
    request: dict[str, Any] = {
        "modelId": model_id,
        "messages": messages,
        "inferenceConfig": {"maxTokens": max_tokens},
    }
    if temperature is not None:
        request["inferenceConfig"]["temperature"] = temperature
    if system:
        request["system"] = [{"text": system}]
    if tools:
        request["toolConfig"] = {
            "tools": [
                {
                    "toolSpec": {
                        "name": t["name"],
                        "description": t.get("description") or t["name"],
                        "inputSchema": {
                            "json": t.get("parameters") or {"type": "object"}
                        },
                    }
                }
                for t in tools
            ]
        }
    try:
        async with runtime_client() as client:
            return await client.converse(**request)
    except BedrockError:
        raise
    except Exception as exc:  # noqa: BLE001 - every AWS failure is translated
        raise _translate(model_id, exc) from exc


def converse_text(response: dict[str, Any]) -> str:
    blocks = ((response.get("output") or {}).get("message") or {}).get("content") or []
    return "".join(b.get("text", "") for b in blocks if isinstance(b, dict)).strip()


# --- InvokeModel: embeddings ------------------------------------------------


def embedding_body(
    model_id: str, texts: list[str], *, input_type: str
) -> dict[str, Any]:
    """The request body for an embeddings model, in its vendor's shape."""
    if model_id.startswith("cohere.embed"):
        body: dict[str, Any] = {"texts": texts, "input_type": input_type}
        if model_id.startswith("cohere.embed-v4"):
            body["embedding_types"] = ["float"]
            body["output_dimension"] = constants.BEDROCK_EMBEDDING_DIMENSIONS
        return body
    raise BedrockError(f"{model_id} is not an embeddings model this gateway can call")


def embedding_vectors(payload: dict[str, Any]) -> list[list[float]]:
    """Vectors from a Cohere reply: a list, or ``{"float": [...]}`` when
    ``embedding_types`` was asked for."""
    embeddings = payload.get("embeddings")
    if isinstance(embeddings, dict):
        embeddings = embeddings.get("float")
    if not isinstance(embeddings, list):
        raise BedrockError("Embeddings reply had no vectors")
    return embeddings


async def embed(
    model_id: str, texts: list[str], *, input_type: str = "search_document"
) -> tuple[list[list[float]], int | None]:
    """Embed ``texts``. Returns the vectors and the billed token count, when
    the reply carries one (Cohere reports it in ``meta.billed_units``)."""
    body = embedding_body(model_id, texts, input_type=input_type)
    try:
        async with runtime_client() as client:
            response = await client.invoke_model(
                modelId=model_id,
                body=json.dumps(body),
                contentType="application/json",
                accept="application/json",
            )
            raw = response["body"]
            data = await raw.read() if hasattr(raw, "read") else raw
    except BedrockError:
        raise
    except Exception as exc:  # noqa: BLE001 - every AWS failure is translated
        raise _translate(model_id, exc) from exc
    payload = json.loads(data)
    vectors = embedding_vectors(payload)
    billed = ((payload.get("meta") or {}).get("billed_units") or {}).get("input_tokens")
    return vectors, (int(billed) if billed is not None else None)
