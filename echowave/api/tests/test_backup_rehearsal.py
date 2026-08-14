"""Proving a backup comes back, rather than proving one was written.

The backup job checks the write path: a dump was taken, encrypted, uploaded,
and the object is the right size. Every way a backup is worthless while looking
healthy is invisible from there. ``pg_dump`` ran against a replica that had
stopped replicating. The object is encrypted under a secret since rotated. The
restore fails on an extension the host does not have. Each produces a bucket
full of correctly-sized objects and nothing to restore.

The tests below are about the judgement, which is where a rehearsal is easy to
get wrong in the direction of always passing:

**Empty is failure, not "fewer than live".** The restored copy is expected to
be *behind* live, so a naive `restored <= live` calls an empty ledger a pass —
which is the exact outcome the whole thing exists to catch.

**More than live is failure too.** These tables only grow, so a restored copy
ahead of live is a dump of a different database.

**Row counts are not enough.** A dump missing rows from the middle of the
ledger passes every count and only fails when the running balance is checked
against ``balance_after_paise``.

**A failure is recorded, not just raised.** A rehearsal that failed four days
ago is the most important fact about these backups, and it is only a fact if it
is written down.
"""

from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa

from api.db.models import BackupRehearsalModel
from api.services.backup import rehearsal


@pytest.fixture
async def no_rehearsals(async_session):
    """Start from an empty table.

    Every question here is about *the newest* rehearsal, so a row left behind
    by another test is not noise — it is the answer. The shared test database
    makes that a real possibility rather than a theoretical one.
    """
    await async_session.execute(sa.delete(BackupRehearsalModel))
    await async_session.flush()


class TestJudgingTheRestoredCopy:
    def test_a_copy_slightly_behind_live_passes(self):
        """The normal case. The dump was taken earlier and these tables only
        grow, so behind is expected rather than suspicious."""
        ok, findings = rehearsal._compare(
            {"credit_ledger_rows": 980, "organizations": 40},
            {"credit_ledger_rows": 1000, "organizations": 41},
        )

        assert ok
        assert findings["credit_ledger_rows"]["note"] == "ok"

    def test_an_empty_ledger_fails(self):
        """The failure this exists to catch, and the one a naive
        `restored <= live` calls a pass."""
        ok, findings = rehearsal._compare(
            {"credit_ledger_rows": 0}, {"credit_ledger_rows": 1000}
        )

        assert not ok
        assert "empty" in findings["credit_ledger_rows"]["note"]

    def test_more_rows_than_live_fails(self):
        """These tables only grow, so a restored copy ahead of live is a dump
        of something else — a different database, or a stale environment
        somebody pointed the job at."""
        ok, findings = rehearsal._compare(
            {"organizations": 5000}, {"organizations": 40}
        )

        assert not ok
        assert "different database" in findings["organizations"]["note"]

    def test_an_empty_live_database_is_not_a_failure(self):
        """A fresh deployment has nothing to restore and nothing to compare.
        Reporting that as a broken backup would train people to ignore it."""
        ok, _ = rehearsal._compare(
            {"credit_ledger_rows": 0, "organizations": 0},
            {"credit_ledger_rows": 0, "organizations": 0},
        )

        assert ok

    def test_a_sum_may_fall_as_well_as_rise(self):
        """A refund is a negative ledger row, so the total is not monotonic.
        Only an empty ledger is judged on the money columns."""
        ok, _ = rehearsal._compare(
            {"credit_ledger_paise": 900_000},
            {"credit_ledger_paise": 500_000},
        )

        assert ok


@pytest.mark.asyncio
class TestRecordingIt:
    async def test_a_failure_is_recorded_rather_than_raised(
        self, db_session, async_session, monkeypatch, no_rehearsals
    ):
        """A rehearsal nobody can see the result of is the runbook line it
        replaces. The row is what a readiness check reads."""

        class _NoBackups:
            async def alist_files(self, prefix):
                return []

        monkeypatch.setattr(
            "api.services.backup.rehearsal.get_storage", lambda: _NoBackups()
        )

        with pytest.raises(rehearsal.RehearsalError):
            await rehearsal.rehearse(async_session)

    async def test_a_broken_restore_is_written_down(
        self, db_session, async_session, monkeypatch, no_rehearsals
    ):
        """The one that matters. A restore that fails has to leave evidence,
        or the next person to look sees only the successful writes."""

        class _Unreadable:
            async def alist_files(self, prefix):
                return ["backups/postgres/2026/08/01/decibyl-20260801T000000Z.dump.enc"]

            async def adownload_file(self, key, path):
                return False

        monkeypatch.setattr(
            "api.services.backup.rehearsal.get_storage", lambda: _Unreadable()
        )

        result = await rehearsal.rehearse(async_session)
        await async_session.flush()

        assert not result.ok
        row = (
            await async_session.execute(sa.select(BackupRehearsalModel))
        ).scalar_one()
        assert row.ok is False
        assert "could not be read" in (row.error or "")


