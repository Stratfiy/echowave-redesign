"""Where a box runs: the sandbox service in production, a subprocess in a test.

Two runners, one shape. :class:`RemoteRunner` talks to the ``sandbox``
service (``sandbox/server.py``), which starts one Docker container per job
with its network removed and relays the box's lines over four small HTTP
calls. :class:`LocalRunner` runs the same prelude in a plain subprocess on
this machine with no isolation at all, for tests and a developer's laptop,
and refuses to run anywhere that looks like production.

The bridge in ``jobs.py`` does not know which it has. That is the point of
the protocol: a request line out, a reply line in, and whether the other end
is a container or a child process is a deployment detail.
"""

from __future__ import annotations

import asyncio
import os
import sys
from dataclasses import dataclass, field
from typing import Any, Optional, Protocol

import httpx
from loguru import logger

from api.constants import DEPLOYMENT_MODE, SANDBOX_SECRET, SANDBOX_URL
from api.services.sandbox import protocol


@dataclass
class Limits:
    timeout_seconds: int = protocol.DEFAULT_TIMEOUT_SECONDS
    memory_mb: int = protocol.DEFAULT_MEMORY_MB
    cpus: float = protocol.DEFAULT_CPUS


@dataclass
class Event:
    """One thing the box did: asked for a tool, or finished."""

    kind: str  # "request" | "done"
    request: dict[str, Any] | None = None
    exit_code: int | None = None
    output: str = ""
    error: str = ""
    timed_out: bool = False


class Runner(Protocol):
    async def start(self, code: str, limits: Limits) -> str: ...

    async def next(self, job: str, *, wait: float) -> Event | None: ...

    async def reply(self, job: str, call_id: int, result: Any) -> None: ...

    async def stop(self, job: str) -> None: ...


class SandboxUnavailable(RuntimeError):
    """No box can be had right now. The caller says so, never half-runs."""


# ---------------------------------------------------------------------------
# Production: the sandbox service


class RemoteRunner:
    """The ``sandbox`` service over HTTP on the compose network.

    Four calls: start a job, long-poll for its next event, reply to a
    request, stop it. The service holds no credentials and never calls us
    back; everything it knows about a job is the code and the limits.
    """

    def __init__(self, url: str, secret: str, *, timeout: float = 35.0):
        self._url = url.rstrip("/")
        self._headers = {"x-sandbox-secret": secret}
        self._timeout = timeout

    async def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self._url, headers=self._headers, timeout=self._timeout
        )

    async def start(self, code: str, limits: Limits) -> str:
        try:
            async with await self._client() as client:
                response = await client.post(
                    "/jobs",
                    json={
                        "prelude": protocol.prelude(),
                        "code": code,
                        "timeout_seconds": limits.timeout_seconds,
                        "memory_mb": limits.memory_mb,
                        "cpus": limits.cpus,
                    },
                )
        except httpx.HTTPError as exc:
            raise SandboxUnavailable(f"sandbox service unreachable: {exc}") from exc
        if response.status_code == 429:
            raise SandboxUnavailable("sandbox is full; try again in a minute")
        if response.status_code >= 400:
            raise SandboxUnavailable(
                f"sandbox refused the job: HTTP {response.status_code}"
            )
        return str(response.json()["id"])

    async def next(self, job: str, *, wait: float) -> Event | None:
        try:
            async with await self._client() as client:
                response = await client.get(
                    f"/jobs/{job}/next", params={"wait": max(0.0, min(wait, 25.0))}
                )
                response.raise_for_status()
                body = response.json()
        except httpx.HTTPError as exc:
            raise SandboxUnavailable(f"sandbox service lost: {exc}") from exc
        if body.get("request"):
            return Event(kind="request", request=body["request"])
        if body.get("done"):
            done = body["done"]
            return Event(
                kind="done",
                exit_code=done.get("exit_code"),
                output=str(done.get("output") or ""),
                error=str(done.get("error") or ""),
                timed_out=bool(done.get("timed_out")),
            )
        return None

    async def reply(self, job: str, call_id: int, result: Any) -> None:
        try:
            async with await self._client() as client:
                response = await client.post(
                    f"/jobs/{job}/reply",
                    content=protocol.encode_reply(call_id, result),
                    headers={"content-type": "application/json"},
                )
                response.raise_for_status()
        except httpx.HTTPError as exc:
            raise SandboxUnavailable(f"sandbox service lost: {exc}") from exc

    async def stop(self, job: str) -> None:
        try:
            async with await self._client() as client:
                await client.delete(f"/jobs/{job}")
        except httpx.HTTPError as exc:  # noqa: BLE001 - best effort
            logger.debug("Could not stop sandbox job {}: {}", job, exc)


