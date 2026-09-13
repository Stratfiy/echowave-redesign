"""A channel's context compacts; it does not drop.

The first cut of channel context took the newest thirty rows and discarded
the rest. That is the wrong direction for a room where somebody said "it's
500, not 400" six weeks ago and nobody has said otherwise since: the
correction is the thing that matters, and it was the first to go.

So the old end folds into a précis, the way Claude Code's own context does and
the way ``qa/analysis.py`` already does per node. These tests guard the
invariant that makes that safe -- **every row is either in the summary or in
the window, never both and never neither** -- and the two costs that make it
affordable: the fold runs *after* a reply as its own job, and it borrows the
answering run's model rather than needing one of its own.
"""

from types import SimpleNamespace as N
from unittest.mock import AsyncMock, patch

import pytest

from api.services.workflow import channel_context as cc

NAMES = {9: "Supplier chaser"}


def _row(i: int, body: str = "", actor: str = "human", workflow_id=None, summary=""):
    return N(
        id=i,
        actor=actor,
        workflow_id=workflow_id,
        summary=summary,
        payload={"body": body} if body else {},
    )


class TestRendering:
    def test_a_person_and_a_named_bot(self):
        rows = [
            _row(2, actor="agent", workflow_id=9, summary="Confirmed the shipment"),
            _row(1, "what did the supplier quote?"),
        ]
        out = cc.render(rows, NAMES)
        assert "Someone: what did the supplier quote?" in out
        assert "Supplier chaser: Confirmed the shipment" in out
        # Oldest first: a conversation reads forwards.
        assert out.index("Someone:") < out.index("Supplier chaser:")

    def test_the_summary_comes_before_the_window(self):
        out = cc.render(
            [_row(5, "latest")], NAMES, summary="Quoted 400; corrected to 500."
        )
        assert out.index("in summary:") < out.index("Recent messages")
        assert "Quoted 400; corrected to 500." in out

    def test_a_summary_with_no_window_still_renders(self):
        # A quiet channel whose history was folded long ago: the précis is the
        # whole of what the bot should know.
        assert "Old news." in cc.render([], NAMES, summary="Old news.")

    def test_nothing_at_all_is_none(self):
        assert cc.render([], NAMES) is None
        assert cc.render([], NAMES, summary="   ") is None

    def test_overflow_with_no_summary_says_so(self):
        """The one state in which something is genuinely not shown: the fold
        has not run yet. Said out loud so the bot knows it came in late."""
        rows = [_row(i, f"msg {i}") for i in range(cc.MAX_EVENTS, 0, -1)]
        assert cc.TRUNCATED_NOTE in cc.render(rows, NAMES)

    def test_overflow_with_a_summary_does_not_claim_anything_is_missing(self):
        rows = [_row(i, f"msg {i}") for i in range(cc.MAX_EVENTS, 0, -1)]
        assert cc.TRUNCATED_NOTE not in cc.render(rows, NAMES, summary="covered")

    def test_a_bot_the_roster_no_longer_knows_is_still_attributed(self):
        out = cc.render(
            [_row(1, actor="agent", workflow_id=42, summary="did x")], NAMES
        )
        assert "Another bot: did x" in out

    def test_a_pasted_document_is_cut_to_its_gist(self):
        out = cc.render([_row(1, "x" * (cc.MAX_LINE * 3))], NAMES)
        line = [l for l in out.splitlines() if l.startswith("Someone:")][0]
        assert len(line) <= len("Someone: ") + cc.MAX_LINE


