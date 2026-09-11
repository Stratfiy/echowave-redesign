"""What becomes a memory, and what is dropped before it costs anything.

Every test here is a decision that is expensive to change once a graph has
been built on it: what time a call is filed under, what a call has to contain
before it is worth an extraction, and whether a value the workflow confirmed
is stored the same way as a line the speech recogniser guessed at.
"""

from __future__ import annotations

from datetime import UTC, datetime

from api.services.knowledge_graph.episodes import (
    MAX_EPISODE_CHARS,
    SOURCE_MESSAGE,
    SOURCE_TEXT,
    episode_from_call,
    episodes_from_document,
)

HAPPENED = datetime(2026, 8, 14, 10, 30, tzinfo=UTC)
A_REAL_CALL = (
    "Agent: Narayani Dental, good morning. Caller: Enakku pallu vali, "
    "I need to see the doctor. Agent: Of course, when suits you? "
    "Caller: Next Thursday eleven o'clock please."
)


def _call(**overrides):
    kwargs = dict(
        organization_id=42,
        group_id="org:42",
        workflow_run_id=313,
        agent_name="Narayani",
        transcript=A_REAL_CALL,
        happened_at=HAPPENED,
    )
    kwargs.update(overrides)
    return episode_from_call(**kwargs)


class TestWhenACallHappened:
    def test_the_episode_is_filed_under_the_call_not_the_ingestion(self):
        """The whole reason for a temporal graph.

        Backfilling a month of calls today must not make them all facts about
        today, or "what did we know before the appointment" answers with
        everything learned after it.
        """
        assert _call().reference_time == HAPPENED

    def test_the_partition_is_carried_not_recomputed(self):
        assert _call().group_id == "org:42"

    def test_a_call_is_a_dialogue_not_prose(self):
        assert _call().source == SOURCE_MESSAGE

    def test_the_run_id_names_it_so_a_reingest_is_recognisable(self):
        assert _call().name == "call:313"


class TestWhatIsNotWorthRemembering:
    def test_an_empty_transcript_produces_nothing(self):
        assert _call(transcript=None) is None

    def test_a_hangup_produces_nothing(self):
        """Two words before the line drops is not a memory, it is a cost."""
        assert _call(transcript="Hello? Hello?") is None

    def test_but_a_short_call_that_recorded_something_is_kept(self):
        """The caller said little and the workflow still captured a fact."""
        episode = _call(
            transcript="Yes. Okay.",
            gathered_context={"phone_number": "9840012345"},
        )
        assert episode is not None
        assert "9840012345" in episode.body


class TestConfirmedValuesAreNotJustMoreTranscript:
    def test_recorded_values_are_marked_as_confirmed(self):
        episode = _call(gathered_context={"preferred_time": "Thursday 11am"})
        assert "confirmed values" in episode.body
        assert "preferred time: Thursday 11am" in episode.body

    def test_the_transcript_is_marked_as_merely_heard(self):
        assert "as heard" in _call().body

    def test_workflow_plumbing_is_left_out(self):
        """A nested structure is machinery, not something a person said."""
        episode = _call(
            gathered_context={"raw_payload": {"a": 1}, "tags": ["x"], "name": "Kumar"}
        )
        assert "Kumar" in episode.body
        assert "raw_payload" not in episode.body
        assert "tags" not in episode.body

    def test_blank_values_are_left_out(self):
        episode = _call(gathered_context={"notes": "   ", "name": "Kumar"})
        assert "notes" not in episode.body


class TestOneCallCannotCostUnboundedly:
    def test_a_runaway_transcript_is_capped(self):
        episode = _call(transcript="Caller: yes.\n" * 5000)
        assert len(episode.body) <= MAX_EPISODE_CHARS

    def test_the_cut_lands_on_a_line_rather_than_mid_sentence(self):
        episode = _call(transcript="Caller: yes okay fine.\n" * 5000)
        assert not episode.body.endswith("Caller: yes o")


class TestDocuments:
    def test_one_episode_per_chunk(self):
        episodes = episodes_from_document(
            group_id="org:42",
            document_uuid="abc",
            filename="Fee schedule.pdf",
            chunks=["Cleaning is 800 rupees.", "Root canal is 6000 rupees."],
            published_at=HAPPENED,
        )
        assert len(episodes) == 2
        assert episodes[1].name == "document:abc:1"

    def test_a_document_is_prose_not_dialogue(self):
        episodes = episodes_from_document(
            group_id="org:42",
            document_uuid="abc",
            filename="f.pdf",
            chunks=["Cleaning is 800 rupees."],
            published_at=HAPPENED,
        )
        assert episodes[0].source == SOURCE_TEXT

    def test_it_is_filed_under_when_the_document_was_given_to_us(self):
        """March's fee schedule states March's fees, not today's."""
        episodes = episodes_from_document(
            group_id="org:42",
            document_uuid="abc",
            filename="f.pdf",
            chunks=["Cleaning is 800 rupees."],
            published_at=HAPPENED,
        )
        assert episodes[0].reference_time == HAPPENED

    def test_empty_chunks_are_skipped(self):
        episodes = episodes_from_document(
            group_id="org:42",
            document_uuid="abc",
            filename="f.pdf",
            chunks=["", "   ", "Real content here."],
            published_at=HAPPENED,
        )
        assert len(episodes) == 1
