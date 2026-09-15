"""The sandbox service: one Docker container per job, network removed.

Small on purpose. It knows how to start a box, relay the lines the box
prints, hand replies to its stdin, and kill it. It holds no credentials,
never calls anything, and does not know what the script does. The harness
(the api) owns the loop and the tools; this owns ``docker run``.

What a box gets, from the flags below, is stricter than the candidate we
measured in the Steps 5 and 6 evaluation: unprivileged, seccomp on, every
capability dropped, no new privileges, a read-only root, a small tmpfs to
work in, a CPU and memory ceiling, a process cap, and ``--network none``.
The host's metadata endpoint is out of reach because there is no network
at all.

Four calls, all behind ``x-sandbox-secret``::

    POST   /jobs                {prelude, code, timeout_seconds, memory_mb, cpus}
    GET    /jobs/{id}/next?wait  -> {request} | {done} | {}
    POST   /jobs/{id}/reply     one JSON line for the box's stdin
    DELETE /jobs/{id}

At most ``SANDBOX_MAX_JOBS`` boxes at once (two, to start); a request past
that is a 429 the harness turns into "try again in a minute".
"""

from __future__ import annotations

import asyncio
import os
import shlex
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Request
from pydantic import BaseModel, Field

SECRET = os.environ.get("SANDBOX_SECRET", "")
MAX_JOBS = int(os.environ.get("SANDBOX_MAX_JOBS", "2"))
IMAGE = os.environ.get("SANDBOX_IMAGE", "python:3.12-slim")
SENTINEL = "@@decibyl-tool@@"
MAX_OUTPUT_CHARS = 64_000

app = FastAPI(title="Decibyl sandbox", docs_url=None, redoc_url=None)


class JobRequest(BaseModel):
    prelude: str
    code: str = Field(max_length=64_000)
    timeout_seconds: int = Field(default=300, ge=5, le=900)
    memory_mb: int = Field(default=512, ge=64, le=2048)
    cpus: float = Field(default=1.0, gt=0, le=2.0)


@dataclass
class Job:
    id: str
    process: asyncio.subprocess.Process
    deadline: float
    requests: asyncio.Queue = field(default_factory=asyncio.Queue)
    output: list[str] = field(default_factory=list)
    output_chars: int = 0
    stderr: list[str] = field(default_factory=list)
    done: dict[str, Any] | None = None
    timed_out: bool = False
    reader: asyncio.Task | None = None
    watchdog: asyncio.Task | None = None


JOBS: dict[str, Job] = {}


def _check(secret: str | None) -> None:
    if not SECRET or secret != SECRET:
        raise HTTPException(status_code=401, detail="no")


def _docker_command(job_id: str, request: JobRequest) -> list[str]:
    return [
        "docker",
        "run",
        "-i",
        "--rm",
        "--name",
        f"sandbox-{job_id}",
        "--network",
        "none",
        "--memory",
        f"{request.memory_mb}m",
        "--memory-swap",
        f"{request.memory_mb}m",
        "--cpus",
        str(request.cpus),
        "--pids-limit",
        "256",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--read-only",
        "--tmpfs",
        "/work:rw,size=64m,uid=65534,gid=65534",
        "--tmpfs",
        "/tmp:rw,size=16m,uid=65534,gid=65534",
        "--user",
        "65534:65534",
        "--workdir",
        "/work",
        "--env",
        "PYTHONIOENCODING=utf-8",
        "--env",
        "PYTHONUNBUFFERED=1",
        "--env",
        "HOME=/work",
        "--env",
        f"CODE={request.code}",
        IMAGE,
        "python",
        "-I",
        "-u",
        "-c",
        request.prelude,
    ]


async def _read_stdout(job: Job) -> None:
    assert job.process.stdout is not None
    async for raw in job.process.stdout:
        line = raw.decode("utf-8", "replace").rstrip("\n")
        if line.startswith(SENTINEL):
            await job.requests.put(line[len(SENTINEL) :])
            continue
        if job.output_chars < MAX_OUTPUT_CHARS:
            job.output.append(line)
            job.output_chars += len(line) + 1
    code = await job.process.wait()
    stderr = b""
    if job.process.stderr is not None:
        try:
            stderr = await asyncio.wait_for(job.process.stderr.read(), timeout=2)
        except asyncio.TimeoutError:
            pass
    job.done = {
        "exit_code": code,
        "output": "\n".join(job.output)[:MAX_OUTPUT_CHARS],
        "error": stderr.decode("utf-8", "replace")[-4_000:],
        "timed_out": job.timed_out,
    }
    await job.requests.put(None)


async def _watchdog(job: Job) -> None:
    await asyncio.sleep(max(0.0, job.deadline - time.monotonic()))
    if job.done is None:
        job.timed_out = True
        await _kill(job)


async def _kill(job: Job) -> None:
    if job.process.returncode is None:
        try:
            job.process.kill()
        except ProcessLookupError:
            pass
    # The container may outlive the client process for a moment.
    proc = await asyncio.create_subprocess_exec(
        "docker",
        "rm",
        "-f",
        f"sandbox-{job.id}",
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    await proc.wait()


@app.post("/jobs")
async def start(
    request: JobRequest, x_sandbox_secret: str | None = Header(default=None)
):
    _check(x_sandbox_secret)
    live = [j for j in JOBS.values() if j.done is None]
    if len(live) >= MAX_JOBS:
        raise HTTPException(status_code=429, detail="sandbox is full")
    job_id = uuid.uuid4().hex[:12]
    process = await asyncio.create_subprocess_exec(
        *_docker_command(job_id, request),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    job = Job(
        id=job_id, process=process, deadline=time.monotonic() + request.timeout_seconds
    )
    job.reader = asyncio.create_task(_read_stdout(job))
    job.watchdog = asyncio.create_task(_watchdog(job))
    JOBS[job_id] = job
    return {"id": job_id}


@app.get("/jobs/{job_id}/next")
async def next_event(
    job_id: str, wait: float = 20.0, x_sandbox_secret: str | None = Header(default=None)
):
    _check(x_sandbox_secret)
    job = JOBS.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="no such job")
    if job.done is not None and job.requests.empty():
        return {"done": job.done}
    try:
        item = await asyncio.wait_for(
            job.requests.get(), timeout=max(0.0, min(wait, 25.0))
        )
    except asyncio.TimeoutError:
        return {}
    if item is None:
        return {"done": job.done}
    return {"request": item}


@app.post("/jobs/{job_id}/reply")
async def reply(
    job_id: str, request: Request, x_sandbox_secret: str | None = Header(default=None)
):
    _check(x_sandbox_secret)
    job = JOBS.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="no such job")
    if job.process.stdin is None or job.process.returncode is not None:
        raise HTTPException(status_code=409, detail="the box has ended")
    body = (await request.body()).rstrip(b"\n") + b"\n"
    job.process.stdin.write(body)
    await job.process.stdin.drain()
    return {"ok": True}


@app.delete("/jobs/{job_id}")
async def stop(job_id: str, x_sandbox_secret: str | None = Header(default=None)):
    _check(x_sandbox_secret)
    job = JOBS.pop(job_id, None)
    if job is None:
        return {"ok": True}
    await _kill(job)
    if job.watchdog:
        job.watchdog.cancel()
    return {"ok": True}


@app.get("/health")
async def health():
    live = sum(1 for j in JOBS.values() if j.done is None)
    return {"ok": True, "running": live, "capacity": MAX_JOBS}


def command_line(job_id: str, request: JobRequest) -> str:
    """For a person checking what a box is started with."""
    return " ".join(shlex.quote(part) for part in _docker_command(job_id, request))
