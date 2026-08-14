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


async def rehearse_database_restore(_ctx) -> None:
    """Restore the newest backup into a scratch database and check it came back.

    The nightly job proves only the write path: a dump was taken, encrypted,
    uploaded, and the object is the right size. Every way a backup is worthless
    while looking healthy is invisible from there — a dump of a replica that
    had stopped replicating, an object encrypted under a secret since rotated,
    a restore that fails on an extension the host does not have.

    Monthly rather than nightly, because restoring is expensive and the
    failures it catches are slow-moving. A job that ran every night would be
    switched off, and a switched-off rehearsal proves less than none — it looks
    like coverage.

    **Failures are recorded, not raised.** The opposite of the backup job
    above, and for the opposite reason: the backup re-raises because a failed
    write leaves no other trace, while a failed rehearsal writes its own row
    and the readiness check reads it. Re-raising as well would mark the ARQ job
    failed on a night when the *evidence* worked exactly as intended.
    """
    if not BACKUP_ENABLED:
        logger.warning(
            "BACKUP_ENABLED is false — skipping the restore rehearsal. There "
            "is nothing to restore from."
        )
        return

    from api.db import db_client
    from api.services.backup import rehearsal

    async with db_client.async_session() as session:
        try:
            result = await rehearsal.rehearse(session)
        except rehearsal.RehearsalError as exc:
            logger.error("Restore rehearsal could not run: {}", exc)
            return
        await session.commit()

    if result.ok:
        logger.info("Restore rehearsal passed against {}", result.key)
    else:
        logger.error(
            "Restore rehearsal FAILED against {}: {}. These backups cannot "
            "currently be relied on.",
            result.key,
            result.error or "the checks did not pass",
        )
