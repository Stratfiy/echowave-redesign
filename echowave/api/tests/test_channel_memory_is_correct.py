"""A channel's memory folds every message, and loses no word that matters.

Two defects in channel compaction, both silent:

**A backlog was skipped.** The fold read its batch through the feed query --
newest first by ``(at, id)``, then limited to sixty -- reversed it and folded
the oldest twenty *of those*, then moved the id watermark past them. With 100
rows pending the read returned ids 100..41, the fold took 41..60, and ids 1..40
sat below the watermark unread: excluded from every later read, never in any
summary. The fold now has its own read, oldest first by ``id`` (the key the
watermark is on), and consumes a prefix of it.

**A long message was cut before the model saw it.** The fold built its input
with the window's preview line, 400 characters, so "a page of background, then
CORRECTION: the meeting is cancelled" reached the model as the background. It
now reads the whole message in bounded chunks, keeps every sentence carrying a
correction, cancellation, negation, figure, date or promise word for word with
its event id, and never slices a generated summary mid-sentence.

The model is a stub throughout: these are tests of what the fold *feeds* and
*keeps*, not of what a model writes.
"""

import asyncio
import random
import re
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest

from api.db.models import OrganizationModel
from api.services.workflow import channel_context as cc

_ID = re.compile(r"\[#(\d+)")


class StubModel:
    """Stands in for ``channel_context._fold``: records which event ids each
    call was handed and the transcript it read, and writes a short, lossy
    summary -- one line per call naming the ids, nothing of their content.
    A summary that keeps no detail is the worst case for the kept lines."""

    def __init__(self, reply=None):
        self.transcripts: list[str] = []
        self.seen: list[int] = []
        self._reply = reply

    async def __call__(self, *, previous, transcript, run_id, max_chars=None):
        self.transcripts.append(transcript)
        ids = []
        for found in _ID.findall(transcript):
            if int(found) not in ids:
                ids.append(int(found))
        if max_chars is None:
            self.seen.extend(i for i in ids if i not in self.seen)
        if self._reply is not None:
            return self._reply(previous, transcript, max_chars)
        lines = [l for l in previous.splitlines() if l.strip()][-6:]
        lines.append(f"Talked over #{ids[0]}..#{ids[-1]}." if ids else "Nothing.")
        return "\n".join(lines)


@pytest.fixture
async def channel(db_session, async_session):
    org = OrganizationModel(provider_id=f"memory-{datetime.now(UTC).timestamp()}")
    async_session.add(org)
    await async_session.flush()
    folder = await db_session.create_folder("Ops", org.id)
    return org.id, folder.id


async def _say(db, org_id, folder_id, body, *, at=None, **extra):
    return await db.record_agent_event(
        organization_id=org_id,
        folder_id=folder_id,
        kind="message",
        actor="human",
        summary=body[:500],
        payload={"body": body, **extra.pop("payload", {})},
        at=at,
        **extra,
    )


async def _chatter(db, org_id, folder_id, n, *, word="chatter"):
    # No digits, negations or dates: lines the kept-word net has no reason to
    # keep, so anything that survives in the précis survived on its own.
    letters = "abcdefghijklmnopqrstuvwxyz"
    return [
        await _say(
            db, org_id, folder_id, f"{word} {letters[i % 26]}{letters[i // 26 % 26]} ok"
        )
        for i in range(n)
    ]


async def _drain(org_id, folder_id, model, *, rounds=50):
    with patch.object(cc, "_fold", model):
        for _ in range(rounds):
            if not await cc.compact(
                organization_id=org_id, folder_id=folder_id, run_id=1
            ):
                return


async def _watermark(db, org_id, folder_id):
    folder = await db.get_folder(folder_id, organization_id=org_id)
    return folder.context_summarised_through, folder.context_summary


def _assert_every_covered_row_was_read(ids, through, model):
    """The invariant: every row at or below the watermark was handed to the
    model, once, in order -- and nothing above it was."""
    covered = [i for i in ids if i <= through]
    assert model.seen == covered
    assert set(ids) - set(covered) == {i for i in ids if i > through}


