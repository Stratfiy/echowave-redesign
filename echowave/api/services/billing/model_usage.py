"""Write down what every model call outside a pipeline used.

Pipeline runs meter themselves onto their receipt. The builder client does
not have a run: it is the door Decibyl's assistant, the builder, triggers,
document fields, the acceptable-use check and the graph reviews all go
through. This module is how those calls are recorded, from inside the
client, so no caller can forget.

**Who asked** is carried in a context variable rather than a parameter
threaded through a dozen call sites. A caller that knows says so with
``scope(organization_id=..., feature=...)``; a caller that does not is
recorded as ``unattributed`` rather than skipped. The report shows the
unattributed share, and that number going down is how the attribution gets
finished -- dropping the call instead would make the gap invisible, which is
the one outcome this exists to prevent.

**Recording never costs the reply.** A write that fails is logged and the
answer goes back to the person as if nothing happened. Measurement is worth
less than the thing being measured.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Iterator

from loguru import logger

from api.db import db_client
from api.db.models import ModelUsageModel

UNATTRIBUTED = "unattributed"

#: The four counts, named as the pipeline names them in ``usage_info["llm"]``.
FIELDS = (
    "prompt_tokens",
    "completion_tokens",
    "cache_read_input_tokens",
    "cache_creation_input_tokens",
)

_scope: ContextVar[tuple[int | None, str]] = ContextVar(
    "model_usage_scope", default=(None, UNATTRIBUTED)
)


@contextmanager
def scope(*, organization_id: int | None, feature: str) -> Iterator[None]:
    """Attribute every model call inside this block."""
    token = _scope.set((organization_id, (feature or UNATTRIBUTED)[:64]))
    try:
        yield
    finally:
        _scope.reset(token)


@contextmanager
def labelled(feature: str) -> Iterator[None]:
    """Name what is asking while keeping whichever account an outer scope
    already set -- for code that knows its own purpose but not the account."""
    organization_id, _ = current()
    with scope(organization_id=organization_id, feature=feature):
        yield


def current() -> tuple[int | None, str]:
    return _scope.get()


async def _write(row: dict[str, Any]) -> None:
    async with db_client.async_session() as session:
        session.add(ModelUsageModel(**row))
        await session.commit()


async def record(*, provider: str, model: str, usage: dict[str, Any] | None) -> None:
    """One call's usage, attributed to whatever scope is open."""
    if not usage:
        return
    organization_id, feature = current()
    row: dict[str, Any] = {
        "organization_id": organization_id,
        "feature": feature,
        "provider": provider,
        "model": (model or "")[:128],
    }
    for field in FIELDS:
        try:
            row[field] = max(int(usage.get(field) or 0), 0)
        except (TypeError, ValueError):
            row[field] = 0
    try:
        await _write(row)
    except Exception as exc:  # noqa: BLE001 - measurement never costs the reply
        logger.warning(
            "Could not record model usage for {}/{}: {}", provider, model, exc
        )


async def record_audio(*, provider: str, model: str, seconds: float | None) -> None:
    """One transcription's audio, attributed to whatever scope is open.

    Written even when the vendor did not say how long the audio was, with
    0 seconds: the call happened, and a count of calls with unknown length
    is still a number D-1 can use, where a skipped row is not."""
    organization_id, feature = current()
    try:
        audio = max(float(seconds or 0), 0.0)
    except (TypeError, ValueError):
        audio = 0.0
    try:
        await _write(
            {
                "organization_id": organization_id,
                "feature": feature,
                "provider": provider,
                "model": (model or "")[:128],
                "audio_seconds": audio,
            }
        )
    except Exception as exc:  # noqa: BLE001 - measurement never costs the reply
        logger.warning(
            "Could not record transcription usage for {}/{}: {}", provider, model, exc
        )


def provider_of(service: Any) -> str:
    """A transcription service's vendor, from its class name:
    ``DeepgramTranscriptionService`` is ``deepgram``."""
    name = type(service).__name__.removesuffix("TranscriptionService")
    return (name or "unknown").lower()[:64]


__all__ = [
    "FIELDS",
    "UNATTRIBUTED",
    "current",
    "labelled",
    "provider_of",
    "record",
    "record_audio",
    "scope",
]
