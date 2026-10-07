"""Infrastructure checks: environment parity, the deploy path the runbook
describes, and (with ``--aws``) the AWS resources it depends on.

Read-only, always. It prints key **names**, counts and verdicts, never a
value: environment parity compares the two environments' keys, and checks
that no secret-named key holds the same value in both (by comparing SHA-256
digests in memory) -- staging must never reuse a production secret.

Three groups, each a list of ``Check(name, status, detail)`` with status
``ok``, ``warn``, ``fail`` or ``skipped``:

``repo``   What the workflows and scripts actually do, checked against what
           OPS-RUNBOOK.md says they do, so the runbook cannot drift silently.
``parity`` Production and staging configuration: the same keys, different
           secrets. From two ``.env`` files, or from Parameter Store paths
           with ``--aws``.
``aws``    RDS point-in-time recovery, Multi-AZ and encryption; S3 public
           access blocked; ECR repositories present; CloudWatch alarms
           present and not silent (INSUFFICIENT_DATA is reported, never
           counted as healthy); the Secrets Manager namespace.

    python -m scripts.check_infra repo
    python -m scripts.check_infra parity --prod-env /path/prod.env --staging-env /path/staging.env
    python -m scripts.check_infra aws --rds-id decibyl-prod --bucket decibyl-prod-recordings

Exit status 1 when any check fails, so CI can gate on it.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]  # the repository root
APP = ROOT / "echowave"

#: Keys whose values legitimately differ and whose absence on one side is a
#: parity break all the same.
SECRET_NAME = re.compile(
    r"(SECRET|PASSWORD|TOKEN|PRIVATE|_KEY$|_KEY_|API_KEY|DSN|CREDENTIAL)", re.I
)
#: Keys that only one environment should have.
PRODUCTION_ONLY = frozenset({"RAZORPAY_WEBHOOK_SECRET_LIVE"})
STAGING_ONLY = frozenset({"STAGING_EMAIL_A", "STAGING_EMAIL_B"})


@dataclass(frozen=True)
class Check:
    group: str
    name: str
    status: str
    detail: str


# --- repo -------------------------------------------------------------------


def _read(path: Path) -> str:
    try:
        return path.read_text()
    except OSError:
        return ""


def repo_checks(root: Path = ROOT) -> list[Check]:
    wf = root / ".github" / "workflows"
    deploy = _read(wf / "deploy.yml")
    staging = _read(wf / "deploy-staging.yml")
    build = _read(wf / "build-images.yml")
    ops = _read(wf / "ops.yml")
    ci_deploy = _read(root / "echowave" / "scripts" / "ci_deploy.sh")
    compose = _read(root / "echowave" / "docker-compose.yaml")
    out: list[Check] = []

    def check(name: str, ok: bool, good: str, bad: str, *, warn: bool = False) -> None:
        out.append(
            Check(
                "repo",
                name,
                "ok" if ok else ("warn" if warn else "fail"),
                good if ok else bad,
            )
        )

    check(
        "workflows at the repository root",
        bool(deploy and staging and ops),
        "deploy.yml, deploy-staging.yml and ops.yml are where GitHub reads them.",
        "A workflow is missing from .github/workflows at the repository root.",
    )
    check(
        "production deploys over SSM with OIDC",
        "aws ssm send-command" in deploy and "role-to-assume" in deploy,
        "deploy.yml assumes a role over OIDC and sends the deploy through SSM.",
        "deploy.yml no longer deploys over SSM with an assumed role.",
    )
    check(
        "staging runs the same deploy script",
        "ci_deploy.sh" in staging and "ci_deploy.sh" in deploy,
        "Both environments run scripts/ci_deploy.sh read from the fetched commit.",
        "Staging and production no longer run the same deploy script.",
    )
    check(
        "staging config is never production's",
        "/decibyl/staging/" in staging,
        "deploy-staging.yml pins CONFIG_SSM_PATH to /decibyl/staging/.",
        "deploy-staging.yml does not pin the staging Parameter Store path.",
    )
    check(
        "deploy migrates, health-checks and rolls back",
        all(
            s in ci_deploy
            for s in ("alembic", "upgrade head", "trap rollback ERR", "HEALTH_URL")
        ),
        "ci_deploy.sh runs migrations, waits for health and rolls back on failure.",
        "ci_deploy.sh lost its migration, health check or rollback.",
    )
    builds_on_box = 'IMAGE_SOURCE="${IMAGE_SOURCE:-build}"' in ci_deploy
    check(
        "images: where the box gets them",
        not builds_on_box,
        "The box pulls images built by CI.",
        "ci_deploy.sh still defaults to IMAGE_SOURCE=build: the box builds its own "
        "images. build-images.yml publishes to GHCR/ECR but is the rollback store "
        "only until the box can pull (KAN-35).",
        warn=True,
    )
    on_push = re.search(r"^on:\s*\n(?:\s*\n)*\s+push:", build, re.M) is not None
    check(
        "images built on merge",
        on_push,
        "build-images.yml runs on push.",
        "build-images.yml runs on workflow_dispatch only: no image is published on "
        "merge, so an IMAGE_TAG rollback needs a manual build first.",
        warn=True,
    )
    check(
        "ops actions are allowlisted",
        "type: choice" in ops and "status" in ops and "OPS_GREP" in ops,
        "ops.yml offers a fixed choice of actions with validated inputs.",
        "ops.yml no longer constrains its actions to a choice.",
    )
    check(
        "api reads .env wholesale",
        "env_file" in compose,
        "docker-compose.yaml injects .env into the api service.",
        "docker-compose.yaml no longer injects .env; settings would be silently absent.",
    )
    return out


# --- parity -----------------------------------------------------------------


_LINE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$")


def parse_env(text: str) -> dict[str, str]:
    """Key -> raw value. Values stay in memory for digest comparison only."""
    out: dict[str, str] = {}
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        match = _LINE.match(line)
        if not match:
            continue
        value = match.group(2).strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        out[match.group(1)] = value
    return out


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def parity_checks(prod: dict[str, str], staging: dict[str, str]) -> list[Check]:
    out: list[Check] = []
    missing = sorted(set(prod) - set(staging) - PRODUCTION_ONLY)
    extra = sorted(set(staging) - set(prod) - STAGING_ONLY)
    out.append(
        Check(
            "parity",
            "keys present on both",
            "ok" if not missing else "fail",
            "Staging has every production key."
            if not missing
            else f"Missing on staging ({len(missing)}): {', '.join(missing)}",
        )
    )
    out.append(
        Check(
            "parity",
            "staging-only keys",
            "ok" if not extra else "warn",
            "None."
            if not extra
            else f"Only on staging ({len(extra)}): {', '.join(extra)} "
            "-- a feature being tried there, or production missing it.",
        )
    )
    shared = [
        k
        for k in sorted(set(prod) & set(staging))
        if SECRET_NAME.search(k) and prod[k] and _digest(prod[k]) == _digest(staging[k])
    ]
    out.append(
        Check(
            "parity",
            "no shared secrets",
            "ok" if not shared else "fail",
            "No secret-named key has the same value in both."
            if not shared
            else f"Same value in production and staging: {', '.join(shared)}. Rotate staging's.",
        )
    )
    env_prod, env_staging = prod.get("ENVIRONMENT"), staging.get("ENVIRONMENT")
    out.append(
        Check(
            "parity",
            "environment names",
            "ok" if env_prod and env_staging and env_prod != env_staging else "fail",
            f"ENVIRONMENT is set and differs ({env_prod} / {env_staging})."
            if env_prod and env_staging and env_prod != env_staging
            else "ENVIRONMENT must be set on both and differ, or telemetry mixes them.",
        )
    )
    return out


def ssm_keys(client, path: str) -> dict[str, str]:
    """Parameter names under a path, with decrypted values held in memory
    for digest comparison only."""
    out: dict[str, str] = {}
    paginator = client.get_paginator("get_parameters_by_path")
    for page in paginator.paginate(Path=path, Recursive=False, WithDecryption=True):
        for parameter in page.get("Parameters", []):
            out[parameter["Name"].rsplit("/", 1)[-1]] = parameter.get("Value", "")
    return out


# --- aws --------------------------------------------------------------------


def aws_checks(
    *,
    rds=None,
    s3=None,
    ecr=None,
    cloudwatch=None,
    secrets=None,
    rds_id: str | None,
    bucket: str | None,
    namespace: str | None,
    now: datetime | None = None,
) -> list[Check]:
    now = now or datetime.now(UTC)
    out: list[Check] = []

    def add(name, status, detail):
        out.append(Check("aws", name, status, detail))

    if rds is not None and rds_id:
        try:
            db = rds.describe_db_instances(DBInstanceIdentifier=rds_id)["DBInstances"][
                0
            ]
            retention = int(db.get("BackupRetentionPeriod") or 0)
            restorable = db.get("LatestRestorableTime")
            fresh = restorable is not None and now - restorable < timedelta(minutes=15)
            add(
                "rds point-in-time recovery",
                "ok" if retention and fresh else "fail",
                f"Retention {retention} days; latest restorable "
                f"{restorable.isoformat() if restorable else 'none'}.",
            )
            add(
                "rds multi-az",
                "ok" if db.get("MultiAZ") else "warn",
                "Multi-AZ."
                if db.get("MultiAZ")
                else "Single-AZ: document the downtime risk.",
            )
            add(
                "rds encrypted",
                "ok" if db.get("StorageEncrypted") else "fail",
                "Storage encrypted."
                if db.get("StorageEncrypted")
                else "Storage NOT encrypted.",
            )
        except Exception as exc:  # noqa: BLE001
            add("rds", "fail", f"{type(exc).__name__} describing {rds_id}.")
    else:
        add("rds", "skipped", "No --rds-id; the bundled Postgres has no PITR.")

    if s3 is not None and bucket:
        try:
            block = s3.get_public_access_block(Bucket=bucket)[
                "PublicAccessBlockConfiguration"
            ]
            all_on = all(
                block.get(k)
                for k in (
                    "BlockPublicAcls",
                    "IgnorePublicAcls",
                    "BlockPublicPolicy",
                    "RestrictPublicBuckets",
                )
            )
            add(
                "s3 public access blocked",
                "ok" if all_on else "fail",
                "All four public-access blocks on." if all_on else f"Blocks: {block}",
            )
        except Exception as exc:  # noqa: BLE001
            add(
                "s3 public access blocked",
                "fail",
                f"{type(exc).__name__} reading {bucket}.",
            )
    else:
        add("s3", "skipped", "No --bucket.")

    if ecr is not None:
        wanted = {"decibyl-api", "decibyl-ui", "decibyl-sandbox"}
        try:
            found = {
                r["repositoryName"]
                for r in ecr.describe_repositories().get("repositories", [])
            }
            missing = sorted(wanted - found)
            add(
                "ecr repositories",
                "ok" if not missing else "warn",
                "decibyl-api, -ui and -sandbox exist."
                if not missing
                else f"Missing: {', '.join(missing)} (the box cannot pull until they exist).",
            )
        except Exception as exc:  # noqa: BLE001
            add(
                "ecr repositories",
                "fail",
                f"{type(exc).__name__} listing repositories.",
            )

    if cloudwatch is not None:
        try:
            alarms = cloudwatch.describe_alarms(AlarmNamePrefix="decibyl").get(
                "MetricAlarms", []
            )
            silent = [
                a["AlarmName"]
                for a in alarms
                if a.get("StateValue") == "INSUFFICIENT_DATA"
            ]
            firing = [a["AlarmName"] for a in alarms if a.get("StateValue") == "ALARM"]
            if not alarms:
                add(
                    "cloudwatch alarms",
                    "fail",
                    "No decibyl* alarms: nothing is watching.",
                )
            elif silent:
                add(
                    "cloudwatch alarms",
                    "warn",
                    f"{len(silent)} alarm(s) have no data -- silence, not health: {', '.join(silent)}",
                )
            else:
                add(
                    "cloudwatch alarms",
                    "ok" if not firing else "warn",
                    f"{len(alarms)} alarms reporting; firing: {', '.join(firing) or 'none'}.",
                )
        except Exception as exc:  # noqa: BLE001
            add("cloudwatch alarms", "fail", f"{type(exc).__name__} listing alarms.")

    if secrets is not None and namespace:
        try:
            listed = secrets.list_secrets(
                Filters=[{"Key": "name", "Values": [namespace]}]
            ).get("SecretList", [])
            add(
                "secrets manager namespace",
                "ok" if listed else "warn",
                f"{len(listed)} secret(s) under {namespace}."
                if listed
                else f"Nothing under {namespace} yet (OPS_SECRET_BACKEND=database is fine).",
            )
        except Exception as exc:  # noqa: BLE001
            add(
                "secrets manager namespace",
                "fail",
                f"{type(exc).__name__} listing secrets.",
            )
    return out


# --- cli --------------------------------------------------------------------


def report(checks: Iterable[Check], as_json: bool) -> int:
    checks = list(checks)
    if as_json:
        print(json.dumps([asdict(c) for c in checks], indent=2))
    else:
        for c in checks:
            print(f"[{c.status:7}] {c.group}: {c.name} -- {c.detail}")
    return 1 if any(c.status == "fail" for c in checks) else 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("group", choices=("repo", "parity", "aws", "all"))
    p.add_argument("--prod-env", type=Path)
    p.add_argument("--staging-env", type=Path)
    p.add_argument("--prod-ssm-path", default="/decibyl/prod/")
    p.add_argument("--staging-ssm-path", default="/decibyl/staging/")
    p.add_argument("--rds-id")
    p.add_argument("--bucket")
    p.add_argument("--secret-namespace", default="decibyl/production/providers/")
    p.add_argument("--region", default="ap-south-1")
    p.add_argument("--json", action="store_true")
    args = p.parse_args(argv)

    checks: list[Check] = []
    if args.group in ("repo", "all"):
        checks += repo_checks()
    if args.group in ("parity", "all"):
        if args.prod_env and args.staging_env:
            checks += parity_checks(
                parse_env(_read(args.prod_env)), parse_env(_read(args.staging_env))
            )
        elif args.group == "parity" or args.group == "all":
            try:
                import boto3

                ssm = boto3.client("ssm", region_name=args.region)
                checks += parity_checks(
                    ssm_keys(ssm, args.prod_ssm_path),
                    ssm_keys(ssm, args.staging_ssm_path),
                )
            except Exception as exc:  # noqa: BLE001
                checks.append(
                    Check(
                        "parity",
                        "parameter store",
                        "skipped",
                        f"{type(exc).__name__}: pass --prod-env and --staging-env, "
                        "or run with AWS credentials.",
                    )
                )
    if args.group in ("aws", "all"):
        try:
            import boto3

            session = boto3.session.Session(region_name=args.region)
            checks += aws_checks(
                rds=session.client("rds"),
                s3=session.client("s3"),
                ecr=session.client("ecr"),
                cloudwatch=session.client("cloudwatch"),
                secrets=session.client("secretsmanager"),
                rds_id=args.rds_id,
                bucket=args.bucket,
                namespace=args.secret_namespace,
            )
        except Exception as exc:  # noqa: BLE001
            checks.append(
                Check("aws", "credentials", "skipped", f"{type(exc).__name__}.")
            )
    return report(checks, args.json)


if __name__ == "__main__":
    raise SystemExit(main())
