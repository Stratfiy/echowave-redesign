"""A call whose agent cannot speak has to end by itself.

``on_pipeline_error`` deliberately stops hanging up on non-fatal errors: pipecat
defaults ``push_error`` to ``fatal=False``, and ending a customer's call because
a provider grumbled about a keepalive was cutting good conversations off at 0s.

The gap that left is this one. A provider that fails to *connect* also arrives
as ``fatal=False``, but it is dead for the rest of the call — the agent never
says a word, the caller sits in silence until they give up, and the run is then
marked completed and billed. The frame cannot tell the two apart. Their
consequence can: after a connect failure the agent never speaks.

These cover the signal that decides it. The direction of the risk is asymmetric
and the tests are written around that: a false "the agent spoke" costs us a
watchdog that does not fire, which is merely the old behaviour, while a false
"the agent is silent" would hang up on a working call — the exact regression the
non-fatal path exists to prevent.
"""

from __future__ import annotations

from api.services.pipecat.in_memory_buffers import InMemoryLogsBuffer

BOT_TEXT = "rtf-bot-text"
USER_TRANSCRIPTION = "rtf-user-transcription"


def _buffer(events: list[dict]) -> InMemoryLogsBuffer:
    buffer = InMemoryLogsBuffer(workflow_run_id=1)
    buffer._events = events
    return buffer


class TestContainsBotSpeech:
    def test_a_buffer_with_no_events_has_no_bot_speech(self):
        assert _buffer([]).contains_bot_speech() is False

    def test_a_call_where_only_the_caller_spoke_has_no_bot_speech(self):
        """The shape of a connect failure: the human says hello, we say nothing."""
        events = [
            {
                "type": USER_TRANSCRIPTION,
                "payload": {"final": True, "text": "Hello? Are you there?"},
            }
        ]
        assert _buffer(events).contains_bot_speech() is False

    def test_one_line_from_the_agent_is_enough(self):
        events = [{"type": BOT_TEXT, "payload": {"text": "Hi, how can I help?"}}]
        assert _buffer(events).contains_bot_speech() is True

    def test_an_empty_bot_line_does_not_count(self):
        """A bot text event with no text is a frame, not something anyone heard."""
        events = [{"type": BOT_TEXT, "payload": {"text": ""}}]
        assert _buffer(events).contains_bot_speech() is False

    def test_a_malformed_event_does_not_raise(self):
        """The watchdog calls this on the error path. It must not add an error."""
        events = [{"type": BOT_TEXT}, {}, {"payload": {"text": "orphan"}}]
        assert _buffer(events).contains_bot_speech() is False

    def test_it_finds_bot_speech_after_other_events(self):
        events = [
            {"type": USER_TRANSCRIPTION, "payload": {"final": True, "text": "hi"}},
            {"type": "rtf-node-transition", "payload": {"node_name": "greet"}},
            {"type": BOT_TEXT, "payload": {"text": "Good morning."}},
        ]
        assert _buffer(events).contains_bot_speech() is True

    def test_user_and_bot_speech_are_asked_separately(self):
        """The two questions must not share an answer.

        A call where the caller spoke and the agent did not is precisely the
        one the watchdog exists for, so `contains_user_speech` returning True
        must not imply anything about the agent.
        """
        events = [
            {
                "type": USER_TRANSCRIPTION,
                "payload": {"final": True, "text": "Can you hear me?"},
            }
        ]
        buffer = _buffer(events)
        assert buffer.contains_user_speech() is True
        assert buffer.contains_bot_speech() is False
