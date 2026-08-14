"""Restore the newest backup into a scratch database and check the data is there.

A backup nobody has restored is a hypothesis. This is how it stops being one.

    python -m scripts.rehearse_restore              # the newest backup
    python -m scripts.rehearse_restore --key backups/postgres/2026/08/01/decibyl-...enc

Nothing touches the live database. The dump is restored into a scratch database
created for the run and dropped afterwards, pass or fail — a scratch copy of
production left on the box is its own incident.

What it checks is the point. "pg_restore exited 0" is another write-path fact;
these are questions about the money:

  * Is the credit ledger there, and does it hold as many rows as live?
  * Does the running balance still reconcile? A dump missing rows from the
    middle passes a row count and fails this.
  * Do settled payments still sum to something?
  * How far behind live is the newest row?

Exits non-zero if the rehearsal failed, so it can be run from cron and noticed.

Needs `pg_restore` and `psql` on the path, and `PLATFORM_CREDENTIAL_SECRET` —
or `PLATFORM_CREDENTIAL_SECRET_PREVIOUS` as well if the backup predates a
rotation.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

from api.db import db_client
from api.services.backup import rehearsal


async def main(key: str | None) -> int:
    async with db_client.async_session() as session:
        try:
            result = await rehearsal.rehearse(session, key=key)
        except rehearsal.RehearsalError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        await session.commit()

    print(f"Backup: {result.key}")
    print(f"Result: {'PASSED' if result.ok else 'FAILED'}")
    if result.error:
        print(f"  {result.error}")

    for name, finding in sorted(result.checks.items()):
        if not isinstance(finding, dict):
            continue
        note = finding.get("note", "")
        if "restored" in finding:
            print(
                f"  {name}: restored={finding['restored']} "
                f"live={finding['live']}  {note}"
            )
        else:
            rest = {k: v for k, v in finding.items() if k != "note"}
            print(f"  {name}: {json.dumps(rest)}  {note}")

    if not result.ok:
        print(
            "\nThis backup did not restore cleanly. Do not rely on it — find "
            "out why before the next incident does it for you.",
            file=sys.stderr,
        )
    return 0 if result.ok else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--key",
        default=None,
        help="A specific backup object. Defaults to the newest.",
    )
    args = parser.parse_args()
    raise SystemExit(asyncio.run(main(args.key)))
