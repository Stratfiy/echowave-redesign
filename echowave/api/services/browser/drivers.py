"""Where a browser runs: a box in the sandbox, a subprocess here, or a fake.

One interface, three drivers, the same line protocol (``PROTOCOL`` below).
The session loop (``session.py``) does not know which it has.

- :class:`SandboxDriver` -- production. The sandbox service starts one
  container per task (``POST /browsers``, ``sandbox/server.py``) from the
  browser image (``sandbox/browser/``), on the browser network: the
  internet, and nothing private -- no api, no database, no metadata
  endpoint, no other box. Unprivileged, every capability dropped, a
  read-only root and a tmpfs profile that dies with the box.
- :class:`LocalDriver` -- the same box script as a subprocess on this
  machine, for a developer and for verifying the real browser without
  Docker. **No container isolation** (the box's own proxy still refuses
  private addresses); refused where ``DEPLOYMENT_MODE`` says production.
- ``FakeDriver`` (``fake.py``) -- a scripted browser over real HTML for the
  tests. It runs the real gate, approvals, limits and cookies, because those
  live on this side of the protocol, not in the driver.

PROTOCOL. The box writes one JSON object per line, prefixed with
``SENTINEL``; anything else it prints is its own log. Types: ``hello``,
``llm`` (id, body: a Messages request -- wants a reply), ``gate`` (id, gate:
the step -- wants a reply), ``step``, ``screen``, ``state``, ``gate_result``,
``cookies``, ``done``. We write one JSON object per line to its stdin:
``{"cmd": "start", "spec": ...}`` first, then ``{"reply_to": id, "result":
...}`` answers and the commands ``takeover``, ``input``, ``handback``,
``export_cookies``, ``stop``.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any, Protocol

import httpx
from loguru import logger

from api import constants

#: The sandbox relays lines with this prefix (sandbox/server.py SENTINEL), so
#: the box uses it too, and the local driver reads the same lines.
SENTINEL = "@@decibyl-tool@@"
#: The box script, beside the sandbox service that ships it.
BOX_SCRIPT = Path(__file__).resolve().parents[3] / "sandbox" / "browser" / "box.py"


class BrowserUnavailable(RuntimeError):
    """No browser can be had here; the person is told why, nothing half-runs."""


class Driver(Protocol):
    name: str

    async def start(self, spec: dict[str, Any]) -> str: ...

    async def next(self, handle: str, *, wait: float) -> dict[str, Any] | None:
        """The box's next message, ``{"type": "exited", ...}`` once it has
        ended, or None if nothing came within ``wait`` seconds."""
        ...

    async def send(self, handle: str, message: dict[str, Any]) -> None: ...

    async def stop(self, handle: str) -> None: ...


def decode(line: str) -> dict[str, Any] | None:
    if not line.startswith(SENTINEL):
        return None
    try:
        value = json.loads(line[len(SENTINEL) :])
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


# --- production: a box per task in the sandbox -----------------------------------


class SandboxDriver:
    """The sandbox service over HTTP. It starts the box and relays its lines;
    it holds no credentials and never calls us back."""

    name = "sandbox"

    def __init__(self, url: str, secret: str, *, timeout: float = 35.0):
        self._url = url.rstrip("/")
        self._headers = {"x-sandbox-secret": secret}
        self._timeout = timeout

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=self._timeout, headers=self._headers)

    async def start(self, spec: dict[str, Any]) -> str:
        limits = spec.get("limits") or {}
        try:
            async with self._client() as client:
                response = await client.post(
                    f"{self._url}/browsers",
                    json={"timeout_seconds": int(limits.get("minutes", 10)) * 60 + 120},
                )
        except httpx.HTTPError as exc:
            raise BrowserUnavailable(
                "The private browser could not be started just now."
            ) from exc
        if response.status_code == 429:
            raise BrowserUnavailable(
                "Every private browser is busy just now; try again in a minute."
            )
        if response.status_code == 503:
            raise BrowserUnavailable(
                str(
                    (response.json() or {}).get("detail") or "The browser is not ready."
                )
            )
        if response.status_code >= 400:
            logger.error(
                "Sandbox refused a browser: {} {}",
                response.status_code,
                response.text[:300],
            )
            raise BrowserUnavailable("The private browser could not be started.")
        handle = str(response.json()["id"])
        await self.send(handle, {"cmd": "start", "spec": spec})
        return handle

    async def next(self, handle: str, *, wait: float) -> dict[str, Any] | None:
        try:
            async with self._client() as client:
                response = await client.get(
                    f"{self._url}/jobs/{handle}/next", params={"wait": wait}
                )
        except httpx.HTTPError as exc:
            logger.warning("Lost the browser box {} for a moment: {}", handle, exc)
            return None
        if response.status_code == 404:
            return {"type": "exited", "exit_code": None, "error": "The box is gone."}
        body = response.json() or {}
        if "request" in body:
            return decode(SENTINEL + str(body["request"]))
        if "done" in body:
            done = body["done"] or {}
            return {
                "type": "exited",
                "exit_code": done.get("exit_code"),
                "error": str(done.get("error") or "")[-2000:],
                "timed_out": bool(done.get("timed_out")),
            }
        return None

    async def send(self, handle: str, message: dict[str, Any]) -> None:
        async with self._client() as client:
            response = await client.post(
                f"{self._url}/jobs/{handle}/reply",
                content=(json.dumps(message) + "\n").encode("utf-8"),
            )
        if response.status_code >= 400:
            raise BrowserUnavailable("The browser has closed.")

    async def stop(self, handle: str) -> None:
        try:
            async with self._client() as client:
                await client.delete(f"{self._url}/jobs/{handle}")
        except httpx.HTTPError as exc:
            logger.warning("Could not stop browser box {}: {}", handle, exc)


# --- development: the same box as a subprocess ----------------------------------


class LocalDriver:
    """The box script in a child process, with the interpreter that has
    browser-use (``BROWSER_BOX_PYTHON``) and a local Chromium."""

    name = "local"

    def __init__(self) -> None:
        if constants.DEPLOYMENT_MODE not in ("oss", "test", "dev") and not os.getenv(
            "BROWSER_ALLOW_LOCAL"
        ):
            raise BrowserUnavailable(
                "Running a browser on the api host is refused outside development."
            )
        python = constants.BROWSER_BOX_PYTHON
        if not python or not Path(python).exists():
            raise BrowserUnavailable(
                "The local browser needs BROWSER_BOX_PYTHON: an interpreter with "
                "browser-use installed (sandbox/browser/README.md)."
            )
        self._python = python
        self._procs: dict[str, asyncio.subprocess.Process] = {}
        self._stderr: dict[str, list[str]] = {}
        self._n = 0

    async def start(self, spec: dict[str, Any]) -> str:
        env = {
            "PATH": os.environ.get("PATH", ""),
            "HOME": os.environ.get("HOME", "/tmp"),
            "PYTHONUNBUFFERED": "1",
            "PYTHONIOENCODING": "utf-8",
            "BROWSER_USE_SETUP_LOGGING": "false",
            "ANONYMIZED_TELEMETRY": "false",
        }
        if constants.BROWSER_CHROMIUM_PATH:
            env["CHROME_PATH"] = constants.BROWSER_CHROMIUM_PATH
        for name in ("LD_LIBRARY_PATH",):
            if os.environ.get(name):
                env[name] = os.environ[name]
        process = await asyncio.create_subprocess_exec(
            self._python,
            "-u",
            str(BOX_SCRIPT),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
            limit=32 * 1024 * 1024,
        )
        self._n += 1
        handle = f"local-{os.getpid()}-{self._n}"
        self._procs[handle] = process
        self._stderr[handle] = []
        asyncio.create_task(self._drain(handle, process))
        await self.send(handle, {"cmd": "start", "spec": spec})
        return handle

    async def _drain(self, handle: str, process: asyncio.subprocess.Process) -> None:
        assert process.stderr is not None
        async for raw in process.stderr:
            lines = self._stderr.setdefault(handle, [])
            lines.append(raw.decode("utf-8", "replace"))
            del lines[:-200]

    async def next(self, handle: str, *, wait: float) -> dict[str, Any] | None:
        process = self._procs.get(handle)
        if process is None:
            return {"type": "exited", "exit_code": None, "error": "No such box."}
        assert process.stdout is not None
        deadline = asyncio.get_running_loop().time() + wait
        while True:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                return None
            try:
                raw = await asyncio.wait_for(
                    process.stdout.readline(), timeout=remaining
                )
            except asyncio.TimeoutError:
                return None
            if not raw:
                code = await process.wait()
                return {
                    "type": "exited",
                    "exit_code": code,
                    "error": "".join(self._stderr.get(handle, []))[-2000:],
                }
            message = decode(raw.decode("utf-8", "replace").rstrip("\n"))
            if message is not None:
                return message

    async def send(self, handle: str, message: dict[str, Any]) -> None:
        process = self._procs.get(handle)
        if process is None or process.stdin is None or process.returncode is not None:
            raise BrowserUnavailable("The browser has closed.")
        process.stdin.write((json.dumps(message) + "\n").encode("utf-8"))
        await process.stdin.drain()

    async def stop(self, handle: str) -> None:
        process = self._procs.pop(handle, None)
        if process is None or process.returncode is not None:
            return
        try:
            process.kill()
        except ProcessLookupError:
            pass
        try:
            await asyncio.wait_for(process.wait(), timeout=5)
        except asyncio.TimeoutError:
            pass


# --- which one -----------------------------------------------------------------

#: Set by a test to hand the session loop a driver of its own.
_override: Driver | None = None


def use(driver: Driver | None) -> None:
    global _override
    _override = driver


def configured() -> str:
    """The driver this deployment would use, by name, or "" for none."""
    if _override is not None:
        return _override.name
    name = constants.BROWSER_DRIVER
    if name:
        return name
    return "sandbox" if constants.SANDBOX_URL else ""


def get() -> Driver:
    if _override is not None:
        return _override
    name = configured()
    if name == "sandbox":
        if not constants.SANDBOX_URL:
            raise BrowserUnavailable(
                "The private browser is not set up on this server."
            )
        return SandboxDriver(constants.SANDBOX_URL, constants.SANDBOX_SECRET or "")
    if name == "local":
        return LocalDriver()
    if name == "fake":
        if constants.DEPLOYMENT_MODE not in ("oss", "test", "dev"):
            raise BrowserUnavailable("The test browser is refused outside development.")
        from api.services.browser.fake import FakeDriver

        return FakeDriver()
    raise BrowserUnavailable("The private browser is not set up on this server.")
