"""The voice speaks the language the reply is written in.

A live tester reported the Elock agent "reads digits in Hindi" while it was
answering a Tamil caller. Both halves of that were true at once: the model had
switched to Tamil, and the voice was still configured with ``hi-IN``, because
nothing in the pipeline connected what was decided to what was said.

Following the caller (``language_follower``) does not fix it and should not try
to. That decision is deliberately slow -- three consecutive turns, short
utterances ignored -- because it is made on a guess. This one is made on the
text itself, which is not a guess, so it is made immediately.

The tests that matter are of two kinds: Tamil text must never be spoken as
Hindi, and Latin text must never move anything at all.
"""

from unittest.mock import AsyncMock

import pytest
from pipecat.frames.frames import (
    InterimTranscriptionFrame,
    LLMFullResponseStartFrame,
    TextFrame,
    TranscriptionFrame,
    TTSSpeakFrame,
    TTSUpdateSettingsFrame,
)
from pipecat.processors.frame_processor import FrameDirection

from api.services.pipecat.language_follower import configured_language
from api.services.pipecat.spoken_language import (
    SpokenLanguageFollower,
    script_language,
)

TAMIL = "இப்ப invoice number சொல்லுங்க"
HINDI = "TT number सही है क्या"
KANNADA = "ಈಗ ಇನ್ವಾಯ್ಸ್ ನಂಬರ್ ಹೇಳಿ"
HINGLISH = "TT number TN 70 AV 8480, sahi hai? haan ya nahin?"


def _follower(**kwargs):
    follower = SpokenLanguageFollower(**kwargs)
    follower.push_frame = AsyncMock()
    return follower


async def _says(follower, text, frame=TextFrame):
    await follower.process_frame(frame(text), FrameDirection.DOWNSTREAM)


def _settings(follower):
    """Every TTS settings frame the follower pushed, in order."""
    return [
        call.args[0]
        for call in follower.push_frame.await_args_list
        if isinstance(call.args[0], TTSUpdateSettingsFrame)
    ]


class TestReadingTheScript:
    def test_each_indian_script_names_its_language(self):
        assert script_language(TAMIL) == "ta"
        assert script_language(HINDI) == "hi"
        assert script_language(KANNADA) == "kn"

    def test_latin_says_nothing_rather_than_english(self):
        """Romanised Hindi is the common case on this product, and calling it
        English would point the voice at the wrong language on every Hinglish
        sentence. Nothing known is the honest answer."""
        assert script_language(HINGLISH) is None
        assert script_language("Yes. Confirm.") is None
        assert script_language("") is None
        assert script_language("TN 70 AV 8480") is None

    def test_a_stray_character_is_not_a_language(self):
        """One Devanagari word inside an English sentence -- a name, a unit --
        must not move the voice."""
        assert script_language("Press 6 then hash हाँ") is None

    def test_devanagari_stays_marathi_for_a_marathi_agent(self):
        """Hindi and Marathi share a script and nothing in the characters
        separates them, so the agent's own language breaks the tie. Reading a
        Marathi agent's own text as Hindi would be a change, not a fix."""
        assert script_language(HINDI, current="mr") == "mr"
        assert script_language(HINDI, current="ta") == "hi"


@pytest.mark.asyncio
class TestTheVoiceFollowsTheWords:
    async def test_tamil_text_is_not_spoken_as_hindi(self):
        """The reported bug, exactly: a Hindi-configured voice, a Tamil
        reply."""
        follower = _follower(initial_language="hi-IN")

        await _says(follower, TAMIL)

        assert [s.delta.language for s in _settings(follower)] == ["ta"]
        assert follower.current_language == "ta"

    async def test_the_greeting_counts_too(self):
        """The opening line is a TTSSpeakFrame queued by the engine rather
        than a model reply, and it is the first thing anybody hears."""
        follower = _follower(initial_language="hi-IN")

        await _says(follower, KANNADA, frame=TTSSpeakFrame)

        assert [s.delta.language for s in _settings(follower)] == ["kn"]

    async def test_a_reply_in_the_configured_language_changes_nothing(self):
        follower = _follower(initial_language="hi-IN")

        await _says(follower, HINDI)

        assert _settings(follower) == []

    async def test_an_english_agent_is_never_touched(self):
        """Latin text carries no script signal, so an English or Hinglish
        agent goes through this processor without it ever doing anything."""
        follower = _follower(initial_language="en-IN")

        await _says(follower, HINGLISH)
        await _says(follower, "Please say the ten digit invoice number.")

        assert _settings(follower) == []
        assert follower.current_language == "en"

    async def test_a_voice_with_nothing_pinned_is_still_pointed(self):
        """An unpinned Sarvam voice is not language-neutral: the service
        factory falls back to Hindi for it. So "nothing configured" is exactly
        the case that needs telling."""
        follower = _follower(initial_language=None)

        await _says(follower, TAMIL)

        assert [s.delta.language for s in _settings(follower)] == ["ta"]

    async def test_unknown_is_not_a_language_to_compare_against(self):
        """``unknown`` is Sarvam's own "work it out", and it is what the
        managed stack stores."""
        follower = _follower(initial_language="unknown")

        await _says(follower, TAMIL)

        assert [s.delta.language for s in _settings(follower)] == ["ta"]