class TestTheWholeBacklogFolds:
    @pytest.mark.asyncio
    async def test_a_backlog_of_100_is_folded_from_its_oldest_row(
        self, db_session, channel
    ):
        org_id, folder_id = channel
        ids = await _chatter(db_session, org_id, folder_id, 100)
        model = StubModel()

        with patch.object(cc, "_fold", model):
            assert await cc.compact(
                organization_id=org_id, folder_id=folder_id, run_id=1
            )

        through, _ = await _watermark(db_session, org_id, folder_id)
        # The first fold starts at the very first row -- the read that used to
        # start at row 41 and leave 1..40 under the watermark unread.
        assert model.seen[: cc.COMPACT_BATCH] == ids[: cc.COMPACT_BATCH]
        _assert_every_covered_row_was_read(ids, through, model)
        # Folds until fewer than COMPACT_AFTER remain: 100 -> 20 left.
        assert through == ids[100 - cc.COMPACT_AFTER + cc.COMPACT_BATCH - 1]

        # And what is left is exactly what the window reads: nothing is in
        # neither, nothing is in both.
        window = await db_session.agent_events(
            organization_id=org_id, folder_id=folder_id, after_id=through, limit=500
        )
        assert sorted(e.id for e in window) == [i for i in ids if i > through]

    @pytest.mark.asyncio
    async def test_a_backlog_of_500_drains_with_nothing_skipped(
        self, db_session, channel
    ):
        org_id, folder_id = channel
        ids = await _chatter(db_session, org_id, folder_id, 500)
        model = StubModel()

        await _drain(org_id, folder_id, model)

        through, _ = await _watermark(db_session, org_id, folder_id)
        _assert_every_covered_row_was_read(ids, through, model)
        assert len([i for i in ids if i > through]) < cc.COMPACT_AFTER

    @pytest.mark.asyncio
    async def test_after_the_compactor_was_down_it_catches_up(
        self, db_session, channel
    ):
        """Messages keep arriving while no fold runs; when folds resume (one
        job per reply, a bounded number of folds each) the backlog drains from
        its oldest end and new messages keep landing above it."""
        org_id, folder_id = channel
        model = StubModel()
        ids = await _chatter(db_session, org_id, folder_id, 30)
        await _drain(org_id, folder_id, model)
        assert (await _watermark(db_session, org_id, folder_id))[0] is None

        # Down: 270 more arrive and nothing folds them.
        ids += await _chatter(db_session, org_id, folder_id, 270, word="later")

        # Back: each reply's job runs one compact, and talk continues.
        with patch.object(cc, "_fold", model):
            for _ in range(12):
                ids += await _chatter(db_session, org_id, folder_id, 5, word="live")
                await cc.compact(organization_id=org_id, folder_id=folder_id, run_id=1)

        through, _ = await _watermark(db_session, org_id, folder_id)
        _assert_every_covered_row_was_read(ids, through, model)
        assert len([i for i in ids if i > through]) < cc.COMPACT_AFTER

    @pytest.mark.asyncio
    async def test_timestamps_out_of_order_skip_nothing(self, db_session, channel):
        """Timestamps that disagree with insertion order (a caller-supplied
        ``at``, two workers' clocks) must not decide what is folded. The fold
        goes by id, the watermark's own key; the window still sorts by time."""
        org_id, folder_id = channel
        base = datetime.now(UTC)
        offsets = list(range(120))
        random.Random(7).shuffle(offsets)
        ids = []
        for n, offset in enumerate(offsets):
            ids.append(
                await _say(
                    db_session,
                    org_id,
                    folder_id,
                    f"note {chr(97 + n % 26)}{chr(97 + n // 26)}",
                    at=base - timedelta(minutes=offset),
                )
            )
        model = StubModel()

        await _drain(org_id, folder_id, model)

        through, _ = await _watermark(db_session, org_id, folder_id)
        _assert_every_covered_row_was_read(ids, through, model)
        window = await db_session.agent_events(
            organization_id=org_id, folder_id=folder_id, after_id=through, limit=500
        )
        assert sorted(e.id for e in window) == [i for i in ids if i > through]

    @pytest.mark.asyncio
    async def test_the_fold_reads_what_the_window_reads(self, db_session, channel):
        """Same population: a row the channel's context would never show
        (on request, off, private to one person) is passed over by both, and
        every row it would show is folded."""
        org_id, folder_id = channel
        await _chatter(db_session, org_id, folder_id, 3)
        await _say(db_session, org_id, folder_id, "transcript", visibility="on_request")
        await _say(db_session, org_id, folder_id, "hidden", visibility="off")
        await _say(db_session, org_id, folder_id, "mine", payload={"private_to": "7"})
        await _chatter(db_session, org_id, folder_id, 3, word="after")

        shown = await db_session.agent_events(
            organization_id=org_id, folder_id=folder_id, limit=500
        )
        folded = await db_session.channel_events_to_compact(
            organization_id=org_id, folder_id=folder_id, after_id=None, limit=500
        )
        assert sorted(e.id for e in shown) == [e.id for e in folded]
        assert len(folded) == 6


