"""What every dialer adapter returns, and how it fails.

An adapter's whole job is two calls: list a window's calls, and fetch one
recording. Everything else -- which calls are worth importing, where the
audio goes, transcribing it -- is the importer's, so adding a third dialer
is writing these two functions and nothing more.
"""

from __future__ import annotations

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


def last_four(number: str | None) -> str | None:
    digits = "".join(ch for ch in number or "" if ch.isdigit())
    return digits[-4:] or None


def as_int(value) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0
