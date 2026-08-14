"""Restoring a backup into a scratch database and checking the money is there.

``database.py`` says it plainly and then leaves it undone: *a backup nobody has
restored is a hypothesis.* Everything that module proves is about the write
path — a dump was taken, encrypted, uploaded, and the object is the right size.
None of that proves a single row comes back.

The ways a backup is worthless while looking healthy are all invisible from the
write side. ``pg_dump`` succeeded against a replica that had stopped
replicating. The dump is of the wrong database. The encryption secret was
rotated and the object can no longer be opened. The restore fails on an
extension the scratch host does not have. Every one of those produces a bucket
full of correctly-sized objects and nothing to restore.

So this restores. Into a **scratch database created for the purpose and dropped
afterwards** — never over a live one, which is why the nightly job prints a
command instead of running it.

Then it checks, and what it checks is the point. "pg_restore exited 0" is
another write-path fact. The questions worth answering are about the ledger:

* Is the credit ledger there at all, and does it hold as many rows as live?
* Does the running balance still reconcile — each row's ``balance_after_paise``
  equal to the sum of everything before it? A dump restored with rows missing
  from the middle passes a row count and fails this.
* Do the payments still sum to what live says they summed to as of that moment?
* How stale is it — how far behind live is the newest row?

The result is recorded, because the other half of the gap is that nobody could
tell whether a rehearsal had *ever* happened. A readiness check reading "last
rehearsed 4 days ago" is evidence; a runbook saying somebody should is not.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import tempfile
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from loguru import logger
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from api.constants import DATABASE_URL
from api.db.models import BackupRehearsalModel
from api.services.storage import get_storage

#: How long a rehearsal counts as recent. Monthly rather than nightly on
#: purpose: restoring is expensive and the failure it catches is slow-moving —
#: a rotated secret, a dropped extension, a replica that stopped. Running it
#: nightly would make it a job people switch off.
REHEARSE_EVERY_DAYS = 30

#: A restore that has not finished in this long has failed in a way worth
#: seeing, rather than being worth waiting out.
RESTORE_TIMEOUT_SECONDS = 30 * 60

#: Name of the database restored into. Dropped at the end of every run,
#: including a failed one — a scratch copy of production left lying around is
#: its own incident.
SCRATCH_DB = "decibyl_restore_rehearsal"


class RehearsalError(RuntimeError):
    """The rehearsal could not be completed."""


@dataclass
class Rehearsal:
    """What a rehearsal found, in terms somebody can act on."""

    key: str
    ok: bool
    checks: dict = field(default_factory=dict)
    error: str | None = None
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    finished_at: datetime | None = None

    def as_dict(self) -> dict:
        return {
            "key": self.key,
            "ok": self.ok,
            "checks": self.checks,
            "error": self.error,
            "started_at": self.started_at.isoformat(),
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
        }


def _libpq_url(database: str | None = None) -> str:
    """``DATABASE_URL`` as libpq understands it, optionally pointed elsewhere.

    SQLAlchemy's URL carries a driver in the scheme — ``postgresql+asyncpg://``
    — which pg_restore does not accept.
    """
    url = DATABASE_URL
    if "+" in url.split("://", 1)[0]:
        scheme, rest = url.split("://", 1)
        url = f"{scheme.split('+', 1)[0]}://{rest}"
    if database is None:
        return url
    base, _, _ = url.rpartition("/")
    return f"{base}/{database}"


def _require(binary: str) -> str:
    found = shutil.which(binary)
    if found is None:
        raise RehearsalError(
            f"{binary} is not installed in this image, so a restore cannot be "
            "rehearsed. It must be installed in the *runner* stage of "
            "api/Dockerfile — a package added to an earlier build stage is "
            "discarded with it."
        )
    return found


async def _psql(sql: str, *, database: str = "postgres") -> None:
    process = await asyncio.create_subprocess_exec(
        _require("psql"),
        "--dbname",
        _libpq_url(database),
        "--no-psqlrc",
        "--quiet",
        "--command",
        sql,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await process.communicate()
    if process.returncode != 0:
        raise RehearsalError(
            f"psql failed ({process.returncode}): {stderr.decode(errors='replace')[:400]}"
        )


async def _pg_restore(dump_path: str) -> None:
    """Restore into the scratch database.

    A non-zero exit is *not* automatically fatal. ``pg_restore`` returns 1 for
    warnings that are routine on a fresh database — an extension already
    present, a role that does not exist here — and treating those as failure
    would make the rehearsal cry wolf until somebody stopped running it. The
    checks that follow are what decide whether the restore worked, which is the
    right authority: they read the data rather than the exit code.
    """
    process = await asyncio.create_subprocess_exec(
        _require("pg_restore"),
        "--no-owner",
        "--no-acl",
        "--dbname",
        _libpq_url(SCRATCH_DB),
        dump_path,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        _, stderr = await asyncio.wait_for(
            process.communicate(), timeout=RESTORE_TIMEOUT_SECONDS
        )
    except TimeoutError:
        process.kill()
        raise RehearsalError(
            f"pg_restore exceeded {RESTORE_TIMEOUT_SECONDS}s and was killed"
        ) from None

    if process.returncode != 0:
        logger.warning(
            "pg_restore reported {} warning(s); the checks decide whether the "
            "restore is usable: {}",
            process.returncode,
            stderr.decode(errors="replace")[:400],
        )


# ---------------------------------------------------------------------------
# What is actually checked
# ---------------------------------------------------------------------------

#: The fingerprint queries, run identically against the restored copy and
#: against live. Money first: the credit ledger is the only record of what
#: every customer has paid and cannot be reconstructed from anywhere else.
_COUNTS = {
    "credit_ledger_rows": "SELECT count(*) FROM credit_ledger",
    "credit_ledger_paise": "SELECT coalesce(sum(delta_paise), 0) FROM credit_ledger",
    "payments_rows": "SELECT count(*) FROM payments",
    "payments_gross_paise": (
        "SELECT coalesce(sum(gross_paise), 0) FROM payments WHERE status = 'paid'"
    ),
    "organizations": "SELECT count(*) FROM organizations",
    "workflow_runs": "SELECT count(*) FROM workflow_runs",
}

#: The one check that is not a count. A dump missing rows from the middle of
#: the ledger passes every count above and fails this — which is the failure
#: mode a row count exists to *look* like it catches.
_LEDGER_RECONCILES = """
SELECT count(*) FROM (
    SELECT balance_after_paise,
           sum(delta_paise) OVER (
               PARTITION BY organization_id ORDER BY id
               ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
           ) AS running
    FROM credit_ledger
) rows WHERE balance_after_paise <> running
"""


async def _scalar(session: AsyncSession, sql: str) -> int:
    return int(await session.scalar(text(sql)) or 0)


async def _fingerprint(session: AsyncSession) -> dict[str, int]:
    return {name: await _scalar(session, sql) for name, sql in _COUNTS.items()}


async def _newest_ledger_row(session: AsyncSession) -> datetime | None:
    return await session.scalar(text("SELECT max(created_at) FROM credit_ledger"))


def _compare(restored: dict[str, int], live: dict[str, int]) -> tuple[bool, dict]:
    """Judge the restored copy against live.

    Restored is expected to be *behind* live, never ahead: the dump was taken
    earlier and these tables only grow. So a figure at or below live is fine
    and a figure above it means the dump is of something else — a different
    database, or a stale environment somebody pointed the job at.

    Zero is failure regardless. A restore that produced an empty ledger is the
    exact outcome this whole module exists to catch, and "0 ≤ live" would call
    it a pass.
    """
    findings = {}
    ok = True
    for name, live_value in live.items():
        restored_value = restored.get(name, 0)
        note = "ok"
        if name.endswith("_paise"):
            # Sums can legitimately fall as well as rise — a refund is a
            # negative row — so only an empty ledger is judged here.
            if live_value and restored_value == 0:
                note, ok = "empty in the restored copy", False
        elif restored_value == 0 and live_value > 0:
            note, ok = "empty in the restored copy", False
        elif restored_value > live_value:
            note, ok = "more than live — this dump is of a different database", False
        findings[name] = {
            "restored": restored_value,
            "live": live_value,
            "note": note,
        }
    return ok, findings


async def rehearse(
    session: AsyncSession,
    *,
    key: str | None = None,
    now: datetime | None = None,
) -> Rehearsal:
    """Restore a backup into a scratch database and check the data came back.

    ``key`` defaults to the newest backup, which is the one worth proving.
    The scratch database is dropped afterwards whether or not the checks pass.
    """
    from api.services.backup.database import _cipher, _list_backups

    moment = now or datetime.now(UTC)
    storage = get_storage()

    if key is None:
        listed = await _list_backups(storage)
        if not listed:
            raise RehearsalError("There are no backups to rehearse a restore from.")
        key = listed[-1][0]

    rehearsal = Rehearsal(key=key, ok=False, started_at=moment)

    try:
        live = await _fingerprint(session)
        live_newest = await _newest_ledger_row(session)

        with tempfile.TemporaryDirectory(prefix="decibyl-rehearsal-") as workdir:
            encrypted_path = os.path.join(workdir, "restore.dump.enc")
            dump_path = os.path.join(workdir, "restore.dump")

            if not await storage.adownload_file(key, encrypted_path):
                raise RehearsalError(f"Backup {key} could not be read from storage.")

            # The failure a bucket full of correctly-sized objects hides best:
            # the secret was rotated and nothing can be opened any more.
            try:
                plaintext = await asyncio.to_thread(
                    lambda: _cipher().decrypt(_read_file(encrypted_path))
                )
            except RehearsalError:
                raise
            except Exception as exc:
                raise RehearsalError(
                    f"{key} cannot be decrypted. Either "
                    "PLATFORM_CREDENTIAL_SECRET has changed since it was "
                    "written — set the old value as "
                    "PLATFORM_CREDENTIAL_SECRET_PREVIOUS — or the object is "
                    "corrupt. Either way this backup cannot currently be "
                    "restored."
                ) from exc
            await asyncio.to_thread(_write_file, dump_path, plaintext)
            os.remove(encrypted_path)

            await _psql(f'DROP DATABASE IF EXISTS "{SCRATCH_DB}" WITH (FORCE)')
            await _psql(f'CREATE DATABASE "{SCRATCH_DB}"')
            try:
                await _pg_restore(dump_path)
                restored, restored_newest = await _read_back()
            finally:
                # Dropped even when the checks fail. A scratch copy of
                # production left on the box is its own incident.
                await _psql(f'DROP DATABASE IF EXISTS "{SCRATCH_DB}" WITH (FORCE)')

        ok, findings = _compare(restored["counts"], live)

        # The check a row count only looks like it makes. A dump missing rows
        # from the middle of the ledger passes every count and fails here.
        broken = restored["ledger_mismatches"]
        if broken:
            ok = False
        findings["ledger_reconciles"] = {
            "mismatched_rows": broken,
            "note": "ok" if not broken else "the running balance does not reconcile",
        }

        if restored_newest is not None and live_newest is not None:
            behind = (live_newest - restored_newest).total_seconds() / 3600
            findings["hours_behind_live"] = {"value": round(behind, 1), "note": "ok"}

        rehearsal.ok = ok
        rehearsal.checks = findings

    except RehearsalError as exc:
        rehearsal.ok = False
        rehearsal.error = str(exc)
        logger.error("Restore rehearsal of {} failed: {}", key, exc)
    except Exception as exc:  # noqa: BLE001 - recorded rather than lost
        rehearsal.ok = False
        rehearsal.error = f"{type(exc).__name__}: {exc}"
        logger.exception("Restore rehearsal of {} failed", key)

    rehearsal.finished_at = datetime.now(UTC)

    # Recorded either way, and recorded *because* it may have failed: a
    # rehearsal nobody can see the result of is the runbook line it replaces.
    session.add(
        BackupRehearsalModel(
            backup_key=rehearsal.key,
            ok=rehearsal.ok,
            checks=rehearsal.checks,
            error=rehearsal.error,
            started_at=rehearsal.started_at,
            finished_at=rehearsal.finished_at,
        )
    )
    await session.flush()

    logger.info(
        "Restore rehearsal of {} {}",
        rehearsal.key,
        "passed" if rehearsal.ok else f"FAILED: {rehearsal.error or 'checks'}",
    )
    return rehearsal


def _write_file(path: str, payload: bytes) -> None:
    with open(path, "wb") as handle:
        handle.write(payload)


def _read_file(path: str) -> bytes:
    with open(path, "rb") as handle:
        return handle.read()


async def _read_back() -> tuple[dict, datetime | None]:
    """Run the fingerprint against the restored copy.

    Its own engine, disposed afterwards, because the scratch database is
    dropped the moment this returns and a pooled connection to a dropped
    database is how the drop fails.
    """
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    url = DATABASE_URL
    base, _, _ = url.rpartition("/")
    engine = create_async_engine(f"{base}/{SCRATCH_DB}", poolclass=None)
    try:
        maker = async_sessionmaker(engine, expire_on_commit=False)
        async with maker() as scratch:
            counts = await _fingerprint(scratch)
            mismatches = await _scalar(scratch, _LEDGER_RECONCILES)
            newest = await _newest_ledger_row(scratch)
        return {"counts": counts, "ledger_mismatches": mismatches}, newest
    finally:
        await engine.dispose()


async def last_rehearsal(session: AsyncSession, *, now: datetime | None = None) -> dict:
    """The most recent rehearsal and how long ago it was.

    Read by the readiness check. Reports the newest attempt rather than the
    newest *success* on purpose — a failed rehearsal four days ago is the most
    important fact about these backups, and hiding it behind "last known good"
    is how a broken restore path stays quiet.
    """
    moment = now or datetime.now(UTC)
    row = (
        await session.scalars(
            select(BackupRehearsalModel).order_by(
                BackupRehearsalModel.started_at.desc()
            )
        )
    ).first()

    if row is None:
        return {"rehearsed": False, "ok": None, "age_days": None}

    age_days = (moment - row.started_at.replace(tzinfo=UTC)).days
    return {
        "rehearsed": True,
        "ok": bool(row.ok),
        "age_days": age_days,
        "stale": age_days > REHEARSE_EVERY_DAYS,
        "backup_key": row.backup_key,
        "at": row.started_at.isoformat(),
        "checks": row.checks or {},
        "error": row.error,
    }


def due(state: dict, *, every_days: int = REHEARSE_EVERY_DAYS) -> bool:
    """Whether a rehearsal is owed. Never rehearsed counts as owed."""
    if not state.get("rehearsed"):
        return True
    return (state.get("age_days") or 0) > every_days


def _stale_cutoff(now: datetime) -> datetime:
    return now - timedelta(days=REHEARSE_EVERY_DAYS)