class TestReadingTheThread:
    @pytest.mark.asyncio
    async def test_it_reads_only_above_the_watermark(self):
        """The invariant. Rows at or below the watermark are in the summary
        and must not also appear verbatim."""
        folder = N(context_summary="covered", context_summarised_through=40)
        with patch("api.services.workflow.channel_context.db_client") as db:
            db.get_folder = AsyncMock(return_value=folder)
            db.agent_events = AsyncMock(return_value=[_row(41, "new")])
            db.get_all_workflows_for_listing = AsyncMock(return_value=[])
            out = await cc.recent_thread(organization_id=1, folder_id=3)

        assert db.agent_events.await_args.kwargs["after_id"] == 40
        assert "covered" in out and "Someone: new" in out

    @pytest.mark.asyncio
    async def test_a_channel_that_never_overflowed_reads_as_before(self):
        folder = N(context_summary=None, context_summarised_through=None)
        with patch("api.services.workflow.channel_context.db_client") as db:
            db.get_folder = AsyncMock(return_value=folder)
            db.agent_events = AsyncMock(return_value=[_row(1, "hi")])
            db.get_all_workflows_for_listing = AsyncMock(return_value=[])
            out = await cc.recent_thread(organization_id=1, folder_id=3)
        assert db.agent_events.await_args.kwargs["after_id"] is None
        assert "in summary" not in out

    @pytest.mark.asyncio
    async def test_a_failure_to_read_is_no_context_not_no_reply(self):
        with patch("api.services.workflow.channel_context.db_client") as db:
            db.get_folder = AsyncMock(side_effect=RuntimeError("down"))
            assert await cc.recent_thread(organization_id=1, folder_id=3) is None


class TestFolding:
    def _folder(self, through=None, summary=None):
        return N(context_summary=summary, context_summarised_through=through)

    @pytest.mark.asyncio
    async def test_nothing_folds_until_enough_has_accumulated(self):
        """A model call is not spent tidying a channel that ticks over one
        message a day."""
        rows = [_row(i, f"m{i}") for i in range(cc.COMPACT_AFTER - 1, 0, -1)]
        with patch("api.services.workflow.channel_context.db_client") as db:
            db.get_folder = AsyncMock(return_value=self._folder())
            db.agent_events = AsyncMock(return_value=rows)
            db.set_folder_context_summary = AsyncMock()
            assert await cc.compact(organization_id=1, folder_id=3, run_id=9) is False
        db.set_folder_context_summary.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_the_oldest_batch_folds_and_the_watermark_is_its_last_id(self):
        """Newest-first in, oldest-first batch out, and the watermark lands on
        the last row folded -- so the next read starts exactly one past it."""
        total = cc.COMPACT_AFTER + 5
        rows = [_row(i, f"m{i}") for i in range(total, 0, -1)]
        with (
            patch("api.services.workflow.channel_context.db_client") as db,
            patch(
                "api.services.workflow.channel_context._fold",
                AsyncMock(return_value="folded précis"),
            ) as fold,
        ):
            db.get_folder = AsyncMock(return_value=self._folder(summary="old"))
            db.agent_events = AsyncMock(return_value=rows)
            db.get_all_workflows_for_listing = AsyncMock(return_value=[])
            db.set_folder_context_summary = AsyncMock(return_value=True)
            assert await cc.compact(organization_id=1, folder_id=3, run_id=9) is True

        # The batch handed to the model is the oldest COMPACT_BATCH rows, with
        # the previous précis folded in rather than replaced.
        kwargs = fold.await_args.kwargs
        assert kwargs["previous"] == "old"
        assert kwargs["transcript"].splitlines()[0].endswith("m1")
        assert len(kwargs["transcript"].splitlines()) == cc.COMPACT_BATCH

        written = db.set_folder_context_summary.await_args.kwargs
        assert written["summary"] == "folded précis"
        assert written["summarised_through"] == cc.COMPACT_BATCH

    @pytest.mark.asyncio
    async def test_a_batch_with_nothing_renderable_still_advances(self):
        """Or it blocks every later fold forever."""
        rows = [_row(i) for i in range(cc.COMPACT_AFTER + 1, 0, -1)]  # no bodies
        with (
            patch("api.services.workflow.channel_context.db_client") as db,
            patch("api.services.workflow.channel_context._fold", AsyncMock()) as fold,
        ):
            db.get_folder = AsyncMock(return_value=self._folder(summary="keep"))
            db.agent_events = AsyncMock(return_value=rows)
            db.get_all_workflows_for_listing = AsyncMock(return_value=[])
            db.set_folder_context_summary = AsyncMock(return_value=True)
            assert await cc.compact(organization_id=1, folder_id=3, run_id=9) is True
        fold.assert_not_awaited()
        written = db.set_folder_context_summary.await_args.kwargs
        assert written["summary"] == "keep"
        assert written["summarised_through"] == cc.COMPACT_BATCH

    @pytest.mark.asyncio
    async def test_a_failed_fold_moves_nothing(self):
        """A fold that fails leaves the channel exactly as it was: rows stay
        above the watermark, still shown verbatim, still there to fold later."""
        rows = [_row(i, f"m{i}") for i in range(cc.COMPACT_AFTER + 1, 0, -1)]
        with (
            patch("api.services.workflow.channel_context.db_client") as db,
            patch(
                "api.services.workflow.channel_context._fold",
                AsyncMock(side_effect=RuntimeError("model down")),
            ),
        ):
            db.get_folder = AsyncMock(return_value=self._folder())
            db.agent_events = AsyncMock(return_value=rows)
            db.get_all_workflows_for_listing = AsyncMock(return_value=[])
            db.set_folder_context_summary = AsyncMock()
            assert await cc.compact(organization_id=1, folder_id=3, run_id=9) is False
        db.set_folder_context_summary.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_an_empty_summary_from_the_model_moves_nothing(self):
        rows = [_row(i, f"m{i}") for i in range(cc.COMPACT_AFTER + 1, 0, -1)]
        with (
            patch("api.services.workflow.channel_context.db_client") as db,
            patch(
                "api.services.workflow.channel_context._fold",
                AsyncMock(return_value=""),
            ),
        ):
            db.get_folder = AsyncMock(return_value=self._folder())
            db.agent_events = AsyncMock(return_value=rows)
            db.get_all_workflows_for_listing = AsyncMock(return_value=[])
            db.set_folder_context_summary = AsyncMock()
            assert await cc.compact(organization_id=1, folder_id=3, run_id=9) is False
        db.set_folder_context_summary.assert_not_awaited()


