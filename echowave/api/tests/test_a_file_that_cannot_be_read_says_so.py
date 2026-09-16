"""A file Decibyl cannot read is said, not promised about.

The founder attached a proposal and was told, twice in the same thread,
that it was "still being read" -- once on the first turn and once minutes
later. It never arrived. Two separate things made that sentence a lie.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from api.services.workflow import decibyl


def _doc(*, full_text=None, status="completed", error=None):
    return MagicMock(
        full_text=full_text, processing_status=status, processing_error=error
    )


class TestAFailedIngestionIsNotPending:
    @pytest.mark.asyncio
    async def test_a_failed_document_is_not_waited_for(self):
        # "Pending" meant "has no text", so a document the pipeline had
        # given up on looked exactly like one still in the queue -- and the
        # turn retried until the cap, then said "still being read" anyway.
        with patch.object(
            decibyl.db_client,
            "get_document_by_uuid",
            AsyncMock(return_value=_doc(status="failed")),
        ):
            pending = await decibyl.unread(
                7, [{"document_uuid": "u", "filename": "Proposal.docx"}]
            )
        assert pending == []

    @pytest.mark.asyncio
    async def test_a_document_still_processing_is_pending(self):
        with patch.object(
            decibyl.db_client,
            "get_document_by_uuid",
            AsyncMock(return_value=_doc(status="processing")),
        ):
            pending = await decibyl.unread(
                7, [{"document_uuid": "u", "filename": "Proposal.docx"}]
            )
        assert pending == ["Proposal.docx"]

    @pytest.mark.asyncio
    async def test_a_document_with_text_is_not_pending(self):
        with patch.object(
            decibyl.db_client,
            "get_document_by_uuid",
            AsyncMock(return_value=_doc(full_text="A 5 kg parcel costs 320")),
        ):
            pending = await decibyl.unread(
                7, [{"document_uuid": "u", "filename": "Prices.pdf"}]
            )
        assert pending == []


class TestTheBlockSaysWhichSituationItIs:
    def test_a_failed_file_is_never_described_as_still_being_read(self):
        said = decibyl._why_there_is_no_text(
            _doc(status="failed", error="Unsupported encoding"), last_try=False
        )
        assert "could not be read" in said
        assert "Unsupported encoding" in said
        assert "still reading" in said or "do NOT" in said
        assert "still being read" not in said

    def test_the_last_try_does_not_promise_to_come_back(self):
        # Nothing re-runs the turn after the cap. A promise made on the way
        # out is the one nobody keeps -- and the founder got it twice.
        said = decibyl._why_there_is_no_text(_doc(status="processing"), last_try=True)
        assert "will not run again" in said
        assert "still being read" not in said

    def test_an_ordinary_wait_still_promises_because_it_is_true(self):
        said = decibyl._why_there_is_no_text(_doc(status="processing"), last_try=False)
        assert "still being read" in said
        assert "runs again by itself" in said

    def test_the_failure_reason_is_clipped_not_dumped(self):
        said = decibyl._why_there_is_no_text(
            _doc(status="failed", error="x" * 500), last_try=False
        )
        assert "x" * 200 in said
        assert "x" * 201 not in said

    def test_a_file_that_is_gone_is_not_described_as_being_read(self):
        # The status is read off the document, so this branch has to come
        # after the None check or it reports a missing file as pending.
        said = decibyl._why_there_is_no_text(None, last_try=False)
        assert "not in the workspace" in said
        assert "still being read" not in said

    def test_a_failure_with_no_reason_still_says_it_failed(self):
        said = decibyl._why_there_is_no_text(_doc(status="failed"), last_try=False)
        assert "could not be read" in said
        assert "The reason given" not in said