class TestConcurrentFolds:
    @pytest.mark.asyncio
    async def test_a_fold_that_lost_the_race_writes_nothing(self, db_session, channel):
        """Fold A reads the channel, then waits on its model. Fold B reads the
        same watermark, finishes, and moves on. A's summary was built from a
        précis B has since replaced; A must not land on top of it."""
        org_id, folder_id = channel
        ids = await _chatter(db_session, org_id, folder_id, 60)
        entered, release = asyncio.Event(), asyncio.Event()

        async def slow(*, previous, transcript, run_id, max_chars=None):
            entered.set()
            await release.wait()
            return "STALE summary from fold A"

        fast = StubModel()
        with patch.object(cc, "_fold", slow):
            a = asyncio.create_task(
                cc.compact(organization_id=org_id, folder_id=folder_id, run_id=1)
            )
            await entered.wait()
        with patch.object(cc, "_fold", fast):
            assert await cc.compact(
                organization_id=org_id, folder_id=folder_id, run_id=2
            )
        b_through, b_summary = await _watermark(db_session, org_id, folder_id)
        release.set()
        assert await a is False

        through, summary = await _watermark(db_session, org_id, folder_id)
        assert (through, summary) == (b_through, b_summary)
        assert "STALE" not in summary
        _assert_every_covered_row_was_read(ids, through, fast)

    @pytest.mark.asyncio
    async def test_a_larger_watermark_does_not_win_on_size_alone(
        self, db_session, channel
    ):
        """The old rule was "larger wins". A fold that started from an older
        watermark, with a stale précis, and happens to reach further, is still
        stale: the swap is on the watermark it started from."""
        org_id, folder_id = channel
        assert await db_session.set_folder_context_summary(
            folder_id,
            org_id,
            summary="winner",
            summarised_through=20,
            expected_through=None,
        )
        assert not await db_session.set_folder_context_summary(
            folder_id,
            org_id,
            summary="stale",
            summarised_through=40,
            expected_through=None,
        )
        assert not await db_session.set_folder_context_summary(
            folder_id,
            org_id,
            summary="backwards",
            summarised_through=10,
            expected_through=20,
        )
        assert await _watermark(db_session, org_id, folder_id) == (20, "winner")
        assert await db_session.set_folder_context_summary(
            folder_id,
            org_id,
            summary="next",
            summarised_through=40,
            expected_through=20,
        )


BACKGROUND = {
    "english": ("Background " * 60, "CORRECTION: meeting is cancelled."),
    "price": ("Background " * 60, "Correction: the price is ₹450, not ₹500."),
    "hindi": ("पृष्ठभूमि " * 60, "सुधार: कल की मीटिंग रद्द कर दी गई है।"),
    "tamil": ("பின்னணி " * 60, "திருத்தம்: நாளை கூட்டம் ரத்து செய்யப்பட்டது."),
    "hinglish": (
        "Bas background info hai " * 15,
        "Correction: rate 500 nahi, 450 hai, aur kal ki meeting cancel ho gayi.",
    ),
    "tanglish": (
        "Summa background solren " * 20,
        "Meeting cancel pannitom, naalai illai.",
    ),
}


