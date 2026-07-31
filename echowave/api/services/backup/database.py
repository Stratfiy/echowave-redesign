"""Nightly encrypted database backup.

The credit ledger is the only record of what every customer has paid, and it
cannot be reconstructed from anywhere else: Razorpay knows what was charged, but
not what was consumed, reserved or adjusted, and every tax invoice already
issued is numbered against rows that live only here. Losing the volume loses the
money history and makes issued invoices unreproducible, which is a GST problem
sitting on top of a customer-trust one.

Until this module existed, nothing took a backup. ``DEPLOY.md`` asked the
operator to take one, which is an instruction to a human rather than an
implementation, and there was no way to tell from the outside whether anyone
ever had.

Four properties, each of which is the difference between a backup and a folder
of files:

* **Encrypted before it leaves the process.** A database dump is every
  recording URL, every phone number and every invoice in one file. It is
  encrypted with the same Fernet secret that protects provider credentials, and
  the plaintext is never written to the object store.
* **Verified after upload.** The object is read back and its size compared
  before the local file is removed. An upload that silently truncated is the
  failure that looks most like success.
* **Pruned on a schedule.** Backups are personal data too — a 90-day retention
  window means nothing if a dump from last year is still sitting in a bucket.
* **Reported as evidence.** ``last_successful()`` is what the readiness check
  reads, so "there is a backup" is answered by the age of the newest one rather
  than by the presence of this file.

**Restoring is not tested here and cannot be.** A backup nobody has restored is
a hypothesis. ``restore_command()`` prints the exact command so the rehearsal is
cheap; do it once before relying on any of this.
"""

from __future__ import annotations

import asyncio
import os
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from loguru import logger

from api.constants import (
    BACKUP_RETENTION_DAYS,
    DATABASE_URL,
    PLATFORM_CREDENTIAL_SECRET,
)
from api.services.storage import get_storage

#: Where dumps live in the object store. A prefix rather than a bucket so it
#: inherits the same access controls as everything else — in particular, no
#: anonymous access.
BACKUP_PREFIX = "backups/postgres"

#: pg_dump's custom format: compressed, and restorable selectively with
#: pg_restore. Plain SQL would be larger and could only be replayed whole.
DUMP_FORMAT = "custom"

#: A dump of a database this size should never take longer than this. Without a
#: timeout a wedged pg_dump holds the worker slot until the process is killed,
#: and the nightly job silently stops running.
DUMP_TIMEOUT_SECONDS = 30 * 60


@dataclass(frozen=True)
class BackupResult:
    key: str
    size_bytes: int
    started_at: datetime
    finished_at: datetime

    @property
    def duration_seconds(self) -> float:
        return (self.finished_at - self.started_at).total_seconds()


def _cipher():
    """The same secret that protects provider credentials.

    Deliberately not a separate key. A second secret is a second thing to lose,
    and losing it means the backups are unreadable — which is indistinguishable
    from having no backups at the moment you need them.
    """
    from cryptography.fernet import Fernet

    if not PLATFORM_CREDENTIAL_SECRET:
        raise RuntimeError(
            "PLATFORM_CREDENTIAL_SECRET is not set. Refusing to write an "
            "unencrypted database dump to object storage."
        )
    return Fernet(PLATFORM_CREDENTIAL_SECRET.encode())


def _dump_key(at: datetime) -> str:
    return f"{BACKUP_PREFIX}/{at:%Y/%m/%d}/decibyl-{at:%Y%m%dT%H%M%SZ}.dump.enc"


def _require_pg_dump() -> str:
    """Locate pg_dump, or say precisely what is wrong.

    Worth its own check because of how this fails otherwise. ``pg_dump`` is
    installed by the Dockerfile, and it was once added to a *build* stage that
    the runtime image discards — so the image built, the app ran, migrations
    applied, every health check passed, and the only symptom was a backup job
    that threw ``FileNotFoundError: pg_dump`` into a log nobody was reading.

    A bare exec error names the missing file but not the reason. This does.
    """
    import shutil

    found = shutil.which("pg_dump")
    if found is None:
        raise RuntimeError(
            "pg_dump is not installed in this image, so no backup can be taken. "
            "It must be installed in the *runner* stage of api/Dockerfile — a "
            "package added to an earlier build stage is discarded with it."
        )
    return found


