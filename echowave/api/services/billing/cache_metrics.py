"""What each model call sent and what the prompt cache did with it.

Prompt caching already exists here: the builder client marks Anthropic's
system block, the pipeline turns Anthropic's caching on, the composer keeps
byte-identical blocks above the values that change, and both doors record
cached tokens. What did not exist is a way to tell whether any of it works:
neither ``model_usage`` nor a run's receipt says *which prompt* a call sent,
so a cache that never hits and a cache that hits every time look the same.

This module records, per model call, the usage in one shape
(``billing.llm_usage``) and four hashes of the prompt prefix:

* ``system_hash`` and ``tools_hash`` -- the system prompt as sent, and the
  tool schemas in the order sent, each serialised deterministically
  (:func:`canonical_json`: sorted keys, no whitespace) so the same content is
  the same hash whatever order a dict was built in;
* ``prefix_hash`` -- both together: what a vendor's prefix cache matches on.
  Two calls of one conversation with different prefix hashes is a prefix
  broken, which is what the report's prefix-breaker list counts;
* ``prompt_fingerprint`` -- the prompt *version*: the same, with the values
  that are different on every call by design (the clock line, what a caller
  has said) masked, so calls of one agent step or one assistant group
  together across conversations.

It also records which conversation a call belongs to -- a run, a Decibyl
thread -- and whether it was a retry, so the report can price a whole task
including the calls a single-call view leaves out.

**Recording never costs the reply**, exactly as in ``model_usage``: every
write is best-effort and logged on failure. Nothing here charges anyone and
nothing here changes a request.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Iterable, Iterator

from loguru import logger

from api.db import db_client
from api.db.models import LlmCallUsageModel
from api.services.billing import model_usage
from api.services.billing.llm_usage import NormalisedUsage

#: What stands in for a masked value in the fingerprint.
_MASK = "␀"

#: Run modes that are text rather than a phone or browser call. Anything
#: else, including a mode added next year, reads as a call -- the kind of work
#: with an outcome to count -- rather than disappearing.
_TEXT_MODES = frozenset({"textchat", "text_chat", "CHAT"})

_conversation: ContextVar[str | None] = ContextVar(
    "cache_metrics_conversation", default=None
)
_retry: ContextVar[bool] = ContextVar("cache_metrics_retry", default=False)


# --- serialisation and hashes ------------------------------------------------


def _plain(value: Any) -> Any:
    """``value`` as JSON-able data, for hashing only."""
    if hasattr(value, "to_default_dict"):
        return value.to_default_dict()
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return dataclasses.asdict(value)
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if isinstance(value, (set, frozenset)):
        return sorted(value, key=str)
    return str(value)


def canonical_json(value: Any) -> str:
    """Deterministic serialisation: sorted keys, no whitespace, unicode kept.

    List order is kept, because it is part of what a vendor's cache matches
    (two tool lists in different orders are different prefixes)."""
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=_plain,
    )


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


@dataclasses.dataclass(frozen=True)
class PromptHashes:
    prompt_fingerprint: str
    prefix_hash: str
    system_hash: str
    tools_hash: str

    def as_row(self) -> dict[str, str]:
        return dataclasses.asdict(self)


def prompt_hashes(
    system: str | None,
    tools: Iterable[Any] | None,
    *,
    volatile: Iterable[str | None] = (),
) -> PromptHashes:
    """The four hashes of one call's stable prefix.

    ``volatile`` are substrings of ``system`` that differ on every call by
    design -- the clock line, the caller's answers so far. They are part of
    the prefix the vendor sees, so they count in ``prefix_hash``; they are
    masked out of ``prompt_fingerprint``, so the version of a prompt is the
    same on Monday as on Tuesday.
    """
    system = system or ""
    tools_json = canonical_json([_plain_tool(t) for t in (tools or [])])
    system_hash = _digest(system)
    tools_hash = _digest(tools_json)
    masked = system
    for value in volatile:
        if value:
            masked = masked.replace(value, _MASK)
    return PromptHashes(
        prompt_fingerprint=_digest(canonical_json([masked, tools_json])),
        prefix_hash=_digest(f"{system_hash}:{tools_hash}"),
        system_hash=system_hash,
        tools_hash=tools_hash,
    )


def _plain_tool(tool: Any) -> Any:
    if isinstance(tool, (dict, list, str, int, float, bool)) or tool is None:
        return tool
    return _plain(tool)


# --- which conversation, and was it a retry ----------------------------------


@contextmanager
def conversation(key: str | None) -> Iterator[None]:
    """Every call inside this block belongs to ``key``."""
    token = _conversation.set(key[:96] if key else None)
    try:
        yield
    finally:
        _conversation.reset(token)


@contextmanager
def retrying() -> Iterator[None]:
    """Calls inside this block are a second attempt at something."""
    token = _retry.set(True)
    try:
        yield
    finally:
        _retry.reset(token)


def current_conversation() -> str | None:
    """The open conversation, else the Decibyl thread being served."""
    key = _conversation.get()
    if key:
        return key
    try:
        from api.services.workflow import agent_timeline

        thread = agent_timeline.current_thread()
    except Exception:  # noqa: BLE001 - a key is a nicety, never a failure
        return None
    return f"thread:{thread}"[:96] if thread else None


def is_retry() -> bool:
    return _retry.get()


# --- writing -----------------------------------------------------------------


def _row(
    *,
    source: str,
    feature: str,
    organization_id: int | None,
    provider: str,
    model: str,
    usage: NormalisedUsage,
    hashes: PromptHashes | None,
    conversation_key: str | None,
    workflow_run_id: int | None = None,
    retry: bool = False,
) -> dict[str, Any]:
    feature = (feature or model_usage.UNATTRIBUTED)[:64]
    row: dict[str, Any] = {
        "source": source,
        "organization_id": organization_id,
        "feature": feature,
        "provider": (provider or "unknown")[:64],
        "model": (model or "")[:128],
        "conversation_key": conversation_key,
        "workflow_run_id": workflow_run_id,
        "input_tokens": usage.input_tokens,
        "output_tokens": usage.output_tokens,
        "cache_read_tokens": usage.cache_read_tokens,
        "cache_write_tokens": usage.cache_write_tokens,
        "reasoning_tokens": usage.reasoning_tokens,
        "is_retry": bool(retry),
        "byok": feature.endswith(":byok"),
    }
    if hashes is not None:
        row.update(hashes.as_row())
    return row


async def _write(rows: list[dict[str, Any]]) -> None:
    async with db_client.async_session() as session:
        session.add_all([LlmCallUsageModel(**row) for row in rows])
        await session.commit()


async def record_direct(
    *,
    provider: str,
    model: str,
    usage: NormalisedUsage | None,
    system: str | None,
    tools: Iterable[Any] | None,
) -> None:
    """One builder-client call, attributed to the open ``model_usage`` scope.

    Never raises."""
    if usage is None:
        return
    try:
        organization_id, feature = model_usage.current()
        row = _row(
            source="direct",
            feature=feature,
            organization_id=organization_id,
            provider=provider,
            model=model,
            usage=usage,
            hashes=prompt_hashes(system, tools),
            conversation_key=current_conversation(),
            retry=is_retry(),
        )
        await _write([row])
    except Exception as exc:  # noqa: BLE001 - measurement never costs the reply
        logger.warning(
            "Could not record cache usage for {}/{}: {}", provider, model, exc
        )


def run_feature(mode: str | None, annotations: Any) -> str:
    """What kind of work a run is, for the report: a routine, an agent's
    text chat, or an agent call (phone or browser)."""
    if isinstance(annotations, dict) and annotations.get("routine"):
        return "routine"
    if (mode or "") in _TEXT_MODES:
        return "agent_chat"
    return "agent_call"


async def record_run(calls: list[dict[str, Any]], workflow_run_id: int | None) -> int:
    """A run's model calls, buffered by the pipeline, written in one go.

    ``calls`` are what ``PipelineMetricsAggregator.take_llm_calls`` hands
    back: ``provider``, ``model``, ``usage`` (a :class:`NormalisedUsage`),
    ``hashes`` (or ``None``), ``feature`` (or ``None`` for the run's own) and
    ``key_source``. The run's organisation and kind of work are read here,
    once. Returns how many rows were written; never raises.
    """
    if not calls:
        return 0
    try:
        organization_id = None
        feature = "agent_call"
        if workflow_run_id is not None:
            run = await db_client.get_workflow_run_by_id(workflow_run_id)
            if run is not None:
                feature = run_feature(
                    getattr(run, "mode", None), getattr(run, "annotations", None)
                )
                workflow = getattr(run, "workflow", None)
                organization_id = getattr(workflow, "organization_id", None)
        key = f"run:{workflow_run_id}" if workflow_run_id is not None else None
        rows = []
        for call in calls:
            usage = call.get("usage")
            if not isinstance(usage, NormalisedUsage) or usage.is_empty:
                continue
            own = call.get("feature") or feature
            if call.get("key_source") == "byok":
                own = f"{own}:byok"
            rows.append(
                _row(
                    source="pipeline",
                    feature=own,
                    organization_id=organization_id,
                    provider=call.get("provider") or "unknown",
                    model=call.get("model") or "",
                    usage=usage,
                    hashes=call.get("hashes"),
                    conversation_key=key,
                    workflow_run_id=workflow_run_id,
                )
            )
        if rows:
            await _write(rows)
        return len(rows)
    except Exception as exc:  # noqa: BLE001 - measurement never costs the call
        logger.warning(
            "Could not record cache usage for run {}: {}", workflow_run_id, exc
        )
        return 0


__all__ = [
    "PromptHashes",
    "canonical_json",
    "conversation",
    "current_conversation",
    "is_retry",
    "prompt_hashes",
    "record_direct",
    "record_run",
    "retrying",
    "run_feature",
]
