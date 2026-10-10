"""The one writer of ``learning_events``.

``record`` is called from places that are doing something else -- settling a
card, taking a thumb, finishing an eval -- so it has two jobs beyond writing:
it must never make that other thing fail, and it must never write what it was
not allowed to.

In order:

1. **Flag.** ``training_loop`` off for the workspace: return, touching
   nothing (no read, no write).
2. **Consent.** The workspace's setting decides whether anything is kept.
   Off ("Stop collecting" or "Stop and delete"), nothing new is written: not
   the words, and not a row of facts either.
3. **Redaction.** Every text goes through ``redact`` before it is stored.
   If that fails for any reason, nothing is written: a row with raw text is
   worse than no row.
4. **One idempotent insert.** The same event twice is one row.

Everything is caught and logged. A lost training row is a smaller harm than
a settled card that errored.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from loguru import logger

from api.db import db_client
from api.services import training_loop
from api.services.training_loop import consent, redact

MAX_KEY_CHARS = 128
MAX_REF_CHARS = 160
MAX_DETAIL_CHARS = 200


def _text(value: Any) -> str | None:
    """Redacted text, or None for nothing worth keeping."""
    if value is None:
        return None
    cleaned = redact.redact(value).strip()
    return cleaned or None


async def run_usage(
    workflow_run_id: int | None, organization_id: int
) -> tuple[str | None, int | None, int | None]:
    """The model and token counts a run has reported so far, from the run's
    own usage record: the model that used the most tokens, and the sums.
    ``(None, None, None)`` when the run is unknown or reported none."""
    if not workflow_run_id:
        return None, None, None
    try:
        run = await db_client.get_workflow_run(
            workflow_run_id, organization_id=organization_id
        )
    except Exception as exc:  # noqa: BLE001 - usage is a nicety
        logger.debug("training_loop: no usage for run {}: {}", workflow_run_id, exc)
        return None, None, None
    llm = (getattr(run, "usage_info", None) or {}).get("llm")
    if not isinstance(llm, dict) or not llm:
        return None, None, None
    prompt_total = completion_total = 0
    best_model, best_tokens = None, -1
    for key, value in llm.items():
        if not isinstance(value, dict):
            continue
        prompt = _count(value.get("prompt_tokens"))
        completion = _count(value.get("completion_tokens"))
        prompt_total += prompt
        completion_total += completion
        if prompt + completion > best_tokens:
            best_tokens = prompt + completion
            best_model = str(key).partition("|||")[2].strip() or None
    if best_model is None and prompt_total == completion_total == 0:
        return None, None, None
    return best_model, prompt_total, completion_total


def _count(value: Any) -> int:
    try:
        return max(int(value), 0)
    except (TypeError, ValueError):
        return 0


async def record(
    *,
    organization_id: int | None,
    event_type: str,
    source: str,
    subject_key: str,
    workflow_id: int | None = None,
    workflow_run_id: int | None = None,
    user_id: int | None = None,
    input_ref: str | None = None,
    group_key: str | None = None,
    input_text: Any = None,
    model_output: Any = None,
    owner_final: Any = None,
    detail: Any = None,
    model: str | None = None,
    prompt_tokens: int | None = None,
    completion_tokens: int | None = None,
    scope: str = training_loop.SCOPE_AGENT,
    data: dict[str, Any] | None = None,
) -> bool:
    """Keep one event. True when a new row was written; False when it was
    switched off, already there, or could not be written. Never raises.

    ``workflow_run_id`` is only used to fill in the model and tokens when the
    caller did not have them.
    """
    if not organization_id or not training_loop.enabled(organization_id):
        return False
    if event_type not in training_loop.EVENT_TYPES:
        logger.warning("training_loop: {!r} is not an event type", event_type)
        return False
    if scope not in training_loop.SCOPES or (
        scope == training_loop.SCOPE_DECIBYL and workflow_id is not None
    ):
        # Decibyl's own thread has no agent; an event naming one is not it.
        logger.warning(
            "training_loop: {!r} with agent {} is not a scope", scope, workflow_id
        )
        return False
    try:
        if not await consent.use_feedback(organization_id):
            return False
        texts = {
            "input_text": _text(input_text),
            "model_output": _text(model_output),
            "owner_final": _text(owner_final),
            "detail": (_text(detail) or "")[:MAX_DETAIL_CHARS] or None,
        }
        if model is None and prompt_tokens is None and completion_tokens is None:
            model, prompt_tokens, completion_tokens = await run_usage(
                workflow_run_id, organization_id
            )
        return await db_client.insert_learning_event(
            {
                "organization_id": organization_id,
                "workflow_id": workflow_id,
                "user_id": user_id,
                "event_type": event_type,
                "source": source,
                "subject_key": subject_key[:MAX_KEY_CHARS],
                "input_ref": (input_ref or None) and input_ref[:MAX_REF_CHARS],
                "group_key": (group_key or None) and group_key[:MAX_REF_CHARS],
                "model": (model or None) and str(model)[:128],
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "scope": scope,
                "data": data,
                "consent_state": training_loop.GRANTED,
                "created_at": datetime.now(UTC),
                **texts,
            }
        )
    except Exception as exc:  # noqa: BLE001 - never fail what called us
        logger.warning(
            "training_loop: could not record {} for workspace {}: {}",
            event_type,
            organization_id,
            exc,
        )
        return False
