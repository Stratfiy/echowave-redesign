"""Following the caller's language mid-call.

The detection was already there — Deepgram runs ``multi`` by default, so every
transcription has carried a language and nothing read it. The work is not
detecting; it is deciding when a detection means the conversation actually
changed language.

**That decision is the whole feature.** Speech recognition mis-detects language
constantly on short utterances, and an agent that changes voice on each
mis-detection is markedly worse than one that never changes at all — it sounds
broken rather than merely inflexible. So the tests that matter are the ones
about *not* switching.
"""

from unittest.mock import AsyncMock

import pytest
from pipecat.frames.frames import TranscriptionFrame, TTSUpdateSettingsFrame
from pipecat.processors.frame_processor import FrameDirection

from api.services.configuration.registry import (
    DecibylSTTService,
    DecibylTTSService,
    DeepgramSTTConfiguration,
    ElevenlabsTTSConfiguration,
    SarvamTTSConfiguration,
)
from api.services.pipecat.language_follower import (
    CONFIRMATIONS_BEFORE_SWITCH,
    LanguageFollower,
    configured_language,
    primary_subtag,
)

# Long enough to clear MIN_CHARS_FOR_DETECTION — short utterances are ignored
# by design and are tested separately.
LONG = "this is a sentence long enough to be worth trusting"
LONG_HI = "yeh ek lambi baat hai jise sunkar bhasha pehchani ja sakti hai"


def _follower(**kwargs):
    follower = LanguageFollower(**kwargs)
    follower.push_frame = AsyncMock()
    return follower


def _said(text: str, language: str | None):
    return TranscriptionFrame(
        text=text, user_id="caller", timestamp="", language=language
    )


async def _hear(follower, text, language, times=1):
    for _ in range(times):
        await follower.process_frame(_said(text, language), FrameDirection.DOWNSTREAM)


def _switches(follower):
    return [
        call.args[0]
        for call in follower.push_frame.await_args_list
        if isinstance(call.args[0], TTSUpdateSettingsFrame)
    ]


class TestPrimarySubtag:
    """``hi`` and ``hi-IN`` are the same language. Providers disagree about how
    specific a tag to return, and vary within a single call."""

    @pytest.mark.parametrize(
        ("given", "expected"),
        [("hi", "hi"), ("hi-IN", "hi"), ("EN-us", "en"), ("  ta  ", "ta")],
    )
    def test_it_reduces_to_the_language(self, given, expected):
        assert primary_subtag(given) == expected

    @pytest.mark.parametrize("given", [None, "", "   "])
    def test_nothing_useful_gives_nothing(self, given):
        assert primary_subtag(given) is None


class TestWhereTheConfiguredLanguageLives:
    """Built against the real config classes, not stubs. The whole reason this
    function exists is that the providers disagree about which half of the
    pipeline owns the language field, and a stub would agree with whatever I
    assumed."""

    def test_the_tts_language_wins_when_there_is_one(self):
        assert (
            configured_language(
                SarvamTTSConfiguration(api_key="k", language="hi-IN"),
                DeepgramSTTConfiguration(api_key="k", language="en-US"),
            )
            == "hi"
        )

    def test_it_falls_back_to_stt_when_the_tts_has_no_language_field(self):
        """ElevenLabs' config has no language at all — its multilingual models
        infer it from the text. Without this fallback the most common BYOK
        stack in the product would start with no baseline."""
        assert not hasattr(ElevenlabsTTSConfiguration(api_key="k"), "language")

        assert (
            configured_language(
                ElevenlabsTTSConfiguration(api_key="k"),
                DeepgramSTTConfiguration(api_key="k", language="hi"),
            )
            == "hi"
        )

    def test_managed_multi_means_no_configured_language(self):
        """The managed default. "multi" is an instruction to detect, not a
        language — treating it as one would make the first two genuine turns
        of every managed call look like a change and switch on them."""
        assert (
            configured_language(
                DecibylTTSService(api_key="k"),
                DecibylSTTService(api_key="k", language="multi"),
            )
            is None
        )

    def test_a_managed_call_pinned_to_a_language_keeps_it(self):
        assert (
            configured_language(
                DecibylTTSService(api_key="k"),
                DecibylSTTService(api_key="k", language="ta-IN"),
            )
            == "ta"
        )

    def test_nothing_configured_anywhere_is_not_an_error(self):
        assert configured_language(None, None) is None


