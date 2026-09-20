"""The nightly sweep must delete something it has not deleted before.

``purge_expired`` takes the oldest ``limit`` rows that are not fully purged and
works through them. The candidate filter is "recording not purged OR transcript
not purged", which is correct as a description of unfinished work and wrong as
a description of work available *now*.

Recordings expire at 90 days and transcripts at 365. Between those two dates a
row has had its audio deleted and its transcript is not yet due, so it is
finished for the time being -- and it still matches the filter. It is also
older than every row that has not been purged at all, so it sorts first.

Once 500 runs are sitting in that window they fill the batch, and they fill it
again the next night, and every night after. Rows crossing day 90 behind them
are never reached. The audio the privacy notice promises to delete at 90 days
stays in the bucket indefinitely.

Nothing fails while this happens. ``purge_run`` on an already-purged row finds
no objects to delete and reports the row cleared, so the sweep logs "purged 500
runs" every night and the metric that would show the stall reports success. It
is the silent-absence shape from AGENTS.md with the deletion as the thing that
goes missing -- and here the absence is of a deletion, so what accumulates is
personal data we told a regulator we had erased.

The fix is to ask the query for rows with work due now, per that
organization's own policy, rather than filtering them out after they have
already spent a place in the batch.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from api.db.models import (
    DataRetentionPolicyModel,
    OrganizationModel,
    WorkflowModel,
    WorkflowRunModel,
)
from api.services.privacy import retention

NOW = datetime(2026, 6, 15, 12, tzinfo=UTC)


async def _org(session, slug: str) -> OrganizationModel:
    org = OrganizationModel(provider_id=f"org-{slug}", quota_decibyl_tokens=0)
    session.add(org)
    await session.flush()
    return org


async def _run(
    session,
    org_id: int,
    slug: str,
    *,
    age_days: int,
    recording_purged: bool = False,
    transcript_purged: bool = False,
) -> WorkflowRunModel:
    workflow = WorkflowModel(name=f"wf-{slug}", organization_id=org_id)
    session.add(workflow)
    await session.flush()
    run = WorkflowRunModel(
        name=f"run-{slug}",
        workflow_id=workflow.id,
        mode="plivo",
        created_at=NOW - timedelta(days=age_days),
        recording_url=(
            retention.PURGED_MARKER if recording_purged else f"{slug}-audio.wav"
        ),
        transcript_url=(
            retention.PURGED_MARKER if transcript_purged else f"{slug}-transcript.txt"
        ),
    )
    session.add(run)
    await session.flush()
    return run


class TestTheBatchIsNotFilledByRowsWithNothingToDo:
    async def test_a_row_between_the_two_windows_does_not_occupy_the_batch(
        self, db_session, async_session, monkeypatch
    ):
        """The stall, at batch size one.

        The old row's audio went at day 90 and its transcript is not due until
        365, so there is nothing to do for it tonight. The young row crossed 90
        yesterday and has audio to delete. With a batch of one, the old row
        used to take the slot every sweep and the young row's audio was never
        touched.
        """
        deleted: list[str] = []
        monkeypatch.setattr(retention, "_delete_objects", _recording_deletions(deleted))

        org = await _org(async_session, "stall")
        await _run(
            async_session,
            org.id,
            "between-windows",
            age_days=200,
            recording_purged=True,
        )
        await _run(async_session, org.id, "due-now", age_days=91)

        result = await retention.purge_expired(async_session, now=NOW, limit=1)

        assert deleted == ["due-now-audio.wav"], (
            "the sweep spent its batch on a row with no work due and never "
            "reached the recording it was supposed to delete"
        )
        assert result["runs_purged"] == 1

    async def test_five_hundred_idle_rows_do_not_hide_the_one_that_is_due(
        self, db_session, async_session, monkeypatch
    ):
        """The reported shape: a full batch of them, and one row behind."""
        deleted: list[str] = []
        monkeypatch.setattr(retention, "_delete_objects", _recording_deletions(deleted))

        org = await _org(async_session, "backlog")
        for index in range(12):
            await _run(
                async_session,
                org.id,
                f"idle-{index}",
                age_days=200 + index,
                recording_purged=True,
            )
        await _run(async_session, org.id, "due-now", age_days=91)

        await retention.purge_expired(async_session, now=NOW, limit=10)

        assert "due-now-audio.wav" in deleted

    async def test_a_sweep_that_purges_nothing_does_not_report_runs_purged(
        self, db_session, async_session, monkeypatch
    ):
        """The metric that hid it. purge_run on an already-purged row deletes
        no objects and reports the row cleared, so the nightly log said "purged
        500 runs, deleted 0 objects" while nothing happened."""
        deleted: list[str] = []
        monkeypatch.setattr(retention, "_delete_objects", _recording_deletions(deleted))

        org = await _org(async_session, "quiet")
        await _run(async_session, org.id, "idle", age_days=200, recording_purged=True)

        result = await retention.purge_expired(async_session, now=NOW, limit=500)

        assert deleted == []
        assert result["runs_purged"] == 0


