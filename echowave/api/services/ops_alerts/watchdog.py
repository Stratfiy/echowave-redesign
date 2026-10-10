"""The API's watch on the background worker.

The five-minute alert job runs *inside* the ARQ worker, so it is the one
thing that cannot say the worker has stopped. The API process outlives it
(``start_services_docker.sh`` runs both in one container, and uvicorn keeps
answering when the worker dies), so the API runs the ``jobs`` detector once
a minute. Each uvicorn worker runs this loop; the evaluation lock in
``runner`` makes that one evaluation a minute, not one per process.

Does nothing while ``ops_alerts`` is off, and the loop never raises.
"""

from __future__ import annotations

import asyncio

from loguru import logger

from api.services.ops_alerts.thresholds import WATCHDOG_INTERVAL_SECONDS

_task: asyncio.Task | None = None


async def _loop() -> None:
    from api.services.ops_alerts import runner

    while True:
        try:
            await runner.evaluate(("jobs",))
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - the watch must outlive a bad minute
            logger.warning("ops_alerts watchdog: {}", type(exc).__name__)
        await asyncio.sleep(WATCHDOG_INTERVAL_SECONDS)


def start() -> None:
    global _task
    if _task is None or _task.done():
        _task = asyncio.create_task(_loop(), name="ops_alerts_watchdog")


async def stop() -> None:
    global _task
    if _task is None:
        return
    _task.cancel()
    try:
        await _task
    except (asyncio.CancelledError, Exception):  # noqa: BLE001
        pass
    _task = None
