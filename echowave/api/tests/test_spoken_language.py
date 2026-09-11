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

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pipecat.frames.frames import (
    InterimTranscriptionFrame,
    LLMFullResponseStartFrame,
    STTUpdateSettingsFrame,
    TextFrame,
    TranscriptionFrame,
    TTSSpeakFrame,
    TTSUpdateSettingsFrame,
)
from pipecat.processors.frame_processor import FrameDirection
from pipecat.transcriptions.language import Language

from api.services.pipecat.language_follower import configured_language
from api.services.pipecat.service_factory import stt_language_can_be_pinned
from api.services.pipecat.spoken_language import (
    MAX_TRANSCRIBER_PINS,
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


def _stt_settings(follower):
    """Every transcriber settings frame the follower pushed, with direction."""
    return [
        (call.args[0], call.args[1])
        for call in follower.push_frame.await_args_list
        if isinstance(call.args[0], STTUpdateSettingsFrame)
    ]


@pytest.mark.asyncio
class TestItAlsoPointsTheTranscriber:
    """Run 299: a caller asked for Tamil, in Tamil, and one stretch of their
    speech came back as Devanagari, then Marathi, then romanised Tamil, then
    English. The transcriber was guessing per utterance and guessing badly.

    By the time the model has written its reply in Tamil script there is
    nothing left to guess, so the transcriber is told."""

    async def test_the_transcriber_is_told_which_language_it_is_hearing(self):
        follower = _follower(initial_language="hi-IN", pin_transcriber=True)

        await _says(follower, TAMIL)

        frames = _stt_settings(follower)
        assert len(frames) == 1
        assert frames[0][0].delta.language == Language.TA_IN

    async def test_it_is_pushed_upstream_because_that_is_where_the_transcriber_is(
        self,
    ):
        """The transcriber is behind this processor, not in front of it. A
        settings frame sent downstream would sail past the voice and reach
        nothing that transcribes."""
        follower = _follower(initial_language="hi-IN", pin_transcriber=True)

        await _says(follower, TAMIL)

        assert _stt_settings(follower)[0][1] is FrameDirection.UPSTREAM

    async def test_nothing_is_pushed_unless_it_was_asked_for(self):
        """Off by default: most transcribers are detecting on purpose."""
        follower = _follower(initial_language="hi-IN")

        await _says(follower, TAMIL)

        assert _stt_settings(follower) == []
        assert _settings(follower)  # the voice still moved

    async def test_a_reply_in_the_language_already_set_moves_nothing(self):
        follower = _follower(initial_language="ta-IN", pin_transcriber=True)

        await _says(follower, TAMIL)

        assert _stt_settings(follower) == []

    async def test_latin_text_never_re_points_anything(self):
        """Hinglish is Latin script. Reconnecting the transcriber for it would
        cost audio and settle nothing."""
        follower = _follower(initial_language="hi-IN", pin_transcriber=True)

        await _says(follower, HINGLISH)

        assert _stt_settings(follower) == []

    async def test_a_language_the_agent_may_not_speak_is_not_pinned_either(self):
        """The allow-list guards the transcriber as well as the voice: a
        language the operator never sanctioned must not become the one the
        call is transcribed in."""
        follower = _follower(
            initial_language="hi-IN",
            allowed=frozenset({"hi", "en"}),
            pin_transcriber=True,
        )

        await _says(follower, TAMIL)

        assert _stt_settings(follower) == []


@pytest.mark.asyncio
class TestTheReconnectsAreCapped:
    """Each pin closes the transcriber's websocket and opens a new one, and
    the audio arriving in that gap is gone. A call that wants a fourth is a
    detector flapping, not a caller changing their mind."""

    async def test_a_flapping_call_stops_being_obeyed(self):
        follower = _follower(initial_language="en-IN", pin_transcriber=True)

        for text in (TAMIL, HINDI, KANNADA, TAMIL, HINDI):
            await follower.process_frame(
                LLMFullResponseStartFrame(), FrameDirection.DOWNSTREAM
            )
            await _says(follower, text)

        assert follower.transcriber_pins == MAX_TRANSCRIBER_PINS
        assert len(_stt_settings(follower)) == MAX_TRANSCRIBER_PINS

    async def test_the_voice_keeps_following_after_the_cap(self):
        """The cap is on reconnecting a websocket, not on speaking correctly.
        A reply in Hindi is still spoken in Hindi once the pins run out."""
        follower = _follower(initial_language="en-IN", pin_transcriber=True)

        for text in (TAMIL, HINDI, KANNADA, TAMIL):
            await follower.process_frame(
                LLMFullResponseStartFrame(), FrameDirection.DOWNSTREAM
            )
            await _says(follower, text)

        assert len(_settings(follower)) == 4
        assert _settings(follower)[-1].delta.language == "ta"


class TestWhichTranscribersCanBeTold:
    """Narrow on purpose. A provider is here because leaving it to guess was
    measured to be worse, not because the parameter exists."""

    def _config(self, provider, model):
        return SimpleNamespace(stt=SimpleNamespace(provider=provider, model=model))

    def test_sarvams_transcribing_models_can_be_told(self):
        assert stt_language_can_be_pinned(self._config("sarvam", "saaras:v3"))
        assert stt_language_can_be_pinned(self._config("sarvam", "saarika:v2.5"))

    def test_the_translate_model_cannot_and_would_raise_if_asked(self):
        """saaras:v2.5 auto-detects and rejects a language outright, so this
        is excluded by what the model accepts rather than by its name."""
        assert not stt_language_can_be_pinned(self._config("sarvam", "saaras:v2.5"))

    def test_deepgram_is_left_to_detect(self):
        """Its multilingual models are asked to detect on purpose and follow a
        caller who switches mid-sentence. Pinning gives that up."""
        assert not stt_language_can_be_pinned(self._config("deepgram", "nova-3"))

    def test_an_unknown_model_is_not_assumed_to_take_one(self):
        assert not stt_language_can_be_pinned(self._config("sarvam", "saaras:v9"))
        assert not stt_language_can_be_pinned(self._config("sarvam", None))