class TestTheWorkItselfStillHappens:
    """A sweep that advances but deletes the wrong things is not an improvement."""

    async def test_audio_goes_at_the_recording_window(
        self, db_session, async_session, monkeypatch
    ):
        deleted: list[str] = []
        monkeypatch.setattr(retention, "_delete_objects", _recording_deletions(deleted))

        org = await _org(async_session, "audio")
        run = await _run(async_session, org.id, "expired", age_days=91)

        await retention.purge_expired(async_session, now=NOW, limit=500)

        assert deleted == ["expired-audio.wav"]
        assert run.recording_url == retention.PURGED_MARKER
        # Not yet 365 days old, so the transcript is untouched.
        assert run.transcript_url == "expired-transcript.txt"

    async def test_a_run_inside_its_recording_window_is_left_alone(
        self, db_session, async_session, monkeypatch
    ):
        deleted: list[str] = []
        monkeypatch.setattr(retention, "_delete_objects", _recording_deletions(deleted))

        org = await _org(async_session, "young")
        run = await _run(async_session, org.id, "young", age_days=30)

        await retention.purge_expired(async_session, now=NOW, limit=500)

        assert deleted == []
        assert run.recording_url == "young-audio.wav"

    async def test_the_transcript_goes_at_its_own_later_window(
        self, db_session, async_session, monkeypatch
    ):
        """The row whose audio went at 90 must still be collected at 365 --
        making the sweep skip idle rows must not make it skip them forever."""
        deleted: list[str] = []
        monkeypatch.setattr(retention, "_delete_objects", _recording_deletions(deleted))

        org = await _org(async_session, "transcript")
        run = await _run(
            async_session, org.id, "old", age_days=400, recording_purged=True
        )

        await retention.purge_expired(async_session, now=NOW, limit=500)

        assert deleted == ["old-transcript.txt"]
        assert run.transcript_url == retention.PURGED_MARKER

    async def test_an_organizations_own_policy_decides_when(
        self, db_session, async_session, monkeypatch
    ):
        """The windows are per organization, so the query cannot use one
        platform-wide number to decide what is due."""
        deleted: list[str] = []
        monkeypatch.setattr(retention, "_delete_objects", _recording_deletions(deleted))

        strict = await _org(async_session, "strict")
        async_session.add(
            DataRetentionPolicyModel(
                organization_id=strict.id,
                recording_retention_days=7,
                transcript_retention_days=30,
            )
        )
        await async_session.flush()
        await _run(async_session, strict.id, "strict-run", age_days=10)

        relaxed = await _org(async_session, "relaxed")
        async_session.add(
            DataRetentionPolicyModel(
                organization_id=relaxed.id,
                recording_retention_days=3650,
                transcript_retention_days=3650,
            )
        )
        await async_session.flush()
        await _run(async_session, relaxed.id, "relaxed-run", age_days=10)

        await retention.purge_expired(async_session, now=NOW, limit=500)

        assert deleted == ["strict-run-audio.wav"], (
            "a ten-day-old call was judged against the wrong organization's policy"
        )


def _recording_deletions(sink: list[str]):
    """Stand in for storage, recording which keys the sweep asked to delete.

    The sweep's own progress is what is under test, and a real bucket would
    make "which objects went" the hardest thing to assert about it.
    """

    async def _delete(keys):
        sink.extend(keys)
        return len(keys), []

    return _delete
