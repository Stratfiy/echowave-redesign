"""Stay under the API's rate limit, and never mistake a 429 for an answer.

The API allows ``RATE_LIMIT_DEFAULT_PER_MINUTE`` (600) requests a minute per
client address, and both test accounts come from one address. The sweeps
make several hundred requests, so unpaced they are refused part-way -- and a
refused request is a 429, which is below 500 and contains no marker: the
route sweep would count it as healthy and the privacy sweep as private.
Both would be passes for the wrong reason.

So every request waits its turn (``E2E_REQUESTS_PER_MINUTE``, 400 by
default), and a 429 is retried after the server's ``Retry-After`` rather
than returned.
"""

from __future__ import annotations

import os
import threading
import time

PER_MINUTE = float(os.environ.get("E2E_REQUESTS_PER_MINUTE", "400"))
RETRIES_ON_429 = 4
_lock = threading.Lock()
_next_at = 0.0


def wait_turn() -> None:
    global _next_at
    with _lock:
        now = time.monotonic()
        start = max(now, _next_at)
        _next_at = start + 60.0 / PER_MINUTE
    delay = start - time.monotonic()
    if delay > 0:
        time.sleep(delay)


def retry_after(headers) -> float:
    try:
        return min(max(float(headers.get("retry-after", "5")), 1.0), 65.0)
    except ValueError:
        return 5.0
