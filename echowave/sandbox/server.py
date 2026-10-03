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
import base64
import binascii
import io
import os
import re
import shlex
import tarfile
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


# --- Site builds (Studio) -----------------------------------------------------
#
# A build is a different animal from a script. It needs Node, room for
# node_modules, and packages from a registry. It gets them under the same
# discipline as a script box -- unprivileged, capabilities dropped, read-only
# root -- and one difference that is the whole point of having a separate
# entry: a network. Only the one named in SANDBOX_BUILD_NETWORK, which compose
# declares ``internal`` with the registry mirror as its only other member. A
# build can fetch packages and can reach nothing else: not the api, not the
# database, not the metadata endpoint, not the internet.
#
# Unset, builds are refused rather than run on some other network. A build
# that quietly fell back to the default bridge would have the internet.

BUILD_NETWORK = os.environ.get("SANDBOX_BUILD_NETWORK", "")
BUILD_IMAGE = os.environ.get("SANDBOX_BUILD_IMAGE", "node:22-slim")
BUILD_REGISTRY = os.environ.get(
    "SANDBOX_BUILD_REGISTRY", "http://sandbox-registry:4873/"
)
MAX_BUILD_SOURCE_BYTES = 5 * 1024 * 1024
MAX_BUILD_OUTPUT_BYTES = 25 * 1024 * 1024
MAX_BUILD_LOG_CHARS = 20_000
_SAFE_DIR = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")

BUILDS: set[str] = set()


class BuildRequest(BaseModel):
    #: Relative path -> UTF-8 text. Binary assets are out of scope for v1.
    files: dict[str, str]
    output_dir: str = "dist"
    timeout_seconds: int = Field(default=600, ge=30, le=900)
    memory_mb: int = Field(default=2048, ge=512, le=4096)
    cpus: float = Field(default=2.0, gt=0, le=4.0)


def _clean_path(path: str) -> str:
    """A path inside /work, or raise. No absolute paths, no ``..``."""
    parts = [p for p in path.replace("\\", "/").split("/") if p not in ("", ".")]
    if not parts or any(p == ".." for p in parts) or path.startswith("/"):
        raise HTTPException(status_code=400, detail=f"bad path {path!r}")
    return "/".join(parts)


def _source_tarball(files: dict[str, str]) -> bytes:
    buffer = io.BytesIO()
    total = 0
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        for raw_path, text in files.items():
            data = text.encode("utf-8")
            total += len(data)
            if total > MAX_BUILD_SOURCE_BYTES:
                raise HTTPException(status_code=413, detail="source too large")
            info = tarfile.TarInfo(name=_clean_path(raw_path))
            info.size = len(data)
            info.mode = 0o644
            tar.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


def _build_script(output_dir: str) -> str:
    # Every line of npm's and the bundler's chatter goes to stderr, so stdout
    # carries exactly one thing: the built output as base64.
    return (
        "set -e\n"
        "tar -xzf - -C /work\n"
        "{ npm install --no-audit --no-fund --loglevel=error"
        " && npm run build; } 1>&2\n"
        f"cd /work/{output_dir}\n"
        "tar -czf - . | base64 -w0\n"
    )


def _build_command(build_id: str, request: BuildRequest) -> list[str]:
    return [
        "docker",
        "run",
        "-i",
        "--rm",
        "--name",
        f"sandbox-build-{build_id}",
        "--network",
        BUILD_NETWORK,
        "--memory",
        f"{request.memory_mb}m",
        "--memory-swap",
        f"{request.memory_mb}m",
        "--cpus",
        str(request.cpus),
        "--pids-limit",
        "1024",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--read-only",
        # exec on both: node_modules carries native binaries (esbuild, rollup)
        # that the bundler runs.
        "--tmpfs",
        "/work:rw,exec,size=1536m,uid=1000,gid=1000",
        "--tmpfs",
        "/tmp:rw,exec,size=512m,uid=1000,gid=1000",
        "--user",
        "1000:1000",
        "--workdir",
        "/work",
        "--env",
        "HOME=/tmp",
        "--env",
        "CI=1",
        "--env",
        f"npm_config_registry={BUILD_REGISTRY}",
        "--env",
        "npm_config_cache=/tmp/.npm",
        "--env",
        "npm_config_update_notifier=false",
        BUILD_IMAGE,
        "sh",
        "-c",
        _build_script(request.output_dir),
    ]


