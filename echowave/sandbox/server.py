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
import hashlib
import io
import json
import os
import re
import shlex
import tarfile
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Request
from pydantic import BaseModel, Field


def _secret() -> str:
    """SANDBOX_SECRET, or the value the api derives when it is unset.

    Kept in step with ``_derived_sandbox_secret`` in ``api/constants.py``:
    both hash OSS_JWT_SECRET (here SANDBOX_SECRET_SEED) with the same label,
    so a deployment that never set SANDBOX_SECRET still has one shared value.
    """
    explicit = os.environ.get("SANDBOX_SECRET", "")
    if explicit:
        return explicit
    seed = os.environ.get("SANDBOX_SECRET_SEED", "")
    if not seed:
        return ""
    return hashlib.sha256(b"decibyl-sandbox/v1:" + seed.encode("utf-8")).hexdigest()


SECRET = _secret()
MAX_JOBS = int(os.environ.get("SANDBOX_MAX_JOBS", "2"))
IMAGE = os.environ.get("SANDBOX_IMAGE", "python:3.12-slim")
SENTINEL = "@@decibyl-tool@@"
MAX_OUTPUT_CHARS = 64_000

# --- Images, pulled on start ---------------------------------------------------
#
# Studio's build and screenshot boxes run images that are not on a fresh host,
# and the screenshot one is about 2 GB. Pulled inside a request, that download
# would count against the request's timeout and fail it, again and again, until
# somebody logged in to the server to pull it by hand. So the service pulls
# them itself when it starts, reports progress on /health, and a request that
# arrives first is told to wait rather than timing out.

IMAGE_STATE: dict[str, str] = {}  # image -> "pulling" | "ready" | "failed"
_PULL_RETRY_SECONDS = 300


