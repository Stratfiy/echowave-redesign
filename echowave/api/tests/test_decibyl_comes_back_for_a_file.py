"""A file that is still being read is answered later, not promised at.

Decibyl said "it's still being read -- I'll let you know as soon as it's
ready" and then never came back: nothing re-read the document and nothing
re-ran the turn. The person had attached a file, asked a question about
it, and got a promise and silence.
"""

from unittest.mock import AsyncMock, patch

import pytest

from api.services.workflow import decibyl
from api.tasks import routines


class _Document:
    def __init__(self, full_text=""):
        self.full_text = full_text


ATTACHED = [{"document_uuid": "u-1", "filename": "Proposal.docx"}]


class TestUnread:
    @pytest.mark.asyncio
    async def test_a_document_with_no_text_yet_is_pending(self):
        with patch.object(
            decibyl.db_client,
            "get_document_by_uuid",
            AsyncMock(return_value=_Document()),
        ):
            assert await decibyl.unread(1, ATTACHED) == ["Proposal.docx"]

    @pytest.mark.asyncio
    async def test_a_document_that_has_been_read_is_not(self):
        with patch.object(
            decibyl.db_client,
            "get_document_by_uuid",
            AsyncMock(return_value=_Document("the text")),
        ):
            assert await decibyl.unread(1, ATTACHED) == []

    @pytest.mark.asyncio
    async def test_a_document_that_is_gone_is_not_pending(self):
        """It will never arrive, and retrying to the cap would spend an
        account's credits on nothing."""
        with patch.object(
            decibyl.db_client, "get_document_by_uuid", AsyncMock(return_value=None)
        ):
            assert await decibyl.unread(1, ATTACHED) == []


class TestTheTurnComesBack:
    @pytest.mark.asyncio
    async def test_the_first_turn_answers_and_queues_a_re_read(self):
        """Silence while a file is read is worse than an answer that says
        it is reading."""
        with (
            patch.object(decibyl, "unread", AsyncMock(return_value=["Proposal.docx"])),
            patch.object(
                decibyl, "answer", AsyncMock(return_value="reading it")
            ) as answer,
            patch("api.tasks.arq.enqueue_job", AsyncMock()) as queue,
        ):
            await routines.answer_decibyl_message(
                None, 1, "what does it say?", attachments=ATTACHED
            )
        answer.assert_awaited_once()
        assert queue.await_args.kwargs["attempt"] == 1

    @pytest.mark.asyncio
    async def test_a_later_turn_stays_quiet_until_there_is_something_to_say(self):
        with (
            patch.object(decibyl, "unread", AsyncMock(return_value=["Proposal.docx"])),
            patch.object(decibyl, "answer", AsyncMock()) as answer,
            patch("api.tasks.arq.enqueue_job", AsyncMock()),
        ):
            await routines.answer_decibyl_message(
                None, 1, "what does it say?", attachments=ATTACHED, attempt=1
            )
        answer.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_it_answers_once_the_text_has_landed(self):
        with (
            patch.object(decibyl, "unread", AsyncMock(return_value=[])),
            patch.object(
                decibyl, "answer", AsyncMock(return_value="it says this")
            ) as answer,
            patch("api.tasks.arq.enqueue_job", AsyncMock()) as queue,
        ):
            await routines.answer_decibyl_message(
                None, 1, "what does it say?", attachments=ATTACHED, attempt=2
            )
        answer.assert_awaited_once()
        queue.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_it_gives_up_at_the_cap_rather_than_forever(self):
        with (
            patch.object(decibyl, "unread", AsyncMock(return_value=["Proposal.docx"])),
            patch.object(
                decibyl, "answer", AsyncMock(return_value="could not read it")
            ) as answer,
            patch("api.tasks.arq.enqueue_job", AsyncMock()) as queue,
        ):
            await routines.answer_decibyl_message(
                None,
                1,
                "what does it say?",
                attachments=ATTACHED,
                attempt=decibyl.UNREAD_RETRIES,
            )
        queue.assert_not_awaited()
        answer.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_a_queue_that_refuses_still_answers(self):
        with (
            patch.object(decibyl, "unread", AsyncMock(return_value=["Proposal.docx"])),
            patch.object(
                decibyl, "answer", AsyncMock(return_value="reading it")
            ) as answer,
            patch(
                "api.tasks.arq.enqueue_job",
                AsyncMock(side_effect=RuntimeError("no queue")),
            ),
        ):
            await routines.answer_decibyl_message(
                None, 1, "what does it say?", attachments=ATTACHED, attempt=1
            )
        answer.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_a_line_with_no_file_is_untouched(self):
        with (
            patch.object(decibyl, "unread", AsyncMock(return_value=[])),
            patch.object(decibyl, "answer", AsyncMock(return_value="hello")) as answer,
            patch("api.tasks.arq.enqueue_job", AsyncMock()) as queue,
        ):
            await routines.answer_decibyl_message(None, 1, "hello")
        answer.assert_awaited_once()
        queue.assert_not_awaited()
