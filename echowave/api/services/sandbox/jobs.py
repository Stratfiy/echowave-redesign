"""One job, end to end: start the box, bridge its tool calls, keep the record.

The loop lives here, on the harness side, and so does every credential.
The box asks for a tool by name; the harness decides whether that tool is
one this bot may use, makes the call with the account's connectors, and
hands the result back. The box never learns how.

The record is written as it happens: a row when the job starts, the call
count as calls are made, the outcome when it ends. A box that dies mid-way
leaves a row that says so and holds the output up to that point, which is
what "state survives a sandbox crash" means in practice.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Awaitable, Callable, Optional

from loguru import logger

from api.db import db_client
from api.services.sandbox import protocol
from api.services.sandbox.runner import (
    Event,
    Limits,
    Runner,
    SandboxUnavailable,
    default_runner,
)

#: How a script names a tool the harness answers itself rather than
#: forwarding to a connector.
READ_SPILLED = "spilled"

ToolCallback = Callable[[str, dict[str, Any]], Awaitable[Any]]

RUNNING = "running"
DONE = "done"
FAILED = "failed"
TIMED_OUT = "timed_out"
CAPPED = "capped"


@dataclass
class JobResult:
    job_id: int | None
    status: str
    exit_code: int | None
    output: str
    error: str
    calls: int
    started_at: datetime
    finished_at: datetime
    #: The last few tool calls, for the timeline.
    call_log: list[dict[str, Any]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.status == DONE and self.exit_code == 0

    def as_result(self) -> dict[str, Any]:
        """What the model is told."""
        out: dict[str, Any] = {
            "status": "success" if self.ok else "error",
            "output": self.output[-8_000:],
            "calls": self.calls,
            "seconds": round((self.finished_at - self.started_at).total_seconds(), 1),
        }
        if not self.ok:
            reason = {
                TIMED_OUT: "the script ran past its time limit",
                CAPPED: f"the script made more than {protocol.DEFAULT_MAX_CALLS} tool calls",
                FAILED: "the script could not run",
            }.get(self.status, "the script failed")
            out["error"] = (self.error[-2_000:] or reason).strip()
        return out


def _hash(code: str) -> str:
    return hashlib.sha256(code.encode("utf-8")).hexdigest()[:16]


async def run(
    *,
    organization_id: int,
    code: str,
    tools: ToolCallback,
    workflow_id: int | None = None,
    workflow_run_id: int | None = None,
    max_calls: int = protocol.DEFAULT_MAX_CALLS,
    limits: Limits | None = None,
    runner: Runner | None = None,
    spilled_reader: Optional[Callable[[str], Awaitable[Any]]] = None,
) -> JobResult:
    """Run one script for one organisation. Never raises: a box that could
    not start, died, ran out of time or made too many calls is a result
    that says which."""
    code = (code or "")[: protocol.MAX_CODE_CHARS]
    limits = limits or Limits()
    started = datetime.now(UTC)
    calls = 0
    call_log: list[dict[str, Any]] = []

    row_id: int | None = None
    try:
        row = await db_client.create_sandbox_job(
            organization_id=organization_id,
            workflow_id=workflow_id,
            workflow_run_id=workflow_run_id,
            code_hash=_hash(code),
            code_chars=len(code),
        )
        row_id = int(row.id)
    except Exception as exc:  # noqa: BLE001 - the run must still happen
        logger.warning(
            "Could not record sandbox job for org {}: {}", organization_id, exc
        )

    async def finish(status: str, event: Event | None, error: str = "") -> JobResult:
        result = JobResult(
            job_id=row_id,
            status=status,
            exit_code=event.exit_code if event else None,
            output=(event.output if event else "")[: protocol.MAX_OUTPUT_CHARS],
            error=(event.error if event else "") or error,
            calls=calls,
            started_at=started,
            finished_at=datetime.now(UTC),
            call_log=call_log[-20:],
        )
        if row_id is not None:
            try:
                await db_client.finish_sandbox_job(
                    row_id,
                    status=status,
                    exit_code=result.exit_code,
                    calls=calls,
                    output=result.output[-16_000:],
                    error=result.error[-4_000:],
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("Could not finish sandbox job {}: {}", row_id, exc)
        return result

    try:
        runner = runner or default_runner()
        job = await runner.start(code, limits)
    except SandboxUnavailable as exc:
        return await finish(FAILED, None, str(exc))
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "Sandbox could not start a job for org {}: {}", organization_id, exc
        )
        return await finish(FAILED, None, "the sandbox could not start")

    try:
        while True:
            try:
                event = await runner.next(job, wait=20.0)
            except SandboxUnavailable as exc:
                return await finish(FAILED, None, str(exc))
            if event is None:
                continue
            if event.kind == "done":
                status = TIMED_OUT if event.timed_out else DONE
                return await finish(status, event)

            request = event.request or {}
            call_id = int(request.get("id") or 0)
            name = str(request.get("tool") or "")
            args = dict(request.get("args") or {})
            calls += 1
            if calls > max_calls:
                await runner.reply(
                    job,
                    call_id,
                    {"status": "error", "error": f"call limit of {max_calls} reached"},
                )
                await runner.stop(job)
                return await finish(CAPPED, None)
            if name == READ_SPILLED and spilled_reader is not None:
                try:
                    result = await spilled_reader(str(args.get("stored_as") or ""))
                except Exception as exc:  # noqa: BLE001
                    result = {"status": "error", "error": str(exc)}
            else:
                try:
                    result = await tools(name, args)
                except Exception as exc:  # noqa: BLE001 - the script hears it
                    logger.warning("Sandbox tool {} failed: {}", name, exc)
                    result = {"status": "error", "error": str(exc)[:500]}
            call_log.append(
                {
                    "id": call_id,
                    "tool": name,
                    "ok": not (
                        isinstance(result, dict) and result.get("status") == "error"
                    ),
                }
            )
            if row_id is not None and calls % 10 == 0:
                try:
                    await db_client.touch_sandbox_job(row_id, calls=calls)
                except Exception:  # noqa: BLE001
                    pass
            try:
                await runner.reply(job, call_id, result)
            except SandboxUnavailable as exc:
                return await finish(FAILED, None, str(exc))
    finally:
        try:
            await runner.stop(job)
        except Exception:  # noqa: BLE001
            pass


__all__ = [
    "CAPPED",
    "DONE",
    "FAILED",
    "READ_SPILLED",
    "RUNNING",
    "TIMED_OUT",
    "JobResult",
    "run",
]
