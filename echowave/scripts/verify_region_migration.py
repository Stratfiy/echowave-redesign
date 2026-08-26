"""Did the deployment actually move, or only the compute?

Run after moving the box between AWS regions — us-east-1 to ap-south-1 in
August 2026, and any move after it. The checks here are deliberately the ones
`ci_deploy.sh` cannot make: it gates on `GET /api/v1/health`, which reads
configuration and returns without touching Postgres, Redis or S3. That check
passes on a box whose database is empty, whose recordings are still in
Virginia, and whose credential secret was regenerated — three states that look
like a healthy deployment right up until a customer logs in, a recording is
played, or a call tries to authenticate to a carrier.

    # 1. On the OLD box, before you shut it down. Capture what "complete" means.
    docker compose exec -T api python -m scripts.verify_region_migration \
        --emit-baseline > /tmp/old-box.json

    # 2. On the NEW box, after the restore.
    docker compose exec -T api python -m scripts.verify_region_migration \
        --baseline /tmp/old-box.json

Without ``--baseline`` every data check degrades to "is there anything at all",
which catches a box restored from nothing and misses a box restored from a
month-old backup. Take the baseline. It is one command and it is the only
evidence that nothing was left behind.

Exit status is 0 when every check passed, 1 otherwise, so it can gate a
cutover. Warnings do not fail the run — they are the things worth a look that
a script cannot decide for you.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import socket
import sys
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse

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


def section(name: str) -> None:
    print(f"\n— {name} —\n")


def _libpq(url: str) -> str:
    """Strip SQLAlchemy's driver from the scheme.

    ``postgresql+asyncpg://`` is a SQLAlchemy URL; asyncpg itself wants
    ``postgresql://``.
    """
    return url.replace("postgresql+asyncpg://", "postgresql://").replace(
        "postgresql+psycopg://", "postgresql://"
    )


# ---------------------------------------------------------------- the box ---

#: IMDS, not the AWS API. It answers for *this* instance without credentials,
#: which is the point: the question is where the code is running, and a boto3
#: call would answer where the credentials are pointed instead.
_IMDS = "http://169.254.169.254"


def _imds(path: str, timeout: float = 2.0) -> str | None:
    """Read one IMDSv2 metadata key, or None if the service is unreachable.

    Unreachable is a normal outcome here and not an error. The default IMDS hop
    limit is 1, and this script usually runs inside the api container — one hop
    past the host — so the token request is refused unless the operator raised
    ``--http-put-response-hop-limit`` to 2. That is a legitimate hardening
    choice, so the caller warns rather than fails.
    """
    try:
        token_req = urllib.request.Request(
            f"{_IMDS}/latest/api/token",
            method="PUT",
            headers={"X-aws-ec2-metadata-token-ttl-seconds": "60"},
        )
        with urllib.request.urlopen(token_req, timeout=timeout) as resp:
            token = resp.read().decode()
        req = urllib.request.Request(
            f"{_IMDS}/latest/meta-data/{path}",
            headers={"X-aws-ec2-metadata-token": token},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode().strip()
    except (urllib.error.URLError, OSError, TimeoutError):
        return None


async def check_where_this_is(expected_region: str) -> str | None:
    """Which region is this instance in, and which instance is it?

    Returns the public IPv4 if IMDS answered, for the DNS checks later.
    """
    section("Where this box is")

    region = await asyncio.to_thread(_imds, "placement/region")
    if region is None:
        record(
            WARN,
            f"This instance is in {expected_region}",
            "IMDS did not answer. Expected inside a container with the default\n"
            "hop limit of 1 — it is not a fault. Confirm from your laptop:\n"
            "  aws ec2 describe-instances --instance-ids <id> \\\n"
            "      --query 'Reservations[].Instances[].Placement.AvailabilityZone'",
        )
    elif region == expected_region:
        record(PASS, f"This instance is in {expected_region}")
    else:
        record(
            FAIL,
            f"This instance is in {expected_region}",
            f"It is in {region}. Either the old box is still serving traffic, or\n"
            "this script was run on the wrong host.",
        )

    instance_id = await asyncio.to_thread(_imds, "instance-id")
    if instance_id:
        record(
            WARN,
            "EC2_INSTANCE_ID in GitHub matches this instance",
            f"This instance is {instance_id}.\n"
            "Not checkable from here — compare it against the EC2_INSTANCE_ID\n"
            "secret, and against the instance ARN in the DecibylGitHubDeploy\n"
            "policy. A stale id there fails the deploy with InvalidInstanceId.",
        )

    return await asyncio.to_thread(_imds, "public-ipv4")


# ------------------------------------------------------- where data rests ---


async def check_residency(expected_region: str) -> None:
    """The buckets, not the box. Personal data rests where the bucket is."""
    section("Where the data rests")

    from api.constants import (
        BACKUP_MIRROR_BUCKET,
        BACKUP_MIRROR_REGION,
        ENABLE_AWS_S3,
        KYC_BUCKET,
        S3_BUCKET,
        S3_ENDPOINT_URL,
        S3_REGION,
    )

    if S3_REGION == expected_region:
        record(PASS, f"S3_REGION is {expected_region}")
    else:
        record(
            FAIL,
            f"S3_REGION is {expected_region}",
            f"It is {S3_REGION!r}. Recordings and transcripts written from now on\n"
            "come to rest there. Fix .env, then:\n"
            "  docker compose up -d --force-recreate api",
        )

    if not ENABLE_AWS_S3:
        record(
            WARN,
            "Object storage is AWS S3",
            "ENABLE_AWS_S3 is false, so storage is MinIO on this box's own disk.\n"
            "Residency then follows the instance, and the recordings moved only\n"
            "if the minio-data volume was copied. Confirm the object count below\n"
            "against the old box rather than trusting the region.",
        )
        return

    if S3_ENDPOINT_URL:
        record(
            WARN,
            "Bucket location is checkable",
            f"S3_ENDPOINT_URL is set ({S3_ENDPOINT_URL}), so this is an\n"
            "S3-compatible server rather than AWS. Region is a formality there;\n"
            "verify where that server physically is by other means.",
        )
        return

    # get_bucket_location is the authority. S3_REGION being right proves what
    # the client *asks for*, not where the bucket is — and a bucket created in
    # us-east-1 stays in us-east-1 forever. Buckets do not move; a "migration"
    # that changed only the variable writes to Virginia with a Mumbai label,
    # or fails with PermanentRedirect, depending on the call.
    import aioboto3

    session = aioboto3.Session()
    for label, bucket in (
        ("recordings", S3_BUCKET),
        ("KYC documents", KYC_BUCKET),
        ("the backup mirror", BACKUP_MIRROR_BUCKET),
    ):
        if not bucket:
            continue
        try:
            async with session.client("s3", region_name=S3_REGION) as s3:
                resp = await s3.get_bucket_location(Bucket=bucket)
            # us-east-1 is returned as None or "": the API's oldest wart, and
            # exactly the value that matters here, so it cannot be treated as
            # "unknown" and skipped.
            actual = resp.get("LocationConstraint") or "us-east-1"
        except Exception as exc:  # noqa: BLE001 -- reported, never raised
            record(
                FAIL,
                f"The bucket holding {label} is in {expected_region}",
                f"{bucket}: {type(exc).__name__}: {exc}\n"
                "Unreachable is its own answer — the instance role may not carry\n"
                "s3:GetBucketLocation in the new region, or the bucket policy may\n"
                "still name the old VPC endpoint.",
            )
            continue

        if actual == expected_region:
            record(PASS, f"The bucket holding {label} is in {expected_region}", bucket)
        else:
            record(
                FAIL,
                f"The bucket holding {label} is in {expected_region}",
                f"{bucket} is in {actual}. A bucket cannot be moved: create one in\n"
                f"{expected_region}, copy the objects across, repoint the variable.\n"
                "Until then this data is resident in the region you left.",
            )

    if BACKUP_MIRROR_BUCKET and BACKUP_MIRROR_REGION == S3_REGION:
        record(
            WARN,
            "The backup mirror is somewhere else",
            f"Mirror and primary are both in {S3_REGION}. The mirror exists to\n"
            "survive losing the primary; in the same region it survives a\n"
            "hardware failure and nothing larger.",
        )


# --------------------------------------------------- did the data come too ---

#: Tables whose emptiness means the restore did not happen, or happened into
#: the wrong database. Ordered from the ones a fresh box cannot fake.
_CORE_TABLES = (
    "organizations",
    "users",
    "organization_memberships",
    "workflows",
    "workflow_definitions",
    "workflow_runs",
    "workflow_recordings",
    "campaigns",
    "credit_ledger",
    "payments",
    "billing_profiles",
    "telephony_configurations",
    "telephony_phone_numbers",
    "platform_provider_credentials",
    "organization_provider_credentials",
    "provider_rates",
    "subscription_plans",
)

#: A fresh box has these from seeding alone, so a non-zero count proves
#: nothing. Excluded from the "the database is not empty" verdict.
_SEEDED_TABLES = frozenset({"provider_rates", "subscription_plans"})


async def _counts(conn) -> dict[str, int]:
    counts: dict[str, int] = {}
    for table in _CORE_TABLES:
        exists = await conn.fetchval("SELECT to_regclass($1)", f"public.{table}")
        if exists is None:
            continue
        counts[table] = await conn.fetchval(f"SELECT count(*) FROM {table}")  # noqa: S608 -- fixed literal list above
    return counts


async def emit_baseline(conn) -> dict:
    """What the old box holds, as the thing the new box has to match."""
    counts = await _counts(conn)
    newest = None
    if "workflow_runs" in counts:
        newest = await conn.fetchval("SELECT max(created_at) FROM workflow_runs")
    return {
        "captured_at": datetime.now(UTC).isoformat(),
        "alembic_version": await conn.fetchval(
            "SELECT version_num FROM alembic_version"
        ),
        "counts": counts,
        "newest_workflow_run": newest.isoformat() if newest else None,
    }


async def check_data_came_too(conn, baseline: dict | None) -> None:
    section("Whether the data came with it")

    counts = await _counts(conn)
    substantive = {t: n for t, n in counts.items() if t not in _SEEDED_TABLES}

    if not any(substantive.values()):
        record(
            FAIL,
            "The database holds the production data",
            "Every core table is empty. This is a fresh schema, not a restore —\n"
            "`alembic upgrade head` on a new box produces exactly this. Nothing\n"
            "below is meaningful until a backup is restored:\n"
            "  docker compose exec -T api python -m scripts.fetch_latest_backup > b.enc\n"
            "  PLATFORM_CREDENTIAL_SECRET=... ./scripts/rehearse_restore.sh b.enc",
        )
    elif baseline is None:
        record(
            WARN,
            "The database holds the production data",
            "There is data, but with no --baseline there is nothing to hold it\n"
            "against. A month-old restore looks identical to a current one here.\n"
            + "\n".join(f"  {t:<36} {n:>9,}" for t, n in counts.items()),
        )
    else:
        old = baseline.get("counts", {})
        short = {
            t: (old[t], counts.get(t, 0)) for t in old if counts.get(t, 0) < old[t]
        }
        if short:
            record(
                FAIL,
                "Every table is at least as full as the old box",
                f"Captured {baseline.get('captured_at', 'unknown')}.\n"
                + "\n".join(
                    f"  {t:<36} was {was:>9,}  now {now:>9,}"
                    for t, (was, now) in sorted(short.items())
                )
                + "\nA short table is a partial restore. Rows do not go missing on\n"
                "their own; do not write to this box until it is explained.",
            )
        else:
            record(
                PASS,
                "Every table is at least as full as the old box",
                f"{len(old)} tables, baseline captured {baseline.get('captured_at')}",
            )

    if "workflow_runs" in counts:
        newest = await conn.fetchval("SELECT max(created_at) FROM workflow_runs")
        if newest is None:
            record(
                WARN,
                "Call history came across",
                "No workflow_runs at all. Correct only if this deployment has\n"
                "never taken a call.",
            )
        else:
            age = datetime.now(UTC) - newest
            detail = f"newest run {newest:%Y-%m-%d %H:%M} UTC ({age.days}d old)"
            if baseline and baseline.get("newest_workflow_run"):
                was = datetime.fromisoformat(baseline["newest_workflow_run"])
                if newest < was:
                    record(
                        FAIL,
                        "Call history came across",
                        f"{detail}, but the old box had one at {was:%Y-%m-%d %H:%M}"
                        " UTC.\nThe restore predates calls the old box had already"
                        " taken.",
                    )
                else:
                    record(PASS, "Call history came across", detail)
            else:
                record(PASS, "Call history came across", detail)

    # Schema, last. A restore from an older dump connects and answers and is
    # missing columns the code writes to.
    applied = await conn.fetchval("SELECT version_num FROM alembic_version")
    expected = _repo_alembic_head()
    if expected is None:
        record(WARN, "The schema is at this checkout's head", f"applied {applied}")
    elif applied == expected:
        record(PASS, "The schema is at this checkout's head", applied)
    else:
        record(
            FAIL,
            "The schema is at this checkout's head",
            f"applied {applied}, checkout expects {expected}.\n"
            "Run: docker compose exec -T api python -m alembic -c api/alembic.ini "
            "upgrade head",
        )


def _repo_alembic_head() -> str | None:
    try:
        from alembic.config import Config
        from alembic.script import ScriptDirectory

        root = Path(__file__).resolve().parents[1] / "api"
        config = Config(str(root / "alembic.ini"))
        config.set_main_option("script_location", str(root / "alembic"))
        heads = ScriptDirectory.from_config(config).get_heads()
        return heads[0] if len(heads) == 1 else None
    except Exception:  # noqa: BLE001 -- absence is reported as a warning
        return None


# ------------------------------------------------ do the secrets still work ---


async def check_secrets_survived(conn) -> None:
    """Every stored credential is Fernet-encrypted under one key.

    A new box generated a new PLATFORM_CREDENTIAL_SECRET is the quietest
    failure in this whole migration: the platform boots, the UI lists every
    provider key with its masked last four intact — those are stored in
    plaintext beside the ciphertext — and every call fails at the vendor with
    an auth error that looks like a rotated vendor key.
    """
    section("Whether the secrets still decrypt")

    from api.constants import PLATFORM_CREDENTIAL_SECRET

    if not PLATFORM_CREDENTIAL_SECRET:
        record(
            FAIL,
            "PLATFORM_CREDENTIAL_SECRET is set",
            "Unset. Nothing encrypted on the old box can be read here, and\n"
            "storing a new key raises rather than falling back. Copy the value\n"
            "from the old box's .env — it is not regenerable.",
        )
        return

    from cryptography.fernet import Fernet, InvalidToken

    try:
        cipher = Fernet(PLATFORM_CREDENTIAL_SECRET.encode())
    except Exception as exc:  # noqa: BLE001 -- reported, never raised
        record(
            FAIL,
            "PLATFORM_CREDENTIAL_SECRET is a valid Fernet key",
            f"{type(exc).__name__}: {exc}",
        )
        return
    record(PASS, "PLATFORM_CREDENTIAL_SECRET is set and well-formed")

    for table, label in (
        ("platform_provider_credentials", "platform provider keys"),
        ("organization_provider_credentials", "customer provider keys"),
    ):
        if await conn.fetchval("SELECT to_regclass($1)", f"public.{table}") is None:
            continue
        rows = await conn.fetch(
            f"SELECT component, provider, encrypted_key, key_last_four "  # noqa: S608 -- fixed literal list above
            f"FROM {table} WHERE is_active"
        )
        if not rows:
            record(WARN, f"The {label} decrypt", "None stored.")
            continue

        broken: list[str] = []
        mismatched: list[str] = []
        for row in rows:
            try:
                plaintext = cipher.decrypt(row["encrypted_key"].encode()).decode()
            except (InvalidToken, Exception):  # noqa: B014 -- InvalidToken is the case that matters
                broken.append(f"{row['component']}/{row['provider']}")
                continue
            # last_four is stored in plaintext, so it is an independent witness
            # that the decryption produced the original key and not merely
            # something that decrypted.
            if row["key_last_four"] and not plaintext.endswith(row["key_last_four"]):
                mismatched.append(f"{row['component']}/{row['provider']}")

        if broken:
            record(
                FAIL,
                f"The {label} decrypt",
                f"{len(broken)} of {len(rows)} failed: {', '.join(broken[:8])}\n"
                "The secret on this box is not the one they were encrypted with.\n"
                "Restore the old .env value, or re-enter every key by hand.",
            )
        elif mismatched:
            record(
                FAIL,
                f"The {label} decrypt",
                f"Decrypted, but the last four do not match for: "
                f"{', '.join(mismatched[:8])}",
            )
        else:
            record(PASS, f"The {label} decrypt", f"all {len(rows)} of them")

    # Telephony credentials take the other shape — encrypted values inside a
    # JSON blob, marked with a prefix. decrypt() is tolerant and leaves a value
    # it could not read still wearing its marker, which is how failure shows.
    if (
        await conn.fetchval("SELECT to_regclass($1)", "public.telephony_configurations")
        is not None
    ):
        from api.services.telephony.credential_encryption import PREFIX, decrypt

        rows = await conn.fetch(
            "SELECT id, provider, credentials FROM telephony_configurations"
        )
        if not rows:
            record(WARN, "The carrier credentials decrypt", "None configured.")
        else:
            stuck = []
            for row in rows:
                creds = row["credentials"]
                if isinstance(creds, str):
                    creds = json.loads(creds)
                readable = decrypt(row["provider"], creds)
                if any(
                    isinstance(v, str) and v.startswith(PREFIX)
                    for v in readable.values()
                ):
                    stuck.append(f"{row['provider']}#{row['id']}")
            if stuck:
                record(
                    FAIL,
                    "The carrier credentials decrypt",
                    f"{len(stuck)} of {len(rows)} unreadable: {', '.join(stuck[:8])}\n"
                    "Outbound calls on these carriers will fail to authenticate.",
                )
            else:
                record(PASS, "The carrier credentials decrypt", f"all {len(rows)}")


# ------------------------------------------------ are the recordings there ---


async def check_recordings_reachable(conn, sample: int) -> None:
    """Rows survived the dump. Objects are a separate migration, and are not.

    A database restore brings every recording *URL* across and not one
    recording. If the bucket did not move with it, this is the check that says
    so — rather than a customer opening a call from last month.
    """
    section("Whether the recordings are still reachable")

    if (
        await conn.fetchval("SELECT to_regclass($1)", "public.workflow_recordings")
        is None
    ):
        record(WARN, "Stored recordings are reachable", "No workflow_recordings table.")
        return

    rows = await conn.fetch(
        "SELECT storage_key, storage_backend FROM workflow_recordings "
        "WHERE storage_key IS NOT NULL AND storage_key <> '' "
        "ORDER BY id DESC LIMIT $1",
        sample,
    )
    if not rows:
        record(
            WARN,
            "Stored recordings are reachable",
            "No recordings stored. Correct only if none was ever uploaded.",
        )
        return

    from api.services.storage import get_storage_for_backend

    missing: list[str] = []
    errored: list[str] = []
    for row in rows:
        try:
            fs = get_storage_for_backend(row["storage_backend"])
            meta = await fs.aget_file_metadata(row["storage_key"])
        except Exception as exc:  # noqa: BLE001 -- reported per object
            errored.append(f"{row['storage_key']}: {type(exc).__name__}")
            continue
        if meta is None:
            missing.append(row["storage_key"])

    if errored:
        record(
            FAIL,
            "Stored recordings are reachable",
            f"{len(errored)} of {len(rows)} errored:\n"
            + "\n".join(f"  {e}" for e in errored[:5])
            + "\nA PermanentRedirect here means the bucket is in a different\n"
            "region than S3_REGION claims. An AccessDenied means the new\n"
            "instance role does not carry the bucket policy the old one did.",
        )
    elif missing:
        record(
            FAIL,
            "Stored recordings are reachable",
            f"{len(missing)} of {len(rows)} newest recordings are not in the bucket:\n"
            + "\n".join(f"  {k}" for k in missing[:5])
            + "\nThe rows came across and the objects did not. Sync the bucket\n"
            "before anyone is told the migration is done.",
        )
    else:
        record(
            PASS,
            "Stored recordings are reachable",
            f"{len(rows)} newest sampled, all present",
        )


# --------------------------------------------- does the world point here ---


async def check_the_world_points_here(public_ip: str | None) -> None:
    """DNS, TURN and the carriers. The box moved; the pointers may not have."""
    section("Whether the outside world points at this box")

    from api.constants import PUBLIC_BASE_URL, TURN_SECRET

    turn_host = os.getenv("TURN_HOST") or None

    if not PUBLIC_BASE_URL:
        record(
            FAIL,
            "PUBLIC_BASE_URL is set",
            "Unset. Webhook and media URLs point at localhost, so carriers post\n"
            "nowhere and recordings embed unreachable links.",
        )
        return

    host = urlparse(PUBLIC_BASE_URL).hostname
    if not host:
        record(FAIL, "PUBLIC_BASE_URL is a URL", f"Cannot parse {PUBLIC_BASE_URL!r}")
        return

    for label, name in (("PUBLIC_BASE_URL", host), ("TURN_HOST", turn_host)):
        if not name:
            if label == "TURN_HOST" and TURN_SECRET:
                record(
                    WARN,
                    "TURN_HOST resolves to this box",
                    "TURN_SECRET is set but TURN_HOST is not. WebRTC callers get\n"
                    "no relay candidate, which fails only for the callers behind\n"
                    "a symmetric NAT — i.e. intermittently, in the field.",
                )
            continue
        try:
            resolved = sorted(
                {
                    info[4][0]
                    for info in await asyncio.to_thread(
                        socket.getaddrinfo, name, None, socket.AF_INET
                    )
                }
            )
        except OSError as exc:
            record(FAIL, f"{label} resolves to this box", f"{name}: {exc}")
            continue

        if public_ip is None:
            record(
                WARN,
                f"{label} resolves to this box",
                f"{name} -> {', '.join(resolved)}\n"
                "IMDS did not answer, so this cannot be compared automatically.\n"
                "Check it against the new instance's Elastic IP by eye.",
            )
        elif public_ip in resolved:
            record(PASS, f"{label} resolves to this box", f"{name} -> {public_ip}")
        else:
            record(
                FAIL,
                f"{label} resolves to this box",
                f"{name} -> {', '.join(resolved)}, but this box is {public_ip}.\n"
                "Traffic is still going to the old instance. Until the record\n"
                "moves, the box answering your customers is the one in the\n"
                "region you left — and it is the one taking the calls.",
            )

    record(
        WARN,
        "The carriers and Razorpay were re-pointed",
        "Not checkable from here, and not covered by DNS if any webhook was\n"
        "registered against a bare IP rather than the hostname:\n"
        "  - Razorpay -> Settings -> Webhooks: "
        f"{PUBLIC_BASE_URL}/api/v1/billing/razorpay/webhook\n"
        "  - each carrier's inbound/status callback URL\n"
        "  - Google OAuth redirect URIs, if the hostname changed\n"
        "  - any allowlist at a vendor that named the old box's egress IP",
    )


# ------------------------------------------------------------ the plumbing ---


async def check_redis() -> None:
    section("Redis")

    from api.constants import REDIS_URL

    if not REDIS_URL:
        record(FAIL, "Redis is reachable", "REDIS_URL is unset.")
        return
    try:
        import redis.asyncio as aioredis

        client = aioredis.from_url(REDIS_URL)
        try:
            await client.ping()
            keys = await client.dbsize()
        finally:
            await client.aclose()
    except Exception as exc:  # noqa: BLE001 -- reported, never raised
        record(
            FAIL,
            "Redis is reachable",
            f"{type(exc).__name__}: {exc}\n"
            "The arq worker, the scheduled backup and every queued run depend\n"
            "on it.",
        )
        return
    record(PASS, "Redis is reachable", f"{keys} keys")


async def check_old_box_is_off() -> None:
    section("The box you left")

    record(
        WARN,
        "The old instance is stopped",
        "Not checkable from here, and it is the check people skip. While the\n"
        "old box runs it keeps its own arq schedule: it takes backups into the\n"
        "same bucket, prunes by the same retention rule, and dials campaigns\n"
        "from a database that is now a fork. Stop it — do not terminate it —\n"
        "until this script is green on the new one, then snapshot and\n"
        "terminate.\n"
        "  aws ec2 stop-instances --region us-east-1 --instance-ids <old-id>",
    )


# ------------------------------------------------------------------- main ---


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database-url",
        default=os.getenv("DATABASE_URL"),
        help="Defaults to DATABASE_URL, i.e. the database the app actually uses",
    )
    parser.add_argument(
        "--region",
        default="ap-south-1",
        help="The region everything is supposed to be in now (default ap-south-1)",
    )
    parser.add_argument(
        "--baseline",
        help="JSON from --emit-baseline on the old box, to compare row counts against",
    )
    parser.add_argument(
        "--emit-baseline",
        action="store_true",
        help="Print this database's counts as JSON and exit. Run on the OLD box.",
    )
    parser.add_argument(
        "--sample",
        type=int,
        default=10,
        help="How many recent recordings to probe in the bucket (default 10)",
    )
    args = parser.parse_args()

    if not args.database_url:
        print("No --database-url and no DATABASE_URL in the environment.")
        return 1

    import asyncpg

    try:
        conn = await asyncpg.connect(_libpq(args.database_url), timeout=15)
    except Exception as exc:  # noqa: BLE001 -- reported, never raised
        print(f"Cannot reach the database: {type(exc).__name__}: {exc}")
        return 1

    try:
        if args.emit_baseline:
            # stdout is the file; everything else would corrupt the redirect.
            print(json.dumps(await emit_baseline(conn), indent=2))
            return 0

        baseline = None
        if args.baseline:
            baseline = json.loads(Path(args.baseline).read_text())

        print(f"Region migration readiness — expecting {args.region}")
        print("=" * 68)

        public_ip = await check_where_this_is(args.region)
        await check_residency(args.region)
        await check_data_came_too(conn, baseline)
        await check_secrets_survived(conn)
        await check_recordings_reachable(conn, args.sample)
        await check_the_world_points_here(public_ip)
        await check_redis()
        await check_old_box_is_off()
    finally:
        await conn.close()

    failures = [r for r in _results if r[0] == FAIL]
    warnings = [r for r in _results if r[0] == WARN]

    print("\n" + "=" * 68)
    print(
        f"{len([r for r in _results if r[0] == PASS])} passed, "
        f"{len(failures)} failed, {len(warnings)} to look at"
    )

    if warnings:
        print("\nLook at these — a script cannot decide them:")
        for _, title in warnings:
            print(f"  - {title}")

    if failures:
        print("\nThe migration is not complete until these are green:")
        for _, title in failures:
            print(f"  - {title}")
        return 1

    print("\nEverything checkable from inside the box has moved.")
    print("The warnings above are the rest of the migration.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