# ---------------------------------------------------------------------------
# Development and tests: a subprocess


@dataclass
class _Local:
    process: asyncio.subprocess.Process
    output: list[str] = field(default_factory=list)
    output_chars: int = 0
    stderr_task: Optional[asyncio.Task] = None
    stderr: list[str] = field(default_factory=list)
    deadline: float = 0.0
    timed_out: bool = False


class LocalRunner:
    """The same prelude in a child process. **No isolation**: the script
    runs as this process's user, with this machine's network. For tests
    and a laptop; refused where ``DEPLOYMENT_MODE`` says production unless
    ``SANDBOX_ALLOW_LOCAL=1`` is set on purpose."""

    def __init__(self) -> None:
        if DEPLOYMENT_MODE not in ("oss", "test", "dev") and not os.getenv(
            "SANDBOX_ALLOW_LOCAL"
        ):
            raise SandboxUnavailable(
                "no sandbox service is configured, and running scripts on the "
                "api host is refused outside development"
            )
        self._jobs: dict[str, _Local] = {}
        self._n = 0

    async def start(self, code: str, limits: Limits) -> str:
        env = {
            "CODE": code,
            "PATH": os.environ.get("PATH", ""),
            "PYTHONIOENCODING": "utf-8",
            "PYTHONUNBUFFERED": "1",
        }
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-I",
            "-u",
            "-c",
            protocol.prelude(),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        self._n += 1
        job = f"local-{self._n}"
        local = _Local(process=process)
        local.deadline = asyncio.get_running_loop().time() + limits.timeout_seconds
        local.stderr_task = asyncio.create_task(self._drain_stderr(local))
        self._jobs[job] = local
        return job

    async def _drain_stderr(self, local: _Local) -> None:
        assert local.process.stderr is not None
        async for raw in local.process.stderr:
            local.stderr.append(raw.decode("utf-8", "replace"))

    async def next(self, job: str, *, wait: float) -> Event | None:
        local = self._jobs[job]
        assert local.process.stdout is not None
        loop = asyncio.get_running_loop()
        while True:
            remaining = local.deadline - loop.time()
            if remaining <= 0:
                local.timed_out = True
                await self.stop(job)
                return await self._done(job)
            try:
                raw = await asyncio.wait_for(
                    local.process.stdout.readline(), timeout=min(wait, remaining)
                )
            except asyncio.TimeoutError:
                return None
            if not raw:
                return await self._done(job)
            line = raw.decode("utf-8", "replace").rstrip("\n")
            request = protocol.decode_request(line)
            if request is not None:
                return Event(kind="request", request=request)
            if local.output_chars < protocol.MAX_OUTPUT_CHARS:
                local.output.append(line)
                local.output_chars += len(line) + 1

    async def _done(self, job: str) -> Event:
        local = self._jobs[job]
        code = await local.process.wait()
        if local.stderr_task:
            try:
                await asyncio.wait_for(local.stderr_task, timeout=2)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                pass
        return Event(
            kind="done",
            exit_code=code,
            output="\n".join(local.output)[: protocol.MAX_OUTPUT_CHARS],
            error="".join(local.stderr)[-4_000:],
            timed_out=local.timed_out,
        )

    async def reply(self, job: str, call_id: int, result: Any) -> None:
        local = self._jobs[job]
        assert local.process.stdin is not None
        local.process.stdin.write(
            (protocol.encode_reply(call_id, result) + "\n").encode()
        )
        await local.process.stdin.drain()

    async def stop(self, job: str) -> None:
        local = self._jobs.get(job)
        if local is None or local.process.returncode is not None:
            return
        try:
            local.process.kill()
        except ProcessLookupError:
            pass


def default_runner() -> Runner:
    """The runner this deployment uses: the service when configured, else
    the local one where that is allowed."""
    if SANDBOX_URL:
        return RemoteRunner(SANDBOX_URL, SANDBOX_SECRET or "")
    return LocalRunner()


__all__ = [
    "Event",
    "Limits",
    "LocalRunner",
    "RemoteRunner",
    "Runner",
    "SandboxUnavailable",
    "default_runner",
]
