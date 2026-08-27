"""Utilities for worker process identification."""

import multiprocessing
import os

from loguru import logger


def get_worker_id() -> int:
    """Get the current worker ID from environment or process name.

    Returns:
        Worker ID (0-based index), or 0 if not in a worker process.
    """
    # Check for custom ASGI_WORKER_ID (for future compatibility)
    worker_id = os.getenv("ASGI_WORKER_ID")
    if worker_id:
        return int(worker_id)

    # Debug log the process name to understand worker identification
    process_name = multiprocessing.current_process().name

    # Try to extract worker number from process name
    # Uvicorn with --workers creates processes like "SpawnProcess-1", "SpawnProcess-2", etc.
    # A respawned worker gets the next number rather than the dead one's, so on
    # a deployment configured for two workers a crash can produce
    # SpawnProcess-3 and therefore a worker id of 2. That is not a correctness
    # problem: the single caller of this uses the id to name a log file, so the
    # visible effect is that the replacement writes to ``app-worker-2.log``
    # instead of reopening ``app-worker-1.log``. Recycling ids would mean the
    # replacement appends to the dead worker's file, which is worse for reading
    # back what happened around a crash.
    #
    # It is written down because the id looks like an index into a fixed set
    # and is not one. Anything that starts partitioning work by worker id —
    # sharding a queue, electing a leader — must not assume the ids are dense,
    # bounded by the worker count, or unique over time.
    if "SpawnProcess" in process_name:
        try:
            # Extract the number after "SpawnProcess-"
            worker_num = int(process_name.split("-")[-1])
            # Convert to 0-based index
            return worker_num - 1
        except (ValueError, IndexError):
            logger.warning(
                f"Could not extract worker ID from process name: {process_name}"
            )

    # Gunicorn creates workers with names like "Worker-1", "Worker-2", etc.
    if "Worker" in process_name:
        try:
            # Extract the number after "Worker-"
            worker_num = int(process_name.split("-")[-1])
            # Convert to 0-based index
            return worker_num - 1
        except (ValueError, IndexError):
            logger.warning(
                f"Could not extract worker ID from process name: {process_name}"
            )

    # Not in a worker process (main process or single-process mode)
    return 0


def is_worker_process() -> bool:
    """Check if we're running in a worker process (not the main process).

    Returns:
        True if in a worker process, False if in main process or single-process mode.
    """
    process_name = multiprocessing.current_process().name
    return "SpawnProcess" in process_name or "Worker" in process_name