class TestTheEndOfALongMessageSurvives:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("case", sorted(BACKGROUND))
    async def test_a_late_correction_survives_many_folds(
        self, db_session, channel, case
    ):
        org_id, folder_id = channel
        background, correction = BACKGROUND[case]
        body = background + correction
        assert len(body) > cc.MAX_LINE  # the length the preview used to cut at
        said = await _say(db_session, org_id, folder_id, body)
        await _chatter(db_session, org_id, folder_id, cc.COMPACT_AFTER - 1)
        model = StubModel()

        await _drain(org_id, folder_id, model)
        through, summary = await _watermark(db_session, org_id, folder_id)
        assert through >= said

        # The model was handed the whole message, end included.
        assert any(correction in t for t in model.transcripts)
        # And the précis keeps it word for word, with the id to find it by,
        # although the stub's narrative kept nothing of it.
        assert f"[#{said}]" in summary and correction in summary

        # Five more folds of talk, each rewriting the précis.
        for _ in range(5):
            await _chatter(db_session, org_id, folder_id, cc.COMPACT_BATCH, word="more")
            before = through
            await _drain(org_id, folder_id, model)
            through, summary = await _watermark(db_session, org_id, folder_id)
            assert through > before
            assert correction in summary

        # The original is still there, exactly as written.
        assert (
            await cc.source_text(
                organization_id=org_id, folder_id=folder_id, event_id=said
            )
            == body
        )

    @pytest.mark.asyncio
    async def test_the_window_preview_keeps_the_end_too(self):
        """Before it is folded, a long message is a line in the window. That
        line is a preview -- and its tail is where the correction is."""
        background, correction = BACKGROUND["english"]
        row = type("Row", (), {})()
        row.id, row.actor, row.workflow_id, row.summary = 1, "human", None, ""
        row.payload = {"body": background + correction}
        out = cc.render([row], {})
        line = [l for l in out.splitlines() if l.startswith("A teammate:")][0]
        assert len(line) <= len("A teammate: ") + cc.MAX_LINE
        assert line.endswith(correction)
        assert line.startswith("A teammate: Background")


class TestBoundedChunks:
    @pytest.mark.asyncio
    async def test_a_pasted_document_is_read_in_chunks(self, db_session, channel):
        org_id, folder_id = channel
        words = " ".join(f"para{chr(97 + i % 26)}" for i in range(6_000))
        late = "The supplier will deliver on Friday."
        body = words + " " + late + " " + words
        assert len(body) > cc.MAX_SOURCE_CHARS
        said = await _say(db_session, org_id, folder_id, body)
        await _chatter(db_session, org_id, folder_id, cc.COMPACT_AFTER - 1)
        model = StubModel()

        await _drain(org_id, folder_id, model)
        _, summary = await _watermark(db_session, org_id, folder_id)

        first = [t for t in model.transcripts if f"#{said}" in t]
        assert len(first) >= 2  # more than one call
        assert all(len(t) <= cc.FOLD_CHUNK_CHARS for t in model.transcripts)
        # Past what the model reads, the transcript says so in words...
        assert any("more characters not shown here" in t for t in first)
        # ...and the key lines come from the whole message regardless.
        assert late in summary

    @pytest.mark.asyncio
    async def test_a_batch_of_documents_folds_a_prefix_within_the_call_budget(
        self, db_session, channel
    ):
        org_id, folder_id = channel
        doc = " ".join(f"word{chr(97 + i % 26)}" for i in range(2_500))
        ids = [
            await _say(db_session, org_id, folder_id, doc)
            for _ in range(cc.COMPACT_AFTER)
        ]
        model = StubModel()

        with patch.object(cc, "MAX_FOLDS_PER_RUN", 1), patch.object(cc, "_fold", model):
            assert await cc.compact(
                organization_id=org_id, folder_id=folder_id, run_id=1
            )

        through, _ = await _watermark(db_session, org_id, folder_id)
        assert len(model.transcripts) <= cc.MAX_FOLD_CALLS
        assert ids[0] <= through < ids[cc.COMPACT_BATCH - 1]
        _assert_every_covered_row_was_read(ids, through, model)


