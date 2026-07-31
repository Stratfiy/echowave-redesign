"""Scheduled database backup.

Runs nightly, well away from the retention sweep so the two are not competing
for object-store IO, and after it so the dump does not carry a day of data that
is about to be deleted anyway.

**Failures are loud.** Every other scheduled job here swallows its exception so
one bad night cannot take down the worker; this one re-raises after logging, so
the failure reaches Sentry and the ARQ job is recorded as failed. A backup that
fails quietly is worse than no backup at all, because the readiness check would
keep reporting the last good one until it aged out and nobody would look.
"""

from loguru import logger

from api.constants import BACKUP_ENABLED
from api.services.backup import database


async def run_database_backup(_ctx) -> None:
    """Take a backup, then prune anything past the retention window."""
    if not BACKUP_ENABLED:
        logger.warning(
            "BACKUP_ENABLED is false — skipping the nightly database backup. "
            "The credit ledger has no other copy."
        )
        return

    try:
        result = await database.run_backup()
    except Exception as exc:
        logger.error("Database backup FAILED: {}", exc)
        raise

    logger.info(
        "Database backup complete: {} ({:.1f} MB in {:.0f}s)",
        result.key,
        result.size_bytes / 1_048_576,
        result.duration_seconds,
    )

    # Pruning is separate from the dump on purpose: a prune that fails must not
    # make a successful backup look like a failed one.
    try:
        await database.prune()
    except Exception as exc:  # noqa: BLE001 - the backup already succeeded
        logger.error("Backup prune failed (the backup itself succeeded): {}", exc)