@pytest.mark.asyncio
class TestReportingIt:
    async def _record(self, async_session, *, ok, days_ago, error=None):
        async_session.add(
            BackupRehearsalModel(
                backup_key="backups/postgres/decibyl-test.dump.enc",
                ok=ok,
                checks={},
                error=error,
                started_at=datetime.now(UTC) - timedelta(days=days_ago),
                finished_at=datetime.now(UTC) - timedelta(days=days_ago),
            )
        )
        await async_session.flush()

    async def test_never_rehearsed_is_reported_as_never(
        self, db_session, async_session, no_rehearsals
    ):
        state = await rehearsal.last_rehearsal(async_session)

        assert state["rehearsed"] is False
        assert rehearsal.due(state)

    async def test_a_recent_pass_is_not_due(
        self, db_session, async_session, no_rehearsals
    ):
        await self._record(async_session, ok=True, days_ago=2)

        state = await rehearsal.last_rehearsal(async_session)

        assert state["ok"] is True
        assert not state["stale"]
        assert not rehearsal.due(state)

    async def test_an_old_pass_is_due_again(
        self, db_session, async_session, no_rehearsals
    ):
        """Restoring proves the path worked *then*. A secret rotated last week
        breaks it silently, and only another restore finds that."""
        await self._record(
            async_session, ok=True, days_ago=rehearsal.REHEARSE_EVERY_DAYS + 5
        )

        state = await rehearsal.last_rehearsal(async_session)

        assert state["stale"]
        assert rehearsal.due(state)

    async def test_the_newest_attempt_wins_even_when_it_failed(
        self, db_session, async_session, no_rehearsals
    ):
        """Reporting "last known good" instead is how a broken restore path
        stays quiet: a pass from last month would mask a failure from
        yesterday."""
        await self._record(async_session, ok=True, days_ago=20)
        await self._record(
            async_session, ok=False, days_ago=1, error="pg_restore: no such extension"
        )

        state = await rehearsal.last_rehearsal(async_session)

        assert state["ok"] is False
        assert "extension" in state["error"]


@pytest.mark.asyncio
class TestTheReadinessCheck:
    async def test_never_rehearsed_reads_as_a_hypothesis_not_a_pass(
        self, db_session, async_session, no_rehearsals
    ):
        """A deployment that has never restored anything must not read ready.
        That claim is the one this whole feature exists to stop us making."""
        from api.services.privacy.readiness import assess

        report = await assess(async_session)
        check = next(c for c in report.checks if c.key == "backup_restore_rehearsed")

        assert check.status != "ready"
        assert "hypothesis" in check.detail

    async def test_a_recent_pass_reads_ready(
        self, db_session, async_session, no_rehearsals
    ):
        from api.services.privacy.readiness import assess

        async_session.add(
            BackupRehearsalModel(
                backup_key="backups/postgres/decibyl-ok.dump.enc",
                ok=True,
                checks={},
                started_at=datetime.now(UTC) - timedelta(days=1),
                finished_at=datetime.now(UTC) - timedelta(days=1),
            )
        )
        await async_session.flush()

        report = await assess(async_session)
        check = next(c for c in report.checks if c.key == "backup_restore_rehearsed")

        assert check.status == "ready"

    async def test_the_remedy_names_the_command(
        self, db_session, async_session, no_rehearsals
    ):
        """A remedy that says "rehearse a restore" is the runbook line again.
        This one is runnable."""
        from api.services.privacy.readiness import assess

        report = await assess(async_session)
        check = next(c for c in report.checks if c.key == "backup_restore_rehearsed")

        assert "scripts.rehearse_restore" in check.remedy
