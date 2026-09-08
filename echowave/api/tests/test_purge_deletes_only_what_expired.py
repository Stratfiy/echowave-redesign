"""An audio purge must not take the transcript with it.

Recordings and transcripts have separate retention windows, and the recording
one is shorter — having two windows is the entire point. So the ordinary sweep
is a run whose audio has expired and whose transcript has not, and on that sweep
`purge_run` was deleting every object belonging to the run.

Two things went wrong at once, and the second hid the first: the transcript
object was destroyed years before its own window closed, and `transcript_url`
was left pointing at it, so the row still advertised a transcript that storage
no longer had. Deletion that reports success is the failure mode this whole
module is written to avoid, and it was doing it.

Nothing covered this, which is why it survived: the existing purge tests all run
the case where both windows have expired, where deleting everything is correct.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from api.services.privacy.retention import PURGED_MARKER, _storage_keys, purge_run

RECORDING_KEY = "recordings/run-1/mixed.wav"
TRANSCRIPT_KEY = "transcripts/run-1.json"


class _Run:
    """The two legacy columns are bare storage keys on older rows, which is the
    shape that made both reachable from one helper in the first place."""

    def __init__(self):
        self.id = 1
        self.recording_url = RECORDING_KEY
        self.transcript_url = TRANSCRIPT_KEY
        self.extra = {}
        self.gathered_context = {"name": "Asha"}
        self.initial_context = {}
        self.logs = {}
        self.annotations = {}


class TestWhichObjectsAPurgeMayDelete:
    def test_an_audio_only_purge_leaves_the_transcript_object_alone(self):
        """The bug, stated as the assertion that would have caught it."""
        keys = _storage_keys(_Run(), include_transcript=False)
        assert RECORDING_KEY in keys
        assert TRANSCRIPT_KEY not in keys, (
            "the transcript object was queued for deletion on a sweep that was "
            "only entitled to the recording"
        )

    def test_a_full_purge_takes_both(self):
        keys = _storage_keys(_Run(), include_transcript=True)
        assert RECORDING_KEY in keys
        assert TRANSCRIPT_KEY in keys

    def test_a_purged_marker_is_never_treated_as_a_key(self):
        """Otherwise a second sweep asks storage to delete an object named
        after the marker, and counts the miss as a failure forever."""
        run = _Run()
        run.recording_url = PURGED_MARKER
        run.transcript_url = PURGED_MARKER
        assert _storage_keys(run, include_transcript=True) == []

    def test_an_http_url_is_not_a_storage_key(self):
        run = _Run()
        run.recording_url = "https://cdn.example.com/a.wav"
        assert _storage_keys(run, include_transcript=False) == []


@pytest.mark.asyncio
class TestWhatPurgeRunActuallyDeletes:
    async def _run_purge(self, *, drop_transcript: bool):
        run = _Run()
        session = AsyncMock()
        with patch(
            "api.services.privacy.retention._delete_objects",
            new=AsyncMock(return_value=(1, [])),
        ) as deleter:
            await purge_run(session, run=run, drop_transcript=drop_transcript)
        return run, deleter.call_args.args[0]

    async def test_the_transcript_object_survives_an_audio_only_purge(self):
        run, deleted_keys = await self._run_purge(drop_transcript=False)
        assert TRANSCRIPT_KEY not in deleted_keys
        # And the row still points at it, which is only honest because the
        # object is still there.
        assert run.transcript_url == TRANSCRIPT_KEY
        assert run.recording_url == PURGED_MARKER

    async def test_a_full_purge_removes_the_transcript_and_clears_the_pointer(self):
        run, deleted_keys = await self._run_purge(drop_transcript=True)
        assert TRANSCRIPT_KEY in deleted_keys
        assert run.transcript_url == PURGED_MARKER
        assert run.gathered_context == {}

    async def test_the_pointer_and_the_object_never_disagree(self):
        """The invariant behind both cases above: a row claims a transcript if
        and only if the purge left the object in place."""
        for drop in (False, True):
            run, deleted_keys = await self._run_purge(drop_transcript=drop)
            claims_transcript = run.transcript_url != PURGED_MARKER
            object_survived = TRANSCRIPT_KEY not in deleted_keys
            assert claims_transcript == object_survived
