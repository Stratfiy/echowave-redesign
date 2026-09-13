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


class TestTheDeployShipsTheCommitItWasAskedFor:
    """The box deployed a commit 243 behind main, on every run, for months.

    The checkout read::

        git checkout --detach "origin/$REF" 2>/dev/null || git checkout --detach "$REF"

    `2>/dev/null` hid why the first form failed, and the fallback checked out
    the *local* branch of that name — which nobody on that box has ever
    updated, because the deploy only ever detaches. It sat at 8d9c9c5, older
    than the rollback target. The api then exited 255 against a database 243
    commits ahead of the code, and the failure read as a bad build.
    """

    def test_it_checks_out_what_it_just_fetched(self):
        assert "FETCH_HEAD" in _source()

    def test_it_no_longer_falls_back_to_a_local_branch(self):
        # The fallback is the whole bug: a name that resolves locally and is
        # never updated will always resolve, and always to the wrong thing.
        #
        # Comments are skipped deliberately — the note above the fix quotes the
        # old line, and a test that cannot tell an explanation from an
        # instruction would force the explanation to be deleted.
        code = [
            line for line in _source().splitlines() if not line.strip().startswith("#")
        ]
        for line in code:
            assert 'checkout --detach "$REF"' not in line

    def test_it_does_not_swallow_the_checkout_error(self):
        checkout_lines = [
            line
            for line in _source().splitlines()
            if "checkout --detach" in line and not line.strip().startswith("#")
        ]
        assert checkout_lines, "the checkout disappeared"
        for line in checkout_lines:
            assert "2>/dev/null" not in line

    def test_it_refuses_to_deploy_a_different_commit_than_asked_for(self):
        # Shipping the wrong commit quietly is worse than stopping.
        source = _source()
        assert 'if [ "$NEW_SHA" != "$TARGET_SHA" ]' in source
        assert "REFUSING TO DEPLOY" in source


class TestTheWorkflowRunsTheNewScript:
    """The loop that stopped every fix to the deploy from ever running."""

    WORKFLOW = SCRIPT.parents[2] / ".github" / "workflows" / "deploy.yml"

    def _workflow(self) -> str:
        return self.WORKFLOW.read_text()

    def test_it_reads_the_script_out_of_the_fetched_commit(self):
        # Not `cp`: a copy is of the file already on disk, which after a
        # rollback is the old one again — so the fix never arrives.
        assert "show FETCH_HEAD:echowave/scripts/ci_deploy.sh" in self._workflow()

    def test_it_does_not_check_out_before_running_the_script(self):
        # Ordering is load-bearing. ci_deploy.sh captures PREVIOUS_SHA from the
        # tree before it checks anything out; checking out in the workflow
        # first would make the rollback target the commit being deployed.
        commands = self._workflow().split("{commands:", 1)[1].split("]}", 1)[0]
        assert "checkout" not in commands

    def test_it_still_runs_the_script_from_tmp(self):
        assert "bash /tmp/ci_deploy.running.sh" in self._workflow()


class TestAnUntrackedFileDoesNotStopTheDeploy:
    """The next thing that was in the way, once the deploy stopped lying.

    Run 253 -- the first deploy on the FETCH_HEAD fix -- got eleven seconds in
    and said::

        error: The following untracked working tree files would be
        overwritten by checkout:
            echowave/scripts/cutover_to_managed_postgres.sh

    Which is git being right. A script was written onto the box by hand during
    this morning's RDS cutover and committed to the repo afterwards, so from
    the moment that commit landed, every checkout aborted on a file whose
    tracked version is the one everybody wants. Nothing about it is rare: it
    is what happens after any incident somebody scripted their way out of.
    """

    def test_the_collision_is_handled_rather_than_hit(self):
        source = _source()
        assert "checkout --detach" in source
        assert ".deploy-displaced" in source

    def test_the_displaced_file_is_moved_and_not_deleted(self):
        """The box's copy may be the only record of what was actually run
        during an incident. A deploy does not get to delete that."""
        source = _source()
        assert "mv " in source
        code = [
            line for line in source.splitlines() if not line.strip().startswith("#")
        ]
        for line in code:
            # `git clean -fd` would take the cutover dump directories with it.
            assert "git clean" not in line
            assert "rm -rf" not in line

    def test_it_parses_the_paths_out_of_a_real_git_refusal(self):
        """The load-bearing half, run rather than read.

        git prints the offending paths tab-indented under a sentence. If that
        extraction is wrong the script moves nothing, the second checkout
        fails exactly as the first did, and the deploy is back where it
        started -- with a log that now claims it handled the case.
        """
        import re
        import subprocess
        import tempfile
        from pathlib import Path as _Path

        extractor = re.search(r"\| *(sed -n [^\n]*p')", _source())
        assert extractor, "the collision extractor is no longer a sed expression"

        with tempfile.TemporaryDirectory() as tmp:
            repo = _Path(tmp)
            run = lambda *a: subprocess.run(  # noqa: E731
                a, cwd=repo, capture_output=True, text=True
            )
            run("git", "init", "-q", "-b", "main")
            run("git", "config", "user.email", "t@t")
            run("git", "config", "user.name", "t")
            (repo / "keep.txt").write_text("base\n")
            run("git", "add", "-A")
            run("git", "commit", "-qm", "base")
            base = run("git", "rev-parse", "HEAD").stdout.strip()

            # A commit that ships the file, exactly like the cutover script.
            (repo / "scripts").mkdir()
            (repo / "scripts" / "cutover.sh").write_text("tracked\n")
            run("git", "add", "-A")
            run("git", "commit", "-qm", "ships the script")
            target = run("git", "rev-parse", "HEAD").stdout.strip()

            # Back to the base commit, then put an untracked copy in its place
            # -- the state the box was actually in.
            run("git", "checkout", "-q", "--detach", base)
            (repo / "scripts").mkdir(exist_ok=True)
            (repo / "scripts" / "cutover.sh").write_text("written by hand\n")

            collisions = subprocess.run(
                f'git checkout --detach "{target}" 2>&1 >/dev/null | '
                + extractor.group(1),
                cwd=repo,
                shell=True,
                capture_output=True,
                text=True,
            ).stdout.split()

            assert collisions == ["scripts/cutover.sh"], collisions