def _unpack_output(encoded: bytes) -> dict[str, str]:
    """The built tree as path -> base64 of the file's bytes."""
    raw = base64.b64decode(encoded.strip() or b"")
    if len(raw) > MAX_BUILD_OUTPUT_BYTES:
        raise ValueError("build output too large")
    out: dict[str, str] = {}
    total = 0
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as tar:
        for member in tar.getmembers():
            if not member.isfile():
                continue
            name = member.name.removeprefix("./")
            if not name or name.startswith("/") or ".." in name.split("/"):
                continue
            handle = tar.extractfile(member)
            if handle is None:
                continue
            data = handle.read()
            total += len(data)
            if total > MAX_BUILD_OUTPUT_BYTES:
                raise ValueError("build output too large")
            out[name] = base64.b64encode(data).decode("ascii")
    return out


@app.post("/builds")
async def build(
    request: BuildRequest, x_sandbox_secret: str | None = Header(default=None)
):
    """Install and build one site. Synchronous: the answer is the result."""
    _check(x_sandbox_secret)
    if not BUILD_NETWORK:
        raise HTTPException(
            status_code=503,
            detail="site builds are not configured (SANDBOX_BUILD_NETWORK)",
        )
    if not _SAFE_DIR.match(request.output_dir):
        raise HTTPException(status_code=400, detail="bad output_dir")
    if "package.json" not in {_clean_path(p) for p in request.files}:
        raise HTTPException(status_code=400, detail="package.json is missing")
    live = sum(1 for j in JOBS.values() if j.done is None) + len(BUILDS)
    if live >= MAX_JOBS:
        raise HTTPException(status_code=429, detail="sandbox is full")

    source = _source_tarball(request.files)
    build_id = uuid.uuid4().hex[:12]
    BUILDS.add(build_id)
    started = time.monotonic()
    timed_out = False
    try:
        process = await asyncio.create_subprocess_exec(
            *_build_command(build_id, request),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(source), timeout=request.timeout_seconds
            )
        except asyncio.TimeoutError:
            timed_out = True
            stdout, stderr = b"", b""
            try:
                process.kill()
            except ProcessLookupError:
                pass
            remover = await asyncio.create_subprocess_exec(
                "docker",
                "rm",
                "-f",
                f"sandbox-build-{build_id}",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await remover.wait()
    finally:
        BUILDS.discard(build_id)

    log = stderr.decode("utf-8", "replace")[-MAX_BUILD_LOG_CHARS:]
    result: dict[str, Any] = {
        "exit_code": None if timed_out else process.returncode,
        "timed_out": timed_out,
        "seconds": round(time.monotonic() - started, 1),
        "log": log,
        "files": {},
    }
    if not timed_out and process.returncode == 0:
        try:
            result["files"] = _unpack_output(stdout)
        except (ValueError, tarfile.TarError, binascii.Error) as exc:
            result["exit_code"] = -1
            result["log"] = (log + f"\nCould not read the build output: {exc}")[
                -MAX_BUILD_LOG_CHARS:
            ]
    return result


@app.get("/health")
async def health():
    live = sum(1 for j in JOBS.values() if j.done is None) + len(BUILDS)
    return {
        "ok": True,
        "running": live,
        "capacity": MAX_JOBS,
        "builds": bool(BUILD_NETWORK),
    }


def command_line(job_id: str, request: JobRequest) -> str:
    """For a person checking what a box is started with."""
    return " ".join(shlex.quote(part) for part in _docker_command(job_id, request))
