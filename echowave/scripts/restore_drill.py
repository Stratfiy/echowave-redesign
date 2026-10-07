"""The backup restore drill, end to end, with evidence (handoff 15 H).

``rehearse_restore.sh`` proves one backup restores and its ledger
reconciles. This runs the whole drill the way an operator would on the box,
and leaves the proof where the console can see it:

1. fetch the newest encrypted backup (``scripts.fetch_latest_backup``), or
   use ``--backup-file``;
2. restore it into a scratch database with ``rehearse_restore.sh`` --
   decrypt into a temporary directory, restore, check the tables, the
   migration head and that every ledger row's running balance reconciles,
   then drop the scratch database;
3. record an ``ops_evidence`` row of kind ``restore_drill``: passed or
   failed, the restore time, ledger rows, drift rows and migration -- never
   a data row;
4. print a JSON summary.

Run it inside the api container, where ``DATABASE_URL``,
``PLATFORM_CREDENTIAL_SECRET``, ``psql`` and ``pg_restore`` already are::

    docker compose exec -T api python -m scripts.restore_drill

The scratch database lives on the same server as the live one unless
``--scratch-url`` points elsewhere (a separate RDS instance is better for a
production drill: a restore competes for the live server's I/O). It is
created and dropped by the rehearsal; the live database is never written.

Exit status is the rehearsal's: 0 when the restore reconciled.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.parse import unquote, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

SCRIPTS = Path(__file__).resolve().parent
LINE = re.compile(
    r"^(?P<at>\S+)\trestore_seconds=(?P<seconds>\d+)\tledger_rows=(?P<ledger>\d+)"
    r"\tdrift_rows=(?P<drift>\d+)\tmigration=(?P<migration>\S+)$"
)


def libpq_env(url: str) -> dict[str, str]:
    """PGHOST, PGPORT, PGUSER, PGPASSWORD from a SQLAlchemy or libpq URL,
    for the createdb / psql / pg_restore the rehearsal runs."""
    parsed = urlparse(url.replace("+asyncpg", "").replace("+psycopg", ""))
    env: dict[str, str] = {}
    if parsed.hostname:
        env["PGHOST"] = parsed.hostname
    if parsed.port:
        env["PGPORT"] = str(parsed.port)
    if parsed.username:
        env["PGUSER"] = unquote(parsed.username)
    if parsed.password:
        env["PGPASSWORD"] = unquote(parsed.password)
    if parsed.path and parsed.path.strip("/"):
        # The maintenance database createdb connects to; never restored into.
        env["PGDATABASE"] = parsed.path.strip("/")
    return env


def parse_rehearsal_line(line: str) -> dict[str, object] | None:
    """The last line ``rehearse_restore.sh`` appends to its log."""
    match = LINE.match(line.strip())
    if not match:
        return None
    return {
        "rehearsed_at": match["at"],
        "restore_seconds": int(match["seconds"]),
        "ledger_rows": int(match["ledger"]),
        "drift_rows": int(match["drift"]),
        "migration": match["migration"],
    }


def fetch_backup(destination: Path) -> int:
    with destination.open("wb") as handle:
        return subprocess.run(
            [sys.executable, "-m", "scripts.fetch_latest_backup"],
            cwd=SCRIPTS.parent,
            stdout=handle,
            check=False,
        ).returncode


def rehearse(backup: Path, log: Path, env: dict[str, str]) -> int:
    return subprocess.run(
        ["bash", str(SCRIPTS / "rehearse_restore.sh"), str(backup)],
        env={**os.environ, **env, "REHEARSAL_LOG": str(log)},
        check=False,
    ).returncode


async def record(
    outcome: str, summary: str, metrics: dict[str, object], link: str | None
) -> None:
    from api.db import db_client
    from api.services.ops import evidence

    async with db_client.async_session() as session:
        await evidence.record(
            session,
            kind="restore_drill",
            outcome=outcome,
            summary=summary,
            metrics=metrics,
            link=link,
        )
        await session.commit()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--backup-file", type=Path, help="Use this encrypted backup.")
    parser.add_argument(
        "--scratch-url",
        default=None,
        help="Server for the scratch database (default: DATABASE_URL's server).",
    )
    parser.add_argument("--scratch-db", default="decibyl_restore_drill")
    parser.add_argument(
        "--no-record", action="store_true", help="Do not write evidence."
    )
    parser.add_argument(
        "--link", default=None, help="Where the full log lives, if anywhere."
    )
    args = parser.parse_args(argv)

    if not os.environ.get("PLATFORM_CREDENTIAL_SECRET"):
        print(
            "PLATFORM_CREDENTIAL_SECRET is not set; backups cannot be decrypted.",
            file=sys.stderr,
        )
        return 2
    server = args.scratch_url or os.environ.get("DATABASE_URL", "")
    env = {**libpq_env(server), "SCRATCH_DB": args.scratch_db}

    with tempfile.TemporaryDirectory(prefix="restore-drill-") as workdir:
        work = Path(workdir)
        backup = args.backup_file
        if backup is None:
            backup = work / "backup.enc"
            if fetch_backup(backup) != 0 or backup.stat().st_size == 0:
                summary = "No backup could be fetched; nothing was restored."
                if not args.no_record:
                    asyncio.run(record("failed", summary, {}, args.link))
                print(json.dumps({"outcome": "failed", "summary": summary}))
                return 1
        log = work / "rehearsal.log"
        code = rehearse(backup, log, env)
        lines = log.read_text().splitlines() if log.exists() else []
        metrics = (parse_rehearsal_line(lines[-1]) if lines else None) or {}

    outcome = "passed" if code == 0 else "failed"
    if outcome == "passed":
        summary = (
            f"Restored and reconciled in {metrics.get('restore_seconds')} s; "
            f"{metrics.get('ledger_rows')} ledger rows, migration {metrics.get('migration')}."
        )
    else:
        summary = f"Restore rehearsal failed (exit {code}); see the drill log."
    if not args.no_record:
        asyncio.run(record(outcome, summary, metrics, args.link))
    print(json.dumps({"outcome": outcome, "summary": summary, "metrics": metrics}))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
