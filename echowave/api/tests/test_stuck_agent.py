"""Noticing a call that went round in circles instead of going anywhere.

A workflow moves between nodes by calling a tool, and a model that will not
call one does not fail -- it keeps talking. Nothing errors, the recording is
clean, and the only evidence is a transcript nobody reads. This is the tag
that makes it findable.

The tests that matter are the ones about *not* flagging. A signal that fires
on ordinary short calls is a signal operators learn to ignore, and then the
real one goes past unread too.
"""

from __future__ import annotations

import pytest

from api.services.pipecat import stuck_agent
from api.services.pipecat.stuck_agent import looks_stuck


class TestTheCallsItMustCatch:
    """Both from the Kriti Labs agent on gpt-5-mini, where the model answered
    in words instead of calling the tool that moves between steps."""

    def test_run_301_eight_turns_and_never_moved(self):
        assert looks_stuck(
            nodes_visited=["Greet and understand"],
            workflow_node_count=12,
            user_turns=8,
        )

    def test_run_299_thirteen_turns_and_never_moved(self):
        assert looks_stuck(
            nodes_visited=["Greet and understand"],
            workflow_node_count=12,
            user_turns=13,
        )


class TestTheCallsItMustLeaveAlone:
    def test_a_call_that_moved_on_is_never_flagged(self):
        """Run 302, the same agent on gpt-4.1-mini: it reached Verify."""
        assert not looks_stuck(
            nodes_visited=["Greet and understand", "Verify TT and invoice"],
            workflow_node_count=12,
            user_turns=10,
        )

    def test_a_caller_who_says_one_thing_and_hangs_up(self):
        """Run 294. One node visited and nothing wrong: there was no
        conversation to move through. Most single-node runs on this account
        are this, which is why the turn count decides."""
        assert not looks_stuck(
            nodes_visited=["Greet and understand"],
            workflow_node_count=12,
            user_turns=1,
        )

    @pytest.mark.parametrize("turns", [0, 1, 2, 3])
    def test_a_call_too_short_to_have_needed_a_transition(self, turns):
        """Measured: every working agent had moved on by the caller's third
        turn, so four is that boundary with a turn of headroom."""
        assert not looks_stuck(
            nodes_visited=["Greet"], workflow_node_count=5, user_turns=turns
        )

    def test_a_one_node_workflow_cannot_be_stuck(self):
        """There is nowhere for it to go, so staying put is correct."""
        assert not looks_stuck(
            nodes_visited=["Only step"], workflow_node_count=1, user_turns=50
        )

    def test_a_run_with_no_recorded_nodes_is_not_guessed_at(self):
        """A call that fell over before the engine started has nothing to say
        about tool calling, and a tag here would point at the wrong thing."""
        assert not looks_stuck(
            nodes_visited=None, workflow_node_count=12, user_turns=10
        )
        assert not looks_stuck(nodes_visited=[], workflow_node_count=12, user_turns=10)


class TestTheThreshold:
    def test_it_fires_exactly_at_the_measured_boundary(self):
        below = looks_stuck(
            nodes_visited=["Greet"],
            workflow_node_count=5,
            user_turns=stuck_agent.MIN_USER_TURNS - 1,
        )
        at = looks_stuck(
            nodes_visited=["Greet"],
            workflow_node_count=5,
            user_turns=stuck_agent.MIN_USER_TURNS,
        )

        assert (below, at) == (False, True)

    def test_the_threshold_leaves_room_above_a_working_agent(self):
        """Working agents transitioned by turn three across every run
        measured. A threshold of three would sit on that boundary."""
        assert stuck_agent.MIN_USER_TURNS > 3


class TestCountingTheCallersTurns:
    def buffer(self, events):
        from api.services.pipecat.in_memory_buffers import InMemoryLogsBuffer

        buffer = InMemoryLogsBuffer.__new__(InMemoryLogsBuffer)
        buffer._events = events
        return buffer

    def turn(self, text="hello", final=True):
        from pipecat.utils.enums import RealtimeFeedbackType

        return {
            "type": RealtimeFeedbackType.USER_TRANSCRIPTION.value,
            "payload": {"final": final, "text": text},
        }

    def test_final_transcriptions_with_words_are_counted(self):
        assert (
            self.buffer([self.turn(), self.turn(), self.turn()]).count_user_turns() == 3
        )

    def test_interim_results_are_not_turns(self):
        """Otherwise one slowly spoken sentence looks like a conversation."""
        assert self.buffer([self.turn(final=False)] * 5).count_user_turns() == 0

    def test_an_empty_transcription_is_not_a_turn(self):
        """Silence that reached the transcriber is not the caller speaking."""
        assert self.buffer([self.turn(text="")] * 4).count_user_turns() == 0

    def test_the_agents_own_speech_is_not_counted(self):
        assert (
            self.buffer(
                [{"type": "rtf-bot-text", "payload": {"text": "hi"}}] * 6
            ).count_user_turns()
            == 0
        )