async def _image_present(image: str) -> bool:
    proc = await asyncio.create_subprocess_exec(
        "docker",
        "image",
        "inspect",
        image,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    return await proc.wait() == 0


async def _pull(image: str) -> bool:
    proc = await asyncio.create_subprocess_exec(
        "docker",
        "pull",
        "-q",
        image,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    return await proc.wait() == 0


async def _ensure_image(image: str) -> None:
    """Pull ``image`` until it is present; retried, never raises."""
    while True:
        try:
            if await _image_present(image):
                IMAGE_STATE[image] = "ready"
                return
            IMAGE_STATE[image] = "pulling"
            if await _pull(image):
                IMAGE_STATE[image] = "ready"
                return
        except OSError:
            # No docker binary or socket: the boxes cannot run either, and
            # /health says so rather than this task dying unseen.
            pass
        IMAGE_STATE[image] = "failed"
        await asyncio.sleep(_PULL_RETRY_SECONDS)


def _studio_images() -> list[str]:
    images = [IMAGE] if IMAGE else []
    if BUILD_NETWORK:
        images += [BUILD_IMAGE, SHOT_IMAGE]
    return list(dict.fromkeys(images))


def _require_image(image: str) -> None:
    """Refuse, with the reason, while an image is still being fetched."""
    state = IMAGE_STATE.get(image)
    if state in ("pulling", "failed"):
        raise HTTPException(
            status_code=503,
            detail=(
                "The sandbox is still downloading what it needs for this "
                "(first start on this server). Try again in a few minutes."
            ),
        )


@asynccontextmanager
async def _lifespan(_app):
    tasks = [asyncio.create_task(_ensure_image(image)) for image in _studio_images()]
    yield
    for task in tasks:
        task.cancel()


app = FastAPI(
    title="Decibyl sandbox", docs_url=None, redoc_url=None, lifespan=_lifespan
)


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
    _require_image(BUILD_IMAGE)
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


# --- Screenshots of a built site (Studio's design review) --------------------
#
# The built files go in, two PNGs come out: a desktop and a phone view, with
# any script errors the page threw and how far it scrolls sideways. The box
# is the stricter kind again -- ``--network none`` -- because nothing in it
# needs more than loopback: Python's own static server and Chromium, driven
# by shoot.mjs over the DevTools protocol.

SHOT_IMAGE = os.environ.get(
    "SANDBOX_SHOT_IMAGE", "mcr.microsoft.com/playwright:v1.56.0-noble"
)
with open(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "shoot.mjs"), "rb"
) as _shoot:
    _SHOOT_JS = _shoot.read()
MAX_SHOT_OUTPUT_BYTES = 12 * 1024 * 1024


class ScreenshotRequest(BaseModel):
    #: Relative path -> base64 of the built file, as /builds returned it.
    files: dict[str, str]
    timeout_seconds: int = Field(default=60, ge=10, le=120)


def _dist_tarball(files: dict[str, str]) -> bytes:
    buffer = io.BytesIO()
    total = 0
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        for raw_path, encoded in files.items():
            try:
                data = base64.b64decode(encoded)
            except (binascii.Error, ValueError) as exc:
                raise HTTPException(status_code=400, detail="bad file data") from exc
            total += len(data)
            if total > MAX_BUILD_OUTPUT_BYTES:
                raise HTTPException(status_code=413, detail="site too large")
            info = tarfile.TarInfo(name=_clean_path(raw_path))
            info.size = len(data)
            info.mode = 0o644
            tar.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


def _shot_command(shot_id: str) -> list[str]:
    script = (
        'echo "$SHOOT" | base64 -d > /tmp/shoot.mjs\n'
        "mkdir -p /work/site\n"
        "tar -xzf - -C /work/site\n"
        "cd /work/site\n"
        "python3 -m http.server 4173 --bind 127.0.0.1 >/dev/null 2>&1 &\n"
        "sleep 0.5\n"
        "node /tmp/shoot.mjs\n"
    )
    return [
        "docker",
        "run",
        "-i",
        "--rm",
        "--name",
        f"sandbox-shot-{shot_id}",
        "--network",
        "none",
        "--memory",
        "1024m",
        "--memory-swap",
        "1024m",
        "--cpus",
        "1.0",
        "--pids-limit",
        "256",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--read-only",
        "--tmpfs",
        "/work:rw,size=128m,uid=1000,gid=1000",
        # Chromium keeps its profile and shared memory under /tmp.
        "--tmpfs",
        "/tmp:rw,exec,size=512m,uid=1000,gid=1000",
        "--shm-size",
        "256m",
        "--user",
        "1000:1000",
        "--env",
        "HOME=/tmp",
        "--env",
        f"SHOOT={base64.b64encode(_SHOOT_JS).decode('ascii')}",
        SHOT_IMAGE,
        "sh",
        "-c",
        script,
    ]


@app.post("/screenshots")
async def screenshots(
    request: ScreenshotRequest, x_sandbox_secret: str | None = Header(default=None)
):
    _check(x_sandbox_secret)
    if "index.html" not in {_clean_path(p) for p in request.files}:
        raise HTTPException(status_code=400, detail="index.html is missing")
    _require_image(SHOT_IMAGE)
    live = sum(1 for j in JOBS.values() if j.done is None) + len(BUILDS)
    if live >= MAX_JOBS:
        raise HTTPException(status_code=429, detail="sandbox is full")

    source = _dist_tarball(request.files)
    shot_id = uuid.uuid4().hex[:12]
    BUILDS.add(shot_id)
    try:
        process = await asyncio.create_subprocess_exec(
            *_shot_command(shot_id),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, _ = await asyncio.wait_for(
                process.communicate(source), timeout=request.timeout_seconds
            )
        except asyncio.TimeoutError:
            try:
                process.kill()
            except ProcessLookupError:
                pass
            remover = await asyncio.create_subprocess_exec(
                "docker",
                "rm",
                "-f",
                f"sandbox-shot-{shot_id}",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await remover.wait()
            return {
                "shots": [],
                "errors": ["The page took too long to render."],
                "overflow": {},
            }
    finally:
        BUILDS.discard(shot_id)

    line = stdout.strip().splitlines()[-1] if stdout.strip() else b""
    if not line or len(line) > MAX_SHOT_OUTPUT_BYTES:
        return {
            "shots": [],
            "errors": ["The screenshot could not be taken."],
            "overflow": {},
        }
    try:
        return json.loads(line)
    except ValueError:
        return {
            "shots": [],
            "errors": ["The screenshot could not be read."],
            "overflow": {},
        }


@app.get("/health")
async def health():
    live = sum(1 for j in JOBS.values() if j.done is None) + len(BUILDS)
    return {
        "ok": True,
        "running": live,
        "capacity": MAX_JOBS,
        "builds": bool(BUILD_NETWORK),
        "images": dict(IMAGE_STATE),
    }


def command_line(job_id: str, request: JobRequest) -> str:
    """For a person checking what a box is started with."""
    return " ".join(shlex.quote(part) for part in _docker_command(job_id, request))