@pytest.mark.asyncio
class TestItDoesNotSwitchWhenItShouldNot:
    """The important half. Each of these, if it switched, would make the agent
    sound broken to a caller who did nothing unusual."""

    async def test_one_turn_in_another_language_is_not_enough(self):
        """A single mis-detected utterance is the common case, not the rare
        one. It must not move the voice."""
        follower = _follower(initial_language="hi")

        await _hear(follower, LONG, "en")

        assert _switches(follower) == []

    async def test_a_region_variant_is_not_a_change(self):
        """hi → hi-IN would otherwise switch the voice for no reason, and
        providers alternate between the two spellings within one call."""
        follower = _follower(initial_language="hi")

        await _hear(follower, LONG_HI, "hi-IN", times=5)

        assert _switches(follower) == []

    async def test_short_utterances_are_ignored(self):
        """ "hmm", "achha", "ok" — where detection is least reliable and most
        frequent. Counting them would let backchannel drive the voice."""
        follower = _follower(initial_language="hi")

        await _hear(follower, "ok", "en", times=10)

        assert _switches(follower) == []

    async def test_confirmations_must_be_consecutive(self):
        """A caller alternating between languages would otherwise accumulate
        enough total hits to trip a switch, when what they are actually doing
        is code-switching within one conversation."""
        follower = _follower(initial_language="hi")

        for _ in range(4):
            await _hear(follower, LONG, "en")
            await _hear(follower, LONG_HI, "hi")

        assert _switches(follower) == []

    async def test_a_turn_with_no_detected_language_changes_nothing(self):
        follower = _follower(initial_language="hi")

        await _hear(follower, LONG, None, times=5)

        assert _switches(follower) == []

    async def test_a_short_interjection_does_not_break_a_genuine_switch(self):
        """ "hmm" in the middle of a real language change should neither confirm
        nor deny it — resetting on it would make switching nearly impossible in
        natural speech."""
        follower = _follower(initial_language="hi")

        await _hear(follower, LONG, "en")
        await _hear(follower, "hmm", "hi")  # too short to count either way
        await _hear(follower, LONG, "en")

        assert len(_switches(follower)) == 1


@pytest.mark.asyncio
class TestItSwitchesWhenItShould:
    async def test_two_consecutive_turns_move_the_voice(self):
        follower = _follower(initial_language="hi")

        await _hear(follower, LONG, "en", times=CONFIRMATIONS_BEFORE_SWITCH)

        (frame,) = _switches(follower)
        assert frame.delta.language == "en"
        assert follower.current_language == "en"

    async def test_the_voice_follows_when_one_is_mapped(self):
        """Some providers key the voice to the language. Changing the language
        without the voice gives an English voice reading Hindi phonetically."""
        follower = _follower(
            initial_language="en", voice_for_language={"hi": "meera", "en": "alloy"}
        )

        await _hear(follower, LONG_HI, "hi", times=CONFIRMATIONS_BEFORE_SWITCH)

        (frame,) = _switches(follower)
        assert frame.delta.language == "hi"
        assert frame.delta.voice == "meera"

    async def test_a_multilingual_voice_is_left_alone(self):
        """ElevenLabs multilingual and Sarvam's Bulbul speak many languages in
        one voice. Sending an unset voice keeps it; sending an empty one would
        clear it."""
        from pipecat.services.settings import is_given

        follower = _follower(initial_language="en")

        await _hear(follower, LONG_HI, "hi", times=CONFIRMATIONS_BEFORE_SWITCH)

        (frame,) = _switches(follower)
        assert frame.delta.language == "hi"
        assert not is_given(frame.delta.voice)

    async def test_it_can_switch_back(self):
        follower = _follower(initial_language="hi")

        await _hear(follower, LONG, "en", times=CONFIRMATIONS_BEFORE_SWITCH)
        await _hear(follower, LONG_HI, "hi", times=CONFIRMATIONS_BEFORE_SWITCH)

        assert [f.delta.language for f in _switches(follower)] == ["en", "hi"]

    async def test_the_first_language_heard_becomes_the_baseline(self):
        """With nothing configured, the opening turn is a baseline rather than
        a change — otherwise every call would switch on its first sentence."""
        follower = _follower(initial_language=None)

        await _hear(follower, LONG, "en")

        assert _switches(follower) == []
        assert follower.current_language == "en"


@pytest.mark.asyncio
class TestItNeverBreaksTheCall:
    async def test_transcriptions_are_always_forwarded(self):
        """This processor observes. If it ever swallowed a transcription the
        LLM would stop hearing the caller."""
        follower = _follower(initial_language="hi")

        await _hear(follower, LONG, "en", times=3)

        forwarded = [
            call.args[0]
            for call in follower.push_frame.await_args_list
            if isinstance(call.args[0], TranscriptionFrame)
        ]
        assert len(forwarded) == 3

    async def test_a_failing_callback_does_not_propagate(self):
        """Observability must not be able to take down a live call."""
        follower = _follower(
            initial_language="hi", on_switch=AsyncMock(side_effect=RuntimeError("nope"))
        )

        await _hear(follower, LONG, "en", times=CONFIRMATIONS_BEFORE_SWITCH)

        assert len(_switches(follower)) == 1

    async def test_every_language_heard_is_recorded(self):
        """ "Which languages did this call involve" is not answerable from one
        column, and it is the question asked when a campaign underperforms in
        one region."""
        follower = _follower(initial_language="hi")

        await _hear(follower, LONG_HI, "hi")
        await _hear(follower, LONG, "en")
        await _hear(follower, "short", "ta")  # short, but still heard

        assert follower.languages_heard == ["hi", "en", "ta"]
