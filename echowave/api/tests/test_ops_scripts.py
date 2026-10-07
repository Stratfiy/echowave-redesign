"""Stream ops, handoff 11 / 15 H: the operator scripts.

What these defend: the restore drill reads the rehearsal's result line and
builds libpq settings from the database URL; the capacity review finds the
knee, fails when the last good level misses the target, and refuses
production without an explicit flag; the infrastructure checks find no
failure in this repository's deploy path, catch a missing staging key and a
reused secret without printing a value, and treat CloudWatch silence as a
warning rather than health.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from scripts import capacity_review, check_infra, restore_drill
from scripts.load_ramp import LevelResult


def test_restore_drill_reads_the_rehearsal_line():
    line = "2026-10-07T10:00:00Z\trestore_seconds=41\tledger_rows=1200\tdrift_rows=0\tmigration=202610071400ops"
    parsed = restore_drill.parse_rehearsal_line(line)
    assert parsed == {
        "rehearsed_at": "2026-10-07T10:00:00Z",
        "restore_seconds": 41,
        "ledger_rows": 1200,
        "drift_rows": 0,
        "migration": "202610071400ops",
    }
    assert restore_drill.parse_rehearsal_line("garbage") is None


def test_restore_drill_libpq_env():
    env = restore_drill.libpq_env(
        "postgresql+asyncpg://decibyl:p%40ss@db.internal:5433/decibyl"
    )
    assert env == {
        "PGHOST": "db.internal",
        "PGPORT": "5433",
        "PGUSER": "decibyl",
        "PGPASSWORD": "p@ss",
        "PGDATABASE": "decibyl",
    }


def test_restore_drill_refuses_without_the_secret(monkeypatch):
    monkeypatch.delenv("PLATFORM_CREDENTIAL_SECRET", raising=False)
    assert restore_drill.main(["--no-record"]) == 2


def _level(concurrency, latency, errors=0, n=10):
    result = LevelResult(concurrency=concurrency)
    result.latencies_ms = [latency] * n
    result.statuses = [200] * (n - errors) + [500] * errors
    return result


def test_capacity_review_finds_the_knee_and_judges_the_target():
    results = [_level(10, 100), _level(25, 200), _level(50, 9000)]
    review = capacity_review.assess(
        results,
        url="https://staging.example/api/v1/health",
        vcpus=8,
        target=40,
        knee_error_rate=0.1,
        knee_p95_ms=8000,
        assumed_calls_per_vcpu=5.0,
    )
    assert review.knee["concurrency"] == 50
    assert review.last_good == 25
    assert review.outcome == "failed"
    assert any("needs 40" in r for r in review.reasons)
    passed = capacity_review.assess(
        [_level(10, 100), _level(50, 300)],
        url="u",
        vcpus=8,
        target=40,
        knee_error_rate=0.1,
        knee_p95_ms=8000,
        assumed_calls_per_vcpu=5.0,
    )
    assert passed.outcome == "passed" and passed.per_vcpu == 6.25
    page = capacity_review.render_markdown(passed, "now")
    assert "| 50 |" in page and "PASSED" in page
    payload = capacity_review.evidence_payload(passed, None)
    assert payload["kind"] == "capacity_review"
    assert payload["metrics"]["last_good_concurrency"] == 50


def test_capacity_review_reads_the_sizing_assumption_without_the_api():
    assert capacity_review.assumed_calls_per_vcpu() == 5.0


def test_capacity_review_refuses_production():
    assert capacity_review.is_production("https://app.decibyl.ai")
    assert not capacity_review.is_production("https://staging.decibyl.ai")
    assert (
        capacity_review.main(["--base-url", "https://app.decibyl.ai", "--vcpus", "4"])
        == 2
    )


def test_the_repository_deploy_path_has_no_failure():
    checks = check_infra.repo_checks()
    assert checks
    failures = [c for c in checks if c.status == "fail"]
    assert failures == [], failures


def test_parity_catches_missing_keys_and_reused_secrets_without_printing_values():
    prod = check_infra.parse_env(
        "ENVIRONMENT=production\nDATABASE_URL='postgresql://p'\n"
        "PLATFORM_CREDENTIAL_SECRET=same-secret-value\nSENTRY_DSN=https://x\n"
    )
    staging = check_infra.parse_env(
        "ENVIRONMENT=staging\nDATABASE_URL=postgresql://s\n"
        'PLATFORM_CREDENTIAL_SECRET="same-secret-value"\nFEATURE_X=1\n'
    )
    checks = {c.name: c for c in check_infra.parity_checks(prod, staging)}
    assert checks["keys present on both"].status == "fail"
    assert "SENTRY_DSN" in checks["keys present on both"].detail
    assert checks["staging-only keys"].status == "warn"
    assert checks["no shared secrets"].status == "fail"
    assert "PLATFORM_CREDENTIAL_SECRET" in checks["no shared secrets"].detail
    assert checks["environment names"].status == "ok"
    for check in checks.values():
        assert "same-secret-value" not in check.detail
        assert "postgresql://" not in check.detail


class _Rds:
    def describe_db_instances(self, **_):
        return {
            "DBInstances": [
                {
                    "BackupRetentionPeriod": 7,
                    "LatestRestorableTime": datetime.now(UTC) - timedelta(minutes=3),
                    "MultiAZ": False,
                    "StorageEncrypted": True,
                }
            ]
        }


class _S3:
    def get_public_access_block(self, **_):
        return {
            "PublicAccessBlockConfiguration": {
                "BlockPublicAcls": True,
                "IgnorePublicAcls": True,
                "BlockPublicPolicy": True,
                "RestrictPublicBuckets": True,
            }
        }


class _Ecr:
    def describe_repositories(self):
        return {"repositories": [{"repositoryName": "decibyl-api"}]}


class _Cw:
    def __init__(self, alarms):
        self.alarms = alarms

    def describe_alarms(self, **_):
        return {"MetricAlarms": self.alarms}


def test_aws_checks_with_silence_never_counted_as_health():
    checks = {
        c.name: c
        for c in check_infra.aws_checks(
            rds=_Rds(),
            s3=_S3(),
            ecr=_Ecr(),
            cloudwatch=_Cw(
                [{"AlarmName": "decibyl-queue", "StateValue": "INSUFFICIENT_DATA"}]
            ),
            rds_id="decibyl-prod",
            bucket="b",
            namespace=None,
        )
    }
    assert checks["rds point-in-time recovery"].status == "ok"
    assert checks["rds multi-az"].status == "warn"
    assert checks["s3 public access blocked"].status == "ok"
    assert checks["ecr repositories"].status == "warn"
    assert checks["cloudwatch alarms"].status == "warn"
    none = {
        c.name: c
        for c in check_infra.aws_checks(
            cloudwatch=_Cw([]), rds_id=None, bucket=None, namespace=None
        )
    }
    assert none["cloudwatch alarms"].status == "fail"
    assert none["rds"].status == "skipped"
