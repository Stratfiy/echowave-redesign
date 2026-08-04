"""The background worker saying it is alive.

Deliberately the most trivial job in `tasks/`: it must not be able to fail for
any reason other than the worker being dead, or it stops meaning what the
health check reads it to mean. No database, no object store, one Redis write.

See `services/worker_health.py` for why the absence of work cannot be used as
the signal instead.
"""

from api.services.worker_health import record_heartbeat


async def record_worker_heartbeat(_ctx) -> None:
    """Record that the ARQ worker reached its scheduled tick."""
    await record_heartbeat()
