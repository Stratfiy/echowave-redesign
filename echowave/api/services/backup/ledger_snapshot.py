"""An hourly copy of the money, between the nightly copies of everything.

**This is not point-in-time recovery and must never be described as if it
were.** PITR replays write-ahead log onto a base backup and loses minutes. This
loses up to an hour, and only of the tables listed below. It exists because the
gap it narrows is the one that cannot be closed any other way:

A database failure at 17:00 restores from last night's dump. Everything lost is
in principle reconstructible — calls can be re-made, agents re-built, documents
re-uploaded — *except the ledger*. It is the only record of what each customer
paid and what they have consumed, and tax invoices have already been issued
numbered against those rows. There is no upstream to re-derive it from:
Razorpay knows what was charged and nothing about what was spent, reserved or
adjusted.

So this dumps only the money tables, hourly. They are small — a busy month is
tens of thousands of rows against a database whose bulk is call transcripts and
recording metadata — which is what makes an hourly cadence affordable when an
hourly full dump would not be.

Why this rather than WAL archiving on the bundled Postgres: ``archive_command``
that fails stops WAL recycling and eventually wedges the primary, and
``pg_receivewal`` without a slot can silently gap while a slot reintroduces the
same disk risk. Neither is a decision worth making on a customer's behalf when
managed Postgres with PITR is an afternoon of configuration and also answers
who patches the database. The readiness check therefore keeps reporting the
real recovery point regardless of this job — see
``services/privacy/readiness._durability_checks``.
"""

from __future__ import annotations

import asyncio
import os
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from loguru import logger

from api.constants import LEDGER_SNAPSHOT_RETENTION_DAYS
from api.services.backup.database import (
    _cipher,
    _libpq_url,
    _read_file,
    _require_pg_dump,
)
from api.services.storage import get_storage

#: Kept apart from the nightly dumps so a prune sweep over one cannot walk into
#: the other, and so an operator listing "backups" is not handed 700 partials.
SNAPSHOT_PREFIX = "backups/ledger"

#: What cannot be reconstructed from anywhere else. Deliberately a short,
#: explicit list rather than a pattern: a table added here is a decision, and a
#: pattern would quietly start dumping something large.
MONEY_TABLES = (
    "credit_ledger",
    "payments",
    "tax_documents",
    "provider_rates",
    "organizations",
)

#: A snapshot of five small tables that takes longer than this is not slow, it
#: is stuck.
SNAPSHOT_TIMEOUT_SECONDS = 5 * 60


@dataclass(frozen=True)
class SnapshotResult:
    key: str
    size_bytes: int
    taken_at: datetime


def _snapshot_key(at: datetime) -> str:
    return f"{SNAPSHOT_PREFIX}/{at:%Y/%m/%d}/ledger-{at:%Y%m%dT%H%M%SZ}.dump.enc"


async def _run_pg_dump_of_money_tables(destination: str) -> None:
    """Dump only the money tables, data and schema, in custom format."""
    binary = _require_pg_dump()

    args = [binary, "--format=custom", "--no-owner", "--no-acl"]
    for table in MONEY_TABLES:
        args.extend(["--table", f"public.{table}"])
    args.extend(["--file", destination, _libpq_url()])

    process = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        _, stderr = await asyncio.wait_for(
            process.communicate(), timeout=SNAPSHOT_TIMEOUT_SECONDS
        )
    except asyncio.TimeoutError:
        process.kill()
        await process.wait()
        raise RuntimeError(
            f"pg_dump of the money tables exceeded {SNAPSHOT_TIMEOUT_SECONDS}s"
        )

    if process.returncode != 0:
        raise RuntimeError(
            f"pg_dump failed ({process.returncode}): "
            f"{stderr.decode(errors='replace')[:500]}"
        )


async def take_snapshot(*, now: datetime | None = None) -> SnapshotResult:
    """Dump, encrypt, upload, verify — same discipline as the nightly job.

    Encrypted with the same secret for the same reason: a second key is a
    second thing to lose, and losing it makes the copies unreadable, which is
    indistinguishable from not having them.
    """
    now = now or datetime.now(UTC)
    storage = get_storage()
    key = _snapshot_key(now)

    with tempfile.TemporaryDirectory(prefix="decibyl-ledger-") as workdir:
        dump_path = os.path.join(workdir, "ledger.dump")

        await _run_pg_dump_of_money_tables(dump_path)
        plaintext = await asyncio.to_thread(_read_file, dump_path)
        if not plaintext:
            raise RuntimeError("pg_dump produced an empty ledger snapshot")

        encrypted = await asyncio.to_thread(_cipher().encrypt, plaintext)

        if not await storage.acreate_file_from_bytes(key, encrypted):
            raise RuntimeError(f"Failed to upload ledger snapshot to {key}")

        metadata = await storage.aget_file_metadata(key)
        uploaded = int((metadata or {}).get("size") or 0)
        if uploaded != len(encrypted):
            raise RuntimeError(
                f"Ledger snapshot verification failed for {key}: expected "
                f"{len(encrypted)} bytes, object holds {uploaded}"
            )

    logger.info("Ledger snapshot written to {} ({} bytes)", key, len(encrypted))
    return SnapshotResult(key=key, size_bytes=len(encrypted), taken_at=now)


async def prune_snapshots(*, now: datetime | None = None) -> int:
    """Drop snapshots past their (short) window.

    Retained for days rather than the nightly dumps' 30, because their whole
    purpose is to cover the hours since the last full backup. A three-week-old
    partial dump of five tables is not useful for recovery and is still a copy
    of every payment — so it ages out fast, under the same obligation as the
    data inside it.
    """
    now = now or datetime.now(UTC)
    cutoff = now - timedelta(days=LEDGER_SNAPSHOT_RETENTION_DAYS)
    storage = get_storage()

    removed = 0
    for key, taken_at in await _list_snapshots(storage):
        if taken_at >= cutoff:
            continue
        if await storage.adelete_file(key):
            removed += 1
        else:
            logger.warning("Could not delete expired ledger snapshot {}", key)

    if removed:
        logger.info("Pruned {} ledger snapshots", removed)
    return removed


async def _list_snapshots(storage) -> list[tuple[str, datetime]]:
    keys = await storage.alist_files(SNAPSHOT_PREFIX)
    found: list[tuple[str, datetime]] = []
    for key in keys or []:
        taken_at = _timestamp_from_key(key)
        if taken_at is not None:
            found.append((key, taken_at))
    return sorted(found, key=lambda item: item[1])


def _timestamp_from_key(key: str) -> datetime | None:
    stem = key.rsplit("/", 1)[-1]
    if not stem.startswith("ledger-") or ".dump" not in stem:
        return None
    raw = stem[len("ledger-") :].split(".", 1)[0]
    try:
        return datetime.strptime(raw, "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
    except ValueError:
        return None


async def newest(*, now: datetime | None = None) -> dict:
    """The newest snapshot and its age, for the readiness check."""
    now = now or datetime.now(UTC)
    try:
        listed = await _list_snapshots(get_storage())
    except Exception as exc:  # noqa: BLE001 — a readiness probe must not raise
        logger.warning("Could not list ledger snapshots: {}", exc)
        return {"available": False, "error": str(exc), "count": 0}

    if not listed:
        return {"available": False, "count": 0, "age_hours": None}

    key, taken_at = listed[-1]
    return {
        "available": True,
        "count": len(listed),
        "key": key,
        "taken_at": taken_at.isoformat(),
        "age_hours": round((now - taken_at).total_seconds() / 3600, 1),
    }
