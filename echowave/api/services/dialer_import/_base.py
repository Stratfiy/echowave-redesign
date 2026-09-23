"""What every dialer adapter returns, and how it fails.

An adapter's whole job is two calls: list a window's calls, and fetch one
recording. Everything else -- which calls are worth importing, where the
audio goes, transcribing it -- is the importer's, so adding a third dialer
is writing these two functions and nothing more.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime

import httpx

#: A generous ceiling: a day's listing is a handful of pages, and a recording
#: an MB a minute. A dialer that takes longer than this is down, and the run
#: says so rather than holding a worker.
TIMEOUT = httpx.Timeout(60.0)

#: Largest recording copied across. An hour of MP3 is ~60 MB; anything larger
#: is not a telecalling call and would cost more to transcribe than it tells.
MAX_RECORDING_BYTES = 80 * 1024 * 1024


class DialerError(Exception):
    """A dialer call failed. ``message`` is written for the business."""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class DialerAuthError(DialerError):
    """The dialer refused the credentials: wrong, revoked or expired."""


@dataclass(frozen=True)
class DialerCall:
    external_id: str
    started_at: datetime | None
    duration_seconds: int
    direction: str | None
    status: str
    recording_url: str | None
    agent_name: str | None
    agent_number: str | None
    customer_number: str | None

    @property
    def answered(self) -> bool:
        return self.status.lower() in {"completed", "answered"}


@asynccontextmanager
async def client_for(client: httpx.AsyncClient | None):
    """The caller's client, or one of our own that is closed afterwards."""
    if client is not None:
        yield client
        return
    async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=True) as own:
        yield own


def raise_for(response: httpx.Response, *, dialer: str, refused: str) -> None:
    """``refused`` tells the business how to fix credentials the dialer turned
    down; any other failure is retried on the next night's run."""
    if response.status_code in (401, 403):
        raise DialerAuthError(f"{dialer} refused {refused}")
    if response.status_code >= 400:
        raise DialerError(
            f"{dialer} answered {response.status_code}. It will be tried again tonight."
        )


async def download(
    call: DialerCall, *, client: httpx.AsyncClient | None, check, **request
) -> bytes:
    """One call's recording, with the dialer's own auth in ``request`` and
    its error wording in ``check``."""
    if not call.recording_url:
        raise DialerError("This call has no recording.")
    async with client_for(client) as http:
        response = await http.get(call.recording_url, **request)
    check(response)
    if len(response.content) > MAX_RECORDING_BYTES:
        raise DialerError("The recording is too long to import.")
    return response.content


def last_four(number: str | None) -> str | None:
    digits = "".join(ch for ch in number or "" if ch.isdigit())
    return digits[-4:] or None


def as_int(value) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0
