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
from pipecat.frames.frames import (
    LLMMessagesAppendFrame,
    TranscriptionFrame,
    TTSUpdateSettingsFrame,
)
from pipecat.processors.frame_processor import FrameDirection

from api.services.configuration.registry import (
    DecibylSTTService,
    DecibylTTSService,
    DeepgramSTTConfiguration,
    ElevenlabsTTSConfiguration,
    SarvamTTSConfiguration,
)
from api.services.pipecat.language_follower import (
    CONFIRMATIONS_BEFORE_ASKING,
    CONFIRMATIONS_BEFORE_SWITCH,
    LanguageFollower,
    configured_language,
    primary_subtag,
    switch_instruction,
)

# Long enough to clear MIN_CHARS_FOR_DETECTION — short utterances are ignored
# by design and are tested separately.
LONG = "this is a sentence long enough to be worth trusting"
LONG_HI = "yeh ek lambi baat hai jise sunkar bhasha pehchani ja sakti hai"
LONG_TA = "idhu oru nedhiya vaakkiyam, mozhi kandupidikka podhumana neelam"


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

    def test_it_falls_back_to_stt_when_the_tts_language_is_unset(self):
        """ElevenLabs' config now has a language field, and it defaults to
        unset. That default is deliberate -- a default of "en" would tell the
        vendor to read Tamil with English phonetics -- so the fallback this
        function exists for still has to fire, and an added field must not
        quietly become an answer of None where STT had one.

        The assertion this replaces was `not hasattr(...)`, which was true
        until the field was added and was never the property that mattered.
        """
        elevenlabs = ElevenlabsTTSConfiguration(api_key="k")
        assert hasattr(elevenlabs, "language")
        assert elevenlabs.language is None

        assert (
            configured_language(
                elevenlabs,
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
        # The rest of the run the interjection must not have broken. Counted
        # off the constant rather than written out, so raising the threshold
        # again cannot leave this silently testing something smaller.
        await _hear(follower, LONG, "en", times=CONFIRMATIONS_BEFORE_SWITCH - 1)

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


class TestItTellsTheModelToo:
    """Switching the voice without telling the model is worse than not
    switching. The model keeps answering in the language its prompt is written
    in, and the new voice reads that text out — a Tamil voice speaking English."""

    def _instructions(self, follower):
        return [
            call.args[0]
            for call in follower.push_frame.await_args_list
            if isinstance(call.args[0], LLMMessagesAppendFrame)
        ]

    def _switch_instructions(self, follower):
        """Only the ones that commit. A full run also carries the question
        that precedes them — see TestItAsksBeforeItSwitches."""
        return [
            frame
            for frame in self._instructions(follower)
            if "Reply only in" in frame.messages[0]["content"]
        ]

    async def test_a_switch_puts_the_new_language_in_the_context(self):
        follower = _follower(initial_language="hi")

        await _hear(follower, LONG_TA, "ta", times=CONFIRMATIONS_BEFORE_SWITCH)

        appended = self._switch_instructions(follower)
        assert len(appended) == 1
        message = appended[0].messages[0]
        assert message["role"] == "system"
        # Named, not tagged. A model told to answer in "ta" is being asked to
        # guess; told Tamil, it does not have to.
        assert "Tamil" in message["content"]
        assert "ta" != message["content"]

    async def test_the_instruction_does_not_trigger_a_reply_of_its_own(self):
        """It rides just ahead of the caller's own transcription. That turn is
        what the model should answer — in the language it has just been given."""
        follower = _follower(initial_language="hi")

        await _hear(follower, LONG_TA, "ta", times=CONFIRMATIONS_BEFORE_SWITCH)

        # Every instruction, the question included: none of them is the model's
        # cue to speak on its own.
        assert self._instructions(follower)
        assert all(frame.run_llm is False for frame in self._instructions(follower))

    async def test_not_switching_says_nothing_to_the_model(self):
        """The whole feature is about when *not* to act. A model given a
        language instruction on a mis-detected "hmm" is the same defect as a
        voice that flips on one."""
        follower = _follower(initial_language="hi")

        await _hear(follower, LONG_TA, "ta")  # one turn is noise, not a switch

        assert self._instructions(follower) == []

    async def test_the_voice_and_the_model_are_told_together(self):
        """Either one alone leaves the call mismatched."""
        follower = _follower(initial_language="hi")

        await _hear(follower, LONG_TA, "ta", times=CONFIRMATIONS_BEFORE_SWITCH)

        assert len(_switches(follower)) == 1
        assert len(self._switch_instructions(follower)) == 1

    def test_an_unlisted_tag_still_produces_an_instruction(self):
        """A tag with no name is still a better instruction than none."""
        assert "Tamil" in switch_instruction("ta")
        assert "xx" in switch_instruction("xx")


class TestItAsksBeforeItSwitches:
    """Two turns raises the question; it does not answer it.

    A tester reported an agent that asked which language the caller wanted,
    was told, and then drifted into Hindi mid-call anyway. The cause was this
    processor: at two turns it pushed "the caller has switched to Hindi,
    reply only in Hindi" — a later and more specific instruction than the
    operator's own "change language only if the caller asks you to, in
    words", so it won. The agent overrode its own prompt on a guess.

    Now two turns puts the question and changes nothing else.
    """

    async def _asked_for(self, follower):
        """The languages the follower asked about, from the frames it pushed."""
        out = []
        for call in follower.push_frame.await_args_list:
            frame = call.args[0]
            if isinstance(frame, LLMMessagesAppendFrame):
                content = frame.messages[0]["content"]
                if "would like to continue" in content:
                    out.append(content)
        return out

    def _switched(self, follower):
        return [
            call.args[0]
            for call in follower.push_frame.await_args_list
            if isinstance(call.args[0], TTSUpdateSettingsFrame)
        ]

    async def test_two_turns_ask_and_do_not_switch(self):
        follower = _follower(initial_language="hi")
        await _hear(follower, LONG, "en", times=CONFIRMATIONS_BEFORE_ASKING)

        assert len(await self._asked_for(follower)) == 1
        assert self._switched(follower) == [], "asking is not switching"
        assert follower.current_language == "hi"

    async def test_the_question_is_put_in_the_language_being_offered(self):
        follower = _follower(initial_language="hi")
        await _hear(follower, LONG, "en", times=CONFIRMATIONS_BEFORE_ASKING)

        asked = (await self._asked_for(follower))[0]
        assert "ask them in English" in asked
        # And it keeps speaking the language it was, until told otherwise.
        assert "Keep answering in Hindi" in asked

    async def test_staying_in_the_new_language_after_the_question_switches(self):
        follower = _follower(initial_language="hi")
        await _hear(follower, LONG, "en", times=CONFIRMATIONS_BEFORE_SWITCH)

        assert self._switched(follower), "a caller who stayed has answered"
        assert follower.current_language == "en"

    async def test_answering_in_the_old_language_switches_nothing(self):
        """ "No, Hindi is fine" is said in Hindi, which resets the run."""
        follower = _follower(initial_language="hi")
        await _hear(follower, LONG, "en", times=CONFIRMATIONS_BEFORE_ASKING)
        await _hear(follower, LONG_HI, "hi")
        await _hear(follower, LONG, "en")

        assert self._switched(follower) == []
        assert follower.current_language == "hi"

    async def test_the_question_is_asked_once_per_language(self):
        """A caller who declined once is not asked again every two turns."""
        follower = _follower(initial_language="hi")
        await _hear(follower, LONG, "en", times=CONFIRMATIONS_BEFORE_ASKING)
        await _hear(follower, LONG_HI, "hi")
        await _hear(follower, LONG, "en", times=CONFIRMATIONS_BEFORE_ASKING)

        assert len(await self._asked_for(follower)) == 1

    async def test_a_language_the_agent_does_not_speak_is_never_asked_about(self):
        follower = _follower(initial_language="hi", allowed=frozenset({"hi", "en"}))
        await _hear(follower, LONG_TA, "ta", times=CONFIRMATIONS_BEFORE_SWITCH)

        assert await self._asked_for(follower) == []
        assert self._switched(follower) == []


@pytest.mark.asyncio
class TestItDoesNotAskAboutASwitchAlreadyMade:
    """Run 303, and the moment the call stopped sounding like a person:

        agent   yes, we can speak Tamil. what do you need?
        caller  I need to book an appointment
        agent   shall we continue in Tamil?

    Two followers watch language. This one watches the caller and asks after
    two turns. The spoken-language one moves the voice the instant the model
    writes in another script, with no confirmations, because the text is not a
    guess. Nothing joined them, so this one asked permission for something the
    agent had already done -- and ignored the caller's actual request to do it.
    """

    def _asks(self, follower):
        return [
            call.args[0]
            for call in follower.push_frame.await_args_list
            if isinstance(call.args[0], LLMMessagesAppendFrame)
        ]

    async def test_the_question_is_dropped_once_the_voice_has_moved(self):
        follower = _follower(initial_language="en-IN")
        follower.note_voice_moved("ta")

        await _hear(follower, LONG_TA, "ta", times=CONFIRMATIONS_BEFORE_ASKING)

        assert self._asks(follower) == []

    async def test_without_the_signal_it_still_asks(self):
        """The guard must not disable the feature it is narrowing."""
        follower = _follower(initial_language="en-IN")

        await _hear(follower, LONG_TA, "ta", times=CONFIRMATIONS_BEFORE_ASKING)

        assert self._asks(follower), "two Tamil turns should still raise the question"

    async def test_it_adopts_the_language_the_voice_moved_to(self):
        follower = _follower(initial_language="en-IN")

        follower.note_voice_moved("ta")

        assert follower.current_language == "ta"

    async def test_a_move_to_the_language_it_already_had_changes_nothing(self):
        follower = _follower(initial_language="ta-IN")
        follower.note_voice_moved("ta")

        assert follower.current_language == "ta"

    @pytest.mark.parametrize("value", [None, "", "multi", "auto", "unknown"])
    async def test_a_non_language_is_ignored(self, value):
        """The vendors' ways of saying "nothing was pinned". Adopting one would
        make every later reply look like a change away from it."""
        follower = _follower(initial_language="en-IN")

        follower.note_voice_moved(value)

        assert follower.current_language == "en"