@pytest.mark.asyncio
class TestItSpeaksOnceAndInTime:
    async def test_a_streamed_reply_pushes_one_settings_frame(self):
        """A reply arrives as dozens of tokens. One language change, not one
        per token: each is a settings update the voice service has to act on
        mid-sentence."""
        follower = _follower(initial_language="hi-IN")

        await follower.process_frame(
            LLMFullResponseStartFrame(), FrameDirection.DOWNSTREAM
        )
        for token in ("இப்ப ", "invoice ", "number ", "சொல்லுங்க"):
            await _says(follower, token)

        assert len(_settings(follower)) == 1

    async def test_the_settings_land_before_the_text_they_describe(self):
        """Ordering is the whole point of sitting here rather than after TTS:
        the voice must be told before it synthesises, not after."""
        follower = _follower(initial_language="hi-IN")

        await _says(follower, TAMIL)

        pushed = [call.args[0] for call in follower.push_frame.await_args_list]
        assert isinstance(pushed[0], TTSUpdateSettingsFrame)
        assert isinstance(pushed[1], TextFrame)

    async def test_a_short_first_token_does_not_settle_it(self):
        """The first token of a reply is routinely punctuation or one letter.
        The decision is made on the run, not on whichever fragment arrives
        first."""
        follower = _follower(initial_language="hi-IN")

        await _says(follower, '"')
        assert _settings(follower) == []

        await _says(follower, TAMIL)
        assert [s.delta.language for s in _settings(follower)] == ["ta"]

    async def test_the_next_reply_can_move_back(self):
        follower = _follower(initial_language="hi-IN")

        await follower.process_frame(
            LLMFullResponseStartFrame(), FrameDirection.DOWNSTREAM
        )
        await _says(follower, TAMIL)
        await follower.process_frame(
            LLMFullResponseStartFrame(), FrameDirection.DOWNSTREAM
        )
        await _says(follower, HINDI)

        assert [s.delta.language for s in _settings(follower)] == ["ta", "hi"]


@pytest.mark.asyncio
class TestWhatItMustNotListenTo:
    async def test_the_callers_own_words_are_not_the_reply(self):
        """Transcriptions are TextFrames too, and they travel downstream. The
        caller's language is a different decision with a deliberately higher
        bar; making it here twice, faster, would defeat the ask-first rule."""
        follower = _follower(initial_language="hi-IN")

        await follower.process_frame(
            TranscriptionFrame(text=TAMIL, user_id="caller", timestamp=""),
            FrameDirection.DOWNSTREAM,
        )
        await follower.process_frame(
            InterimTranscriptionFrame(text=TAMIL, user_id="caller", timestamp=""),
            FrameDirection.DOWNSTREAM,
        )

        assert _settings(follower) == []

    async def test_a_language_this_agent_does_not_speak_is_left_alone(self):
        """The operator listed the languages their line serves. Pointing a
        managed Sarvam voice at one outside the list is how a Hosur clinic
        ended up answering in Punjabi."""
        follower = _follower(initial_language="hi-IN", allowed=frozenset({"hi", "en"}))

        await _says(follower, TAMIL)

        assert _settings(follower) == []
        assert follower.current_language == "hi"

    async def test_every_frame_is_forwarded_whatever_was_decided(self):
        """This processor observes. It must never be the reason a reply fails
        to reach the voice."""
        follower = _follower(initial_language="hi-IN", allowed=frozenset({"hi"}))

        await _says(follower, TAMIL)

        pushed = [call.args[0] for call in follower.push_frame.await_args_list]
        assert [f.text for f in pushed if isinstance(f, TextFrame)] == [TAMIL]


class TestTheManagedBaseline:
    """``configured_language`` decides what the caller-follower compares
    against, and it read Sarvam's "no language" sentinel as a language."""

    class _Config:
        def __init__(self, language):
            self.language = language

    def test_sarvams_auto_detect_is_not_a_baseline(self):
        """Every managed Indian agent stores ``unknown`` on STT -- the service
        factory reads it as "detect it" when it builds the transcriber. Read
        here as a language, it made the caller's very first turn look like a
        change from it, so the language they were already speaking needed
        three confirmations to be adopted as the baseline it already was."""
        assert configured_language(self._Config(None), self._Config("unknown")) is None

    def test_a_real_pin_is_still_a_baseline(self):
        assert configured_language(self._Config("hi-IN"), self._Config("unknown")) == (
            "hi"
        )
