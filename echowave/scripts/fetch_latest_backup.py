"""Write the newest database backup to stdout, still encrypted.

``scripts/rehearse_restore.sh`` has documented this invocation since it was
written, and the module did not exist — so the rehearsal that turns a backup
into a fact failed at its first step, with "No module named
scripts.fetch_latest_backup", on the box, at the moment somebody had decided to
do the right thing.

    docker compose exec -T api python -m scripts.fetch_latest_backup > backup.enc
    PLATFORM_CREDENTIAL_SECRET=... ./scripts/rehearse_restore.sh backup.enc

**Still encrypted** is deliberate and worth defending. The plaintext dump is
every phone number, recording URL and invoice in one file; decrypting here
would put it on the terminal of whoever ran the command and in whatever shell
history or CI log was watching. ``rehearse_restore.sh`` decrypts into a
temporary directory it removes on exit, which is the only place that should
happen.

Bytes go to stdout and everything else to stderr, so the redirect above
captures the object and nothing else. ``-T`` matters: without it Docker
allocates a TTY and mangles the binary stream.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def note(message: str) -> None:
    """Progress goes to stderr. stdout is the file."""
    print(message, file=sys.stderr)


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--key",
        help="Fetch a specific backup object instead of the newest one",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="List available backups and exit, newest last",
    )
    args = parser.parse_args()

    from api.services.backup.database import _list_backups
    from api.services.storage import get_storage

    storage = get_storage()

    try:
        listed = await _list_backups(storage)
    except Exception as exc:
        note(f"Could not list backups: {exc}")
        return 1

    if args.list:
        if not listed:
            note("No backups found.")
            return 1
        for key, taken_at in listed:
            print(f"{taken_at.isoformat()}  {key}")
        return 0

    if args.key:
        key = args.key
    elif listed:
        key, taken_at = listed[-1]
        note(f"Newest backup: {key} ({taken_at.isoformat()})")
    else:
        note(
            "No backups found. Check BACKUP_ENABLED and that the nightly job "
            "has run at least once — the api log records run_database_backup."
        )
        return 1

    with tempfile.TemporaryDirectory(prefix="decibyl-fetch-") as workdir:
        local = os.path.join(workdir, "backup.enc")
        if not await storage.adownload_file(key, local):
            note(f"Could not download {key}.")
            return 1

        size = os.path.getsize(local)
        if not size:
            note(f"{key} is empty — this is not a usable backup.")
            return 1

        # Streamed rather than read whole: a production dump is larger than we
        # want resident, and this runs on the same box that is serving calls.
        with open(local, "rb") as handle:
            while chunk := handle.read(1024 * 1024):
                sys.stdout.buffer.write(chunk)
        sys.stdout.buffer.flush()

    note(f"Wrote {size:,} encrypted bytes to stdout.")
    note("Decrypt with ./scripts/rehearse_restore.sh, which cleans up after itself.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