async def _run_pg_dump(destination: str) -> None:
    """Dump the database to a local file.

    ``pg_dump`` is given the URL directly rather than a password in the
    environment, so no credential is ever written to a file or an argument this
    process does not already hold.
    """
    process = await asyncio.create_subprocess_exec(
        _require_pg_dump(),
        "--format",
        DUMP_FORMAT,
        "--no-owner",
        "--no-acl",
        "--file",
        destination,
        _libpq_url(),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        _, stderr = await asyncio.wait_for(
            process.communicate(), timeout=DUMP_TIMEOUT_SECONDS
        )
    except TimeoutError:
        process.kill()
        raise RuntimeError(
            f"pg_dump exceeded {DUMP_TIMEOUT_SECONDS}s and was killed"
        ) from None

    if process.returncode != 0:
        raise RuntimeError(
            f"pg_dump failed ({process.returncode}): {stderr.decode(errors='replace')[:500]}"
        )


def _libpq_url() -> str:
    """``DATABASE_URL`` as libpq understands it.

    SQLAlchemy's URL carries a driver in the scheme — ``postgresql+asyncpg://``
    — which pg_dump does not accept and fails on with a message about an
    unrecognised host. Stripping it is the whole of the translation.
    """
    url = DATABASE_URL
    if "+" in url.split("://", 1)[0]:
        scheme, rest = url.split("://", 1)
        url = f"{scheme.split('+', 1)[0]}://{rest}"
    return url


async def run_backup(*, now: datetime | None = None) -> BackupResult:
    """Dump, encrypt, upload, verify. Raises if any step fails."""
    now = now or datetime.now(UTC)
    started = now
    storage = get_storage()
    key = _dump_key(now)

    with tempfile.TemporaryDirectory(prefix="decibyl-backup-") as workdir:
        dump_path = os.path.join(workdir, "db.dump")

        await _run_pg_dump(dump_path)
        plaintext = await asyncio.to_thread(_read_file, dump_path)
        if not plaintext:
            raise RuntimeError("pg_dump produced an empty file")

        encrypted = await asyncio.to_thread(_cipher().encrypt, plaintext)

        if not await storage.acreate_file_from_bytes(key, encrypted):
            raise RuntimeError(f"Failed to upload backup to {key}")

        # Read back before trusting it. A truncated upload reports success at
        # every layer above this one.
        metadata = await storage.aget_file_metadata(key)
        uploaded = int((metadata or {}).get("size") or 0)
        if uploaded != len(encrypted):
            raise RuntimeError(
                f"Backup upload verification failed for {key}: "
                f"expected {len(encrypted)} bytes, object holds {uploaded}"
            )

    finished = datetime.now(UTC)
    logger.info(
        "Database backup written to {} ({} bytes, {:.1f}s)",
        key,
        len(encrypted),
        (finished - started).total_seconds(),
    )
    return BackupResult(
        key=key,
        size_bytes=len(encrypted),
        started_at=started,
        finished_at=finished,
    )


def _read_file(path: str) -> bytes:
    with open(path, "rb") as handle:
        return handle.read()


async def prune(*, now: datetime | None = None) -> int:
    """Delete backups past the retention window. Returns how many went.

    A dump is a copy of every phone number and recording reference in the
    system, so it ages under the same obligation as the data inside it. A
    retention policy that the backups quietly exempt themselves from is not a
    retention policy.
    """
    now = now or datetime.now(UTC)
    cutoff = now - timedelta(days=BACKUP_RETENTION_DAYS)
    storage = get_storage()

    listed = await _list_backups(storage)
    removed = 0
    for key, taken_at in listed:
        if taken_at >= cutoff:
            continue
        if await storage.adelete_file(key):
            removed += 1
        else:
            # Reported, not raised: one undeletable object must not stop the
            # rest of the sweep, and it will be retried tomorrow.
            logger.warning("Could not delete expired backup {}", key)

    if removed:
        logger.info(
            "Pruned {} backups older than {} days", removed, BACKUP_RETENTION_DAYS
        )
    return removed


async def _list_backups(storage) -> list[tuple[str, datetime]]:
    """Every backup object with the time encoded in its name.

    The timestamp comes from the key rather than object metadata because it has
    to survive a copy between buckets — which is exactly what someone does when
    moving backups somewhere safer.
    """
    keys = await storage.alist_files(BACKUP_PREFIX)
    found: list[tuple[str, datetime]] = []
    for key in keys:
        taken_at = _timestamp_from_key(key)
        if taken_at is not None:
            found.append((key, taken_at))
    return sorted(found, key=lambda item: item[1])


def _timestamp_from_key(key: str) -> datetime | None:
    stem = key.rsplit("/", 1)[-1]
    if not stem.startswith("decibyl-") or ".dump" not in stem:
        return None
    raw = stem[len("decibyl-") :].split(".", 1)[0]
    try:
        return datetime.strptime(raw, "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
    except ValueError:
        return None


async def last_successful(*, now: datetime | None = None) -> dict:
    """The newest backup, and how old it is.

    Read by the privacy readiness check. Presence of this module proves nothing;
    the age of the newest object is the only evidence that the job is running.
    """
    now = now or datetime.now(UTC)
    try:
        listed = await _list_backups(get_storage())
    except Exception as exc:  # noqa: BLE001 - a readiness probe must not raise
        logger.warning("Could not list backups: {}", exc)
        return {"available": False, "error": str(exc), "count": 0}

    if not listed:
        return {"available": False, "count": 0, "age_hours": None, "key": None}

    key, taken_at = listed[-1]
    return {
        "available": True,
        "count": len(listed),
        "key": key,
        "taken_at": taken_at.isoformat(),
        "age_hours": round((now - taken_at).total_seconds() / 3600, 1),
    }


def restore_command(key: str) -> str:
    """The exact command to restore one backup.

    Printed rather than executed. Restoring over a live database is not
    something any scheduled job should be able to do, and the rehearsal is the
    part that turns a hypothesis into a backup.
    """
    return (
        f"# 1. Fetch and decrypt (needs PLATFORM_CREDENTIAL_SECRET):\n"
        f'python -c "import os,sys;from cryptography.fernet import Fernet;'
        f"sys.stdout.buffer.write(Fernet(os.environ['PLATFORM_CREDENTIAL_SECRET']"
        f".encode()).decrypt(open(sys.argv[1],'rb').read()))\" {key.rsplit('/', 1)[-1]} > db.dump\n"
        f"# 2. Restore into an EMPTY database — never over a live one:\n"
        f"pg_restore --no-owner --no-acl --dbname postgresql://... db.dump"
    )
