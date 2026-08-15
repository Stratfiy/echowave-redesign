"""Is this database ready to be the one that holds the money?

Run it twice: against the new managed instance **before** you cut over, and
against it again **after**. The checks are the ones whose failure is quiet —
each produces a database that looks fine and is wrong in a way you discover
later, usually during an incident.

    # Before the cutover, against the empty RDS instance:
    python -m scripts.verify_managed_database \
        --database-url postgresql://decibyl:pw@decibyl-prod.xxx.ap-south-1.rds.amazonaws.com:5432/decibyl \
        --rds-instance decibyl-prod

    # After restoring into it, from the box:
    docker compose exec api python -m scripts.verify_managed_database --rds-instance decibyl-prod

With no ``--database-url`` it reads ``DATABASE_URL``, so the second form checks
the database the application is actually talking to — which is the question,
and not the same as the one you think you configured.

``--rds-instance`` adds the point-in-time recovery checks via boto3. Skip it
for a non-AWS provider and verify the equivalent in that provider's console.

Exit status is 0 when every check passed, 1 otherwise, so it can gate a deploy.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

PASS = "  ok  "
FAIL = " FAIL "
WARN = " warn "

_results: list[tuple[str, str]] = []


def record(mark: str, title: str, detail: str = "") -> None:
    _results.append((mark, title))
    print(f"[{mark}] {title}")
    for line in detail.splitlines():
        if line:
            print(f"        {line}")


def _libpq(url: str) -> str:
    """Strip SQLAlchemy's driver from the scheme.

    ``postgresql+asyncpg://`` is a SQLAlchemy URL; asyncpg itself wants
    ``postgresql://``. Passing the former through is the single most common way
    this kind of script fails on its first line.
    """
    return url.replace("postgresql+asyncpg://", "postgresql://").replace(
        "postgresql+psycopg://", "postgresql://"
    )


def repo_alembic_head() -> str | None:
    """The migration head this checkout expects."""
    try:
        from alembic.config import Config
        from alembic.script import ScriptDirectory

        root = Path(__file__).resolve().parents[1] / "api"
        config = Config(str(root / "alembic.ini"))
        config.set_main_option("script_location", str(root / "alembic"))
        heads = ScriptDirectory.from_config(config).get_heads()
        return heads[0] if len(heads) == 1 else None
    except Exception:
        return None


async def check_database(url: str) -> None:
    import asyncpg

    print("\n— The database itself —\n")

    try:
        conn = await asyncpg.connect(_libpq(url), timeout=15)
    except Exception as exc:
        record(
            FAIL,
            "The database is reachable",
            f"{exc}\n"
            "Check the security group allows 5432 from this host, and that the\n"
            "instance is in the same VPC as the application.",
        )
        return

    try:
        version = await conn.fetchval("SHOW server_version")
        record(PASS, "The database is reachable", f"PostgreSQL {version}")

        # pgvector. The knowledge base migration runs CREATE EXTENSION vector,
        # and RDS ships the extension without enabling it — so this fails with
        # a message that reads like a broken migration rather than a missing
        # extension, on a fresh instance, at the worst moment.
        installed = await conn.fetchval(
            "SELECT 1 FROM pg_extension WHERE extname = 'vector'"
        )
        if installed:
            record(PASS, "pgvector is enabled")
        else:
            available = await conn.fetchval(
                "SELECT 1 FROM pg_available_extensions WHERE name = 'vector'"
            )
            record(
                FAIL,
                "pgvector is enabled",
                "The extension is "
                + ("available but not created." if available else "NOT AVAILABLE.")
                + "\nRun: CREATE EXTENSION vector;  (as the master user)"
                + (
                    ""
                    if available
                    else "\nThis provider may not support pgvector at all."
                ),
            )

        # Migration head. A restore from a dump taken on an older schema leaves
        # a database that connects, answers, and is missing columns the code
        # writes to.
        try:
            applied = await conn.fetchval("SELECT version_num FROM alembic_version")
        except Exception:
            applied = None

        expected = repo_alembic_head()
        if applied is None:
            record(
                WARN,
                "The schema is at this checkout's head",
                "No alembic_version row — the schema has not been created yet.\n"
                "Expected before the restore; run `alembic upgrade head` after it.",
            )
        elif expected is None:
            record(WARN, "The schema is at this checkout's head", f"Applied: {applied}")
        elif applied == expected:
            record(PASS, "The schema is at this checkout's head", applied)
        else:
            record(
                FAIL,
                "The schema is at this checkout's head",
                f"Applied {applied}, this checkout expects {expected}.\n"
                "Run: alembic upgrade head",
            )

        await _check_the_money(conn)
    finally:
        await conn.close()


async def _check_the_money(conn) -> None:
    """The ledger came across, and it still adds up.

    A row count proves the table is not empty and nothing else. Every ledger
    row records the running balance it produced, so each must equal the
    previous plus its own delta — which is what catches a restore that dropped
    rows out of the middle. That failure leaves every count plausible.
    """
    print("\n— The money —\n")

    try:
        rows = await conn.fetchval("SELECT count(*) FROM credit_ledger")
        payments = await conn.fetchval("SELECT count(*) FROM payments")
        documents = await conn.fetchval("SELECT count(*) FROM tax_documents")
    except Exception as exc:
        record(FAIL, "The money tables are present", str(exc))
        return

    record(
        PASS,
        "The money tables are present",
        f"{rows} ledger rows, {payments} payments, {documents} tax documents",
    )

    if not rows:
        record(
            WARN,
            "The ledger reconciles",
            "No ledger rows to check. Expected on an empty instance before the\n"
            "restore; an incident afterwards.",
        )
        return

    drift = await conn.fetchval(
        """
        SELECT count(*) FROM (
          SELECT balance_after_paise
               - lag(balance_after_paise, 1, 0) OVER w
               - delta_paise AS drift
            FROM credit_ledger
          WINDOW w AS (PARTITION BY organization_id ORDER BY id)
        ) AS reconciliation WHERE drift <> 0
        """
    )

    if drift == 0:
        record(PASS, "The ledger reconciles", f"All {rows} rows.")
    else:
        record(
            FAIL,
            "The ledger reconciles",
            f"{drift} row(s) whose running balance does not follow from the\n"
            "previous row. The restore produced a money history that is\n"
            "internally inconsistent. Do NOT cut over. Row counts alone would\n"
            "have passed this.",
        )


def check_pitr(instance_id: str) -> None:
    """Whether this instance can actually be restored to a point in time.

    The setting whose absence is invisible: snapshots still appear, the
    instance is healthy, and the recovery point is silently 'last snapshot'.
    """
    print("\n— Point-in-time recovery —\n")

    try:
        import boto3
    except ImportError:
        record(WARN, "PITR is enabled", "boto3 not installed; check the console.")
        return

    try:
        client = boto3.client("rds")
        described = client.describe_db_instances(DBInstanceIdentifier=instance_id)
        instance = described["DBInstances"][0]
    except Exception as exc:
        record(
            WARN,
            "PITR is enabled",
            f"Could not describe {instance_id}: {exc}\n"
            "Check credentials and region, or verify in the console.",
        )
        return

    retention = instance.get("BackupRetentionPeriod", 0)
    if retention and retention > 0:
        record(
            PASS,
            "PITR is enabled",
            f"Automated backups retained {retention} day(s), so any second in\n"
            "that window is restorable.",
        )
    else:
        record(
            FAIL,
            "PITR is enabled",
            "BackupRetentionPeriod is 0 — automated backups and continuous WAL\n"
            "archiving are OFF. This instance has the same 24-hour recovery\n"
            "point as the container it replaced, which is the whole reason for\n"
            "moving.\n"
            f"Fix: aws rds modify-db-instance --db-instance-identifier "
            f"{instance_id} --backup-retention-period 7 --apply-immediately",
        )

    latest = instance.get("LatestRestorableTime")
    if latest is None:
        record(
            WARN,
            "A restorable point exists",
            "No LatestRestorableTime yet. Normal for the first few minutes\n"
            "after enabling backups; an incident if it persists.",
        )
    else:
        from datetime import UTC, datetime

        age_minutes = (datetime.now(UTC) - latest).total_seconds() / 60
        if age_minutes <= 15:
            record(
                PASS,
                "A restorable point exists",
                f"Latest restorable time is {age_minutes:.0f} minutes ago.",
            )
        else:
            record(
                FAIL,
                "A restorable point exists",
                f"Latest restorable time is {age_minutes:.0f} minutes ago. WAL\n"
                "archiving is lagging or stopped; the real recovery point is\n"
                "worse than the retention setting suggests.",
            )

    if instance.get("StorageEncrypted"):
        record(PASS, "Storage is encrypted")
    else:
        record(
            FAIL,
            "Storage is encrypted",
            "This instance holds every phone number, recording reference and\n"
            "invoice. Encryption cannot be enabled in place — it needs a\n"
            "snapshot restore, so fix it before the cutover, not after.",
        )


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database-url",
        default=os.getenv("DATABASE_URL"),
        help="Defaults to DATABASE_URL, i.e. the database the app actually uses",
    )
    parser.add_argument(
        "--rds-instance",
        help="RDS instance identifier, to additionally check PITR via boto3",
    )
    args = parser.parse_args()

    print("Managed database readiness")
    print("=" * 60)

    if not args.database_url:
        print("\nNo --database-url and no DATABASE_URL in the environment.")
        return 1

    # Never print the URL: it carries the password.
    host = args.database_url.rsplit("@", 1)[-1]
    print(f"\nChecking {host}")

    await check_database(args.database_url)

    if args.rds_instance:
        check_pitr(args.rds_instance)
    else:
        print("\n— Point-in-time recovery —\n")
        record(
            WARN,
            "PITR is enabled",
            "Not checked — pass --rds-instance, or verify it in your\n"
            "provider's console. This is the reason for the migration, so it\n"
            "is worth confirming rather than assuming.",
        )

    failures = [r for r in _results if r[0] == FAIL]
    warnings = [r for r in _results if r[0] == WARN]

    print("\n" + "=" * 60)
    print(
        f"{len([r for r in _results if r[0] == PASS])} passed, "
        f"{len(failures)} failed, {len(warnings)} to look at"
    )

    if failures:
        print("\nDo not cut over until these are green:")
        for _, title in failures:
            print(f"  - {title}")
        return 1

    print("\nThis database is ready to hold the ledger.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
