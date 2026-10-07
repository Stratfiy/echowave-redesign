"""Where Claude runs for the managed tiers: Anthropic, Claude Platform on AWS,
or Amazon Bedrock.

**The credential slot carries the door.** Every Claude caller here already
passes ``(provider, model, api_key)`` around -- the builder client, the
pipeline's LLM section after managed resolution. On AWS there is no API key:
requests are SigV4-signed with whatever the standard AWS credential chain
finds (the instance role on EC2). So the key slot holds a marker naming the
backend instead (:data:`AWS_PLATFORM_KEY`, :data:`BEDROCK_KEY`), and the one
place that talks to the vendor reads it. A marker is not a secret and never
authenticates anything on its own; it is only ever produced by
:func:`platform_credential` from the operator's configuration.

**Only the platform's own Claude moves.** A workspace that brought its own
Anthropic key keeps going to Anthropic with it (BYOK-1); nothing here touches
that path, because only :func:`platform_credential` produces a marker and only
platform-key resolution calls it.

**Bare ids in, the backend's id on the wire.** The tiers and the builder name
Claude by its first-party id. Claude Platform on AWS takes those unchanged;
Bedrock takes the ``anthropic.``-prefixed id configured for it in
``BEDROCK_CLAUDE_MODEL_IDS``.
"""

from __future__ import annotations

from typing import Any

from loguru import logger

from api import constants
from api.services.aws_gateway import config

#: The key-slot markers. Prefixed so no real vendor key can collide.
AWS_PLATFORM_KEY = "aws-iam:aws_platform"
BEDROCK_KEY = "aws-iam:bedrock"
_KEY_FOR = {config.AWS_PLATFORM: AWS_PLATFORM_KEY, config.BEDROCK: BEDROCK_KEY}
_BACKEND_FOR = {v: k for k, v in _KEY_FOR.items()}

_warned: set[str] = set()


def is_aws_key(api_key: str | None) -> bool:
    return (api_key or "") in _BACKEND_FOR


def backend_for_key(api_key: str | None) -> str:
    """Which backend a credential reaches: an AWS marker, or Anthropic."""
    return _BACKEND_FOR.get(api_key or "", config.ANTHROPIC)


def usage_provider(api_key: str | None) -> str:
    """The provider name a call on this credential is recorded and priced
    under: ``anthropic``, ``anthropic_aws`` or ``aws_bedrock``."""
    return config.USAGE_PROVIDER[backend_for_key(api_key)]


def platform_credential(model: str | None = None) -> str | None:
    """The marker for the configured AWS backend when it can serve ``model``.

    None when Claude runs on Anthropic (the default), and None -- with an
    error in the log, once per reason -- when an AWS backend is chosen but
    not ready. The caller then does what it did before this existed: looks
    up the Anthropic platform key. The gap is also on the Models screen and
    the readiness overview (:func:`config.overview`), so it is never only
    in a log.
    """
    backend = config.claude_backend()
    if backend == config.ANTHROPIC:
        return None
    status = config.claude_backend_status(model)
    if not status.available:
        reason = f"{backend}:{status.reason}"
        if reason not in _warned:
            _warned.add(reason)
            logger.error(
                "CLAUDE_BACKEND={} cannot serve {} yet ({}); Claude stays on "
                "Anthropic's API until it can.",
                backend,
                model or "Claude",
                status.reason,
            )
        return None
    return _KEY_FOR[backend]


def wire_model(api_key: str | None, model: str) -> str:
    """The model id to send on this credential's backend."""
    if backend_for_key(api_key) == config.BEDROCK:
        return config.bedrock_model_for(model) or model
    return model


def async_client(api_key: str, *, timeout: float, max_retries: int = 0) -> Any:
    """The SDK client for an AWS backend. Credentials come from the AWS chain.

    Retries default to none: the callers have their own rate-limit and
    fallback handling, and a hidden retry inside would double the wait
    before a fallback brain could step in.
    """
    backend = backend_for_key(api_key)
    if backend == config.AWS_PLATFORM:
        from anthropic import AsyncAnthropicAWS

        return AsyncAnthropicAWS(
            aws_region=constants.CLAUDE_AWS_REGION,
            workspace_id=constants.ANTHROPIC_AWS_WORKSPACE_ID,
            timeout=timeout,
            max_retries=max_retries,
        )
    if backend == config.BEDROCK:
        from anthropic import AsyncAnthropicBedrock

        return AsyncAnthropicBedrock(
            aws_region=constants.BEDROCK_REGION,
            timeout=timeout,
            max_retries=max_retries,
        )
    raise ValueError("Not an AWS credential")


#: Request fields Bedrock does not take. Bedrock serves the Messages API's
#: core (messages, tools, system, caching, thinking) but not server-side web
#: search or fetch, the Files API, batches or the MCP connector. The builder
#: sends none of those -- its web tools are our own -- but anything that
#: tries is stripped here rather than turned into a 400 mid-conversation.
_BEDROCK_UNSUPPORTED_FIELDS = ("mcp_servers", "container", "inference_geo", "speed")
_BEDROCK_SERVER_TOOL_PREFIXES = ("web_search_", "web_fetch_", "code_execution_")


def adapt_payload(api_key: str, payload: dict[str, Any]) -> dict[str, Any]:
    """A Messages request body made ready for this credential's backend."""
    body = {k: v for k, v in payload.items() if k != "stream"}
    body["model"] = wire_model(api_key, str(payload.get("model") or ""))
    if backend_for_key(api_key) != config.BEDROCK:
        return body
    for name in _BEDROCK_UNSUPPORTED_FIELDS:
        body.pop(name, None)
    tools = body.get("tools")
    if tools:
        kept = [
            t
            for t in tools
            if not str(t.get("type") or "").startswith(_BEDROCK_SERVER_TOOL_PREFIXES)
        ]
        if len(kept) != len(tools):
            logger.warning(
                "Bedrock serves no server-side web or code tools; {} dropped "
                "from this request (our own web tools still run).",
                len(tools) - len(kept),
            )
        body["tools"] = kept
        if not kept:
            body.pop("tools")
    return body
