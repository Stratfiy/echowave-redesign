"""Whether an agent follows the caller into another language.

This replaced an environment variable, which made following a property of the
server rather than of the agent — so an account could have it on its clinic
line or on its compliance line, but not one and not the other.
"""

import pytest

from api.services.pipecat.language_following import (
    CONFIG_KEY,
    should_follow_caller_language,
)


class TestTheAgentDecides:
    def test_on_when_turned_on(self):
        assert should_follow_caller_language({CONFIG_KEY: True}) is True

    def test_off_when_turned_off(self):
        assert should_follow_caller_language({CONFIG_KEY: False}) is False

    @pytest.mark.parametrize("configs", [{}, {CONFIG_KEY: None}, None, "nonsense", 7])
    def test_off_until_somebody_turns_it_on(self, configs):
        """Nothing follows today.

        A default of "on" would change the behaviour of every live call in one
        deploy, on a judgement nobody made per agent.
        """
        assert should_follow_caller_language(configs) is False

    @pytest.mark.parametrize("raw,expected", [(1, True), (0, False), ("", False)])
    def test_reads_what_an_older_client_stored(self, raw, expected):
        assert should_follow_caller_language({CONFIG_KEY: raw}) is expected


class TestRealtimeIsAlwaysOff:
    @pytest.mark.parametrize("configs", [{CONFIG_KEY: True}, {}, None])
    def test_whatever_anybody_configured(self, configs):
        """A speech-to-speech model answers in the language it hears.

        There are no transcription frames to watch and no TTS settings to push,
        so following would do nothing — and pinning a language on one would
        make it worse at exactly what it is already good at.
        """
        assert should_follow_caller_language(configs, is_realtime=True) is False