class TestTheReplyCarriesTheThread:
    @pytest.mark.asyncio
    async def test_the_question_goes_last(self):
        """The thread is prepended to the question in one message, and the
        question is the most recent thing in the window -- not a separate turn
        the bot would answer instead."""
        from api.services.workflow import channel_reply

        captured = {}

        async def fake_append(*, run_id, text_session, user_text, expected_revision):
            captured["text"] = user_text
            return text_session

        session = N(
            revision=1, session_data={"turns": [{"role": "assistant", "content": "ok"}]}
        )
        with (
            patch.object(
                channel_reply.db_client,
                "get_workflow_by_id",
                AsyncMock(return_value=N(organization_id=1, name="Bot")),
            ),
            patch.object(
                channel_reply.db_client,
                "create_workflow_run",
                AsyncMock(return_value=N(id=77)),
            ),
            patch.object(
                channel_reply,
                "authorize_workflow_run_start",
                AsyncMock(return_value=N(has_quota=True, error_message=None)),
            ),
            patch.object(
                channel_reply.db_client,
                "ensure_workflow_run_text_session",
                AsyncMock(return_value=session),
            ),
            patch.object(
                channel_reply,
                "initialize_text_chat_session",
                AsyncMock(return_value=session),
            ),
            patch.object(
                channel_reply,
                "execute_pending_text_chat_turn",
                AsyncMock(return_value=session),
            ),
            patch.object(channel_reply, "append_text_chat_user_message", fake_append),
            patch.object(
                channel_reply.channel_context,
                "recent_thread",
                AsyncMock(return_value="THREAD"),
            ),
            patch.object(channel_reply.agent_timeline, "record", AsyncMock()),
            patch.object(channel_reply, "set_current_run_id", lambda _: None),
        ):
            await channel_reply.answer_in_channel(5, 3, "are we open?")

        assert captured["text"].startswith("THREAD")
        assert captured["text"].endswith("are we open?")