class TestTheSummaryIsNeverSliced:
    @pytest.mark.asyncio
    async def test_too_long_is_asked_again_within_budget(self, db_session, channel):
        org_id, folder_id = channel
        await _chatter(db_session, org_id, folder_id, cc.COMPACT_AFTER)
        budgets = []

        def reply(previous, transcript, max_chars):
            budgets.append(max_chars)
            return "Short and whole." if max_chars else "Too long. " * 400

        with patch.object(cc, "MAX_FOLDS_PER_RUN", 1):
            await _drain(org_id, folder_id, StubModel(reply), rounds=1)
        _, summary = await _watermark(db_session, org_id, folder_id)
        assert budgets == [None, cc.MAX_SUMMARY_CHARS]
        assert cc.split_summary(summary)[0] == "Short and whole."

    @pytest.mark.asyncio
    async def test_still_too_long_drops_whole_lines_least_important_first(
        self, db_session, channel
    ):
        org_id, folder_id = channel
        await _chatter(db_session, org_id, folder_id, cc.COMPACT_AFTER)
        filler = [
            f"Somebody said hello again, round {chr(97 + i % 26)}." for i in range(80)
        ]
        keep = "The order was cancelled and will not ship."
        lines = filler[:40] + [keep] + filler[40:]

        def reply(previous, transcript, max_chars):
            return "\n".join(lines)

        with patch.object(cc, "MAX_FOLDS_PER_RUN", 1):
            await _drain(org_id, folder_id, StubModel(reply), rounds=1)
        _, summary = await _watermark(db_session, org_id, folder_id)
        narrative, _ = cc.split_summary(summary)
        assert 0 < len(narrative) <= cc.MAX_SUMMARY_CHARS
        # Every line is one the model wrote, whole.
        assert all(line in lines for line in narrative.splitlines())
        assert keep in narrative.splitlines()


class TestKeyLines:
    def _row(self, body, i=5):
        row = type("Row", (), {})()
        row.id, row.actor, row.workflow_id, row.summary = i, "human", None, ""
        row.payload = {"body": body}
        return row

    def test_rupees_and_abbreviations_stay_in_one_sentence(self):
        notes = cc.key_notes(
            self._row("Hello all. The rate is Rs. 450 per box now."), {}
        )
        assert notes == ["- [#5] A teammate: The rate is Rs. 450 per box now."]

    def test_a_long_sentence_is_kept_around_its_reason(self):
        background, correction = BACKGROUND["english"]
        (note,) = cc.key_notes(self._row(background + correction), {})
        assert note.endswith(correction)
        assert (
            "…" in note and len(note) <= len("- [#5] A teammate: ") + cc.MAX_NOTE_CHARS
        )

    def test_plain_chat_keeps_nothing(self):
        assert cc.key_notes(self._row("sounds great, thanks everyone"), {}) == []

    def test_kept_lines_over_budget_drop_the_weakest_and_oldest_first(self):
        promise = [
            f"- [#{i}] A teammate: We will send the deck {'x' * 200}"
            for i in range(1, 10)
        ]
        cancel = "- [#2] A teammate: The visit is cancelled."
        kept = cc._fit_notes(promise[:1] + [cancel] + promise[1:])
        assert cancel in kept
        assert sum(len(l) + 1 for l in kept) <= cc.MAX_NOTES_CHARS
        assert promise[-1] in kept and promise[0] not in kept

    def test_the_stored_summary_round_trips(self):
        stored = cc.join_summary("Narrative.", ["- [#3] A: x 1"])
        assert cc.split_summary(stored) == ("Narrative.", ["- [#3] A: x 1"])
        assert cc.split_summary("Old style summary") == ("Old style summary", [])


@pytest.mark.asyncio
async def test_raw_source_is_scoped_to_the_channel(db_session, channel):
    org_id, folder_id = channel
    other = await db_session.create_folder("Elsewhere", org_id)
    said = await _say(db_session, org_id, other.id, "  Exact   wording,\nkept.  ")
    private = await _say(
        db_session, org_id, folder_id, "mine", payload={"private_to": "4"}
    )
    assert (
        await cc.source_text(organization_id=org_id, folder_id=folder_id, event_id=said)
        is None
    )
    assert (
        await cc.source_text(organization_id=org_id, folder_id=other.id, event_id=said)
        == "  Exact   wording,\nkept.  "
    )
    assert (
        await cc.source_text(
            organization_id=org_id, folder_id=folder_id, event_id=private
        )
        is None
    )
    assert (
        await cc.source_text(
            organization_id=org_id + 1, folder_id=other.id, event_id=said
        )
        is None
    )
