"""Recording what an agent actually did in somebody else's software.

Written from one wrapper around handler registration rather than from inside
each handler. That is the whole design decision: there are eight tool kinds
today and there will be more, and a recording line that has to be remembered
is a recording line that will be missed -- by which point the gap is invisible,
because missing rows look exactly like actions that never happened.

Never raises into a call. A failure to write a metric must not end a
conversation, so every path here swallows its own errors and logs. The cost of
that choice is a hole in the data; the cost of the other choice is a caller
hearing the line die because a database was slow.
"""

from __future__ import annotations

import time
from typing import Any, Awaitable, Callable, Optional

from loguru import logger

from api.db import db_client
from api.services.workflow import agent_timeline

STATUS_SUCCESS = "success"
STATUS_ERROR = "error"

#: A provider that answers with a stack trace should not be able to fill the
#: column. Long enough to keep the sentence that says what went wrong.
MAX_ERROR_CHARS = 1000


def classify_result(result: Any) -> tuple[str, Optional[str]]:
    """Read a handler's result the way the model will read it.

    Every tool here answers with ``{"status": "success"|"error", ...}``, which
    is the contract the LLM sees. Reading the same field the model reads means
    this table can never disagree with what the agent was told -- an important
    property when the table is the evidence for whether the product worked.

    Anything that is not that shape counts as success: a handler returning a
    bare string has done its job, and inventing a failure would be worse than
    recording a slightly vague one.
    """
    if isinstance(result, dict):
        status = result.get("status")
        if status == STATUS_ERROR:
            error = result.get("error")
            text = str(error) if error is not None else None
            return STATUS_ERROR, (text[:MAX_ERROR_CHARS] if text else None)
        if status == STATUS_SUCCESS:
            return STATUS_SUCCESS, None
    return STATUS_SUCCESS, None


async def record(
    *,
    organization_id: Optional[int],
    workflow_run_id: Optional[int],
    workflow_id: Optional[int],
    definition_id: Optional[int],
    kind: str,
    app: Optional[str],
    name: str,
    status: str,
    error: Optional[str],
    duration_ms: Optional[int],
) -> None:
    """Write one interaction. Silent on failure, by design -- see the module docstring."""
    if not organization_id:
        # Without a tenant the row cannot be read back by anyone who should see
        # it, and could be read by someone who should not. Dropping it is the
        # safe direction.
        logger.debug("Not recording app interaction {}: no organization", name)
        return
    try:
        await db_client.create_app_interaction(
            organization_id=organization_id,
            workflow_run_id=workflow_run_id,
            workflow_id=workflow_id,
            definition_id=definition_id,
            kind=kind,
            app=app,
            name=name,
            status=status,
            error=error,
            duration_ms=duration_ms,
        )
    except Exception as exc:  # noqa: BLE001 - a metric must never end a call
        logger.warning("Could not record app interaction {}: {}", name, exc)


def wrap_handler(
    handler: Callable[..., Awaitable[None]],
    *,
    kind: str,
    app: Optional[str],
    name: str,
    context: Callable[[], Awaitable[dict[str, Optional[int]]]],
) -> Callable[..., Awaitable[None]]:
    """Return the handler, timed and recorded.

    The result is captured by substituting ``params.result_callback`` rather
    than by reading a return value, because these handlers return None and
    deliver their answer through that callback. The substitute passes
    everything through untouched and keeps a copy; the pipeline cannot tell
    the difference.

    Timing stops when the handler hands its result to the callback, not when
    the coroutine finishes, because the first is what the caller waited
    through and the second includes whatever cleanup happened after the agent
    already had its answer.
    """

    async def recording_handler(params: Any) -> None:
        started = time.perf_counter()
        captured: dict[str, Any] = {}
        original = params.result_callback

        async def capture(result: Any, *args: Any, **kwargs: Any) -> Any:
            if "elapsed_ms" not in captured:
                captured["elapsed_ms"] = int((time.perf_counter() - started) * 1000)
            captured["result"] = result
            return await original(result, *args, **kwargs)

        params.result_callback = capture
        try:
            await handler(params)
            status, error = classify_result(captured.get("result"))
        except Exception as exc:
            # The handler blew up rather than returning an error envelope. That
            # is the most interesting row in the table and the easiest one to
            # lose, so it is recorded before the exception carries on.
            status, error = STATUS_ERROR, str(exc)[:MAX_ERROR_CHARS]
            captured.setdefault(
                "elapsed_ms", int((time.perf_counter() - started) * 1000)
            )
            await _safe_record(
                context,
                kind=kind,
                app=app,
                name=name,
                status=status,
                error=error,
                duration_ms=captured.get("elapsed_ms"),
            )
            raise
        finally:
            params.result_callback = original

        await _safe_record(
            context,
            kind=kind,
            app=app,
            name=name,
            status=status,
            error=error,
            duration_ms=captured.get("elapsed_ms"),
        )

    return recording_handler


async def _safe_record(
    context: Callable[[], Awaitable[dict[str, Optional[int]]]],
    **fields: Any,
) -> None:
    try:
        ids = await context()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not resolve context for an app interaction: {}", exc)
        return
    await record(
        organization_id=ids.get("organization_id"),
        workflow_run_id=ids.get("workflow_run_id"),
        workflow_id=ids.get("workflow_id"),
        definition_id=ids.get("definition_id"),
        **fields,
    )

    # The same facts, as a line on the timeline. Written here rather than in
    # each handler for the reason this module exists at all: a recording line
    # that has to be remembered is a recording line that will be missed, and a
    # tool kind added next year gets a timeline entry without anybody thinking
    # about it.
    #
    # Two writes rather than one because they answer different questions for
    # different people. The row above is the support record -- what the tool
    # returned, how long it took, the error verbatim. This is what happened on
    # the call, in a sentence. Collapsing them would make one of the two
    # answers worse.
    #
    # Independent on purpose: a timeline that will not write must not cost us
    # the support row, and `agent_timeline.record` swallows its own failures
    # for the same reason this function does.
    try:
        await agent_timeline.record_action(
            organization_id=ids.get("organization_id"),
            workflow_id=ids.get("workflow_id"),
            definition_id=ids.get("definition_id"),
            workflow_run_id=ids.get("workflow_run_id"),
            name=fields.get("name") or "",
            app=fields.get("app"),
            status=fields.get("status") or STATUS_SUCCESS,
            error=fields.get("error"),
        )
    except Exception as exc:  # noqa: BLE001 - never into a live call
        logger.warning("Could not record the timeline line for {}: {}", fields, exc)
