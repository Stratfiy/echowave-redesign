"""The deploy must not destroy the evidence of its own failure.

Three deploys failed on 13 Sep 2026 and none could be diagnosed, because the
compose-up failure path rolled back without capturing anything the api said on
its way out. The container that held the answer existed for about two seconds
and was then replaced.

The other half is worse. `db_revision` decides whether rolling back is safe —
whether the database is at a migration the previous commit can even read — and
it was asking the local `postgres` service, which since the managed-Postgres
cutover is not the database the application uses. That service still runs and
still answers, so the guard was returning a plausible revision from the wrong
database. Guards that answer confidently about the wrong thing do not fail
loudly; they approve the unsafe thing. This is the guard added after deploy #89
turned a failed deploy into an outage.
"""

import re
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "ci_deploy.sh"


def _source() -> str:
    return SCRIPT.read_text()


def _db_revision_body() -> str:
    body = _source().split("db_revision() {", 1)[1]
    return body.split("\n}", 1)[0]


class TestTheRollbackGuardReadsTheRealDatabase:
    def test_it_reads_the_url_the_application_reads(self):
        assert "DATABASE_URL" in _db_revision_body()

    def test_it_no_longer_queries_the_local_postgres_service(self):
        # `exec -T postgres` is the old query. The service is still in the
        # compose file and still healthy, which is exactly why naming it here
        # is a silent wrong answer rather than an error.
        assert "exec -T postgres" not in _db_revision_body()

    def test_it_does_not_exec_into_a_container_that_is_dead_by_then(self):
        # This function runs because the api would not start. There is nothing
        # to exec into; it has to be a one-off container from the image.
        body = _db_revision_body()
        assert "run --rm --no-deps" in body
        assert "compose exec" not in body

    def test_it_strips_the_driver_suffix_psql_cannot_parse(self):
        assert "+asyncpg" in _db_revision_body()


class TestTheFailurePathKeepsTheLogs:
    def test_rollback_captures_the_api_logs(self):
        assert "capture_api_logs" in _source()

    def test_it_captures_before_it_replaces_anything(self):
        # Ordering is the whole point: after the checkout and rebuild, the
        # container holding the answer is gone.
        body = _source().split("rollback() {", 1)[1].split("\n}", 1)[0]
        assert body.index("capture_api_logs") < body.index("checkout --detach")


class TestCredentialsNeverReachTheDeployLog:
    """The redaction is run, not read.

    A startup failure is the likeliest place for a connection string to be
    printed — OperationalError renders the URL it failed on, password included
    — and this log is readable by anyone with read access to the repository.
    """

    def _redact(self, line: str) -> str:
        pattern = re.search(r"sed -E '([^']+)'", _source())
        assert pattern, "the redaction was removed from capture_api_logs"
        return subprocess.run(
            ["sed", "-E", pattern.group(1)],
            input=line,
            capture_output=True,
            text=True,
            check=True,
        ).stdout

    def test_it_masks_a_postgres_password(self):
        out = self._redact(
            "could not connect: postgresql+asyncpg://decibyl:hunter2@db.example:5432/app\n"
        )
        assert "hunter2" not in out
        assert ":***@" in out
        # The parts an operator needs to diagnose with must survive.
        assert "db.example:5432/app" in out

    def test_it_masks_more_than_one_on_a_line(self):
        out = self._redact("tried rediss://u:p1@cache and postgres://v:p2@db\n")
        assert "p1" not in out and "p2" not in out

    def test_it_leaves_an_ordinary_url_alone(self):
        out = self._redact("GET https://app.decibyl.ai/api/v1/health\n")
        assert out.strip() == "GET https://app.decibyl.ai/api/v1/health"

    def test_it_leaves_a_line_with_no_url_alone(self):
        out = self._redact("Traceback (most recent call last):\n")
        assert out.strip() == "Traceback (most recent call last):"
