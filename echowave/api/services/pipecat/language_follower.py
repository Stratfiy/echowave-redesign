"""Follow the caller's language: tell the model, and switch the voice to match.

An agent is configured with one language. A caller in India frequently is not:
they open in English, switch to Hindi when the conversation gets substantive,
and mix both in a sentence. A fixed-language agent answers the whole call in the
language it was configured with, which is the single most common reason a voice
agent gets hung up on here.

The detection already exists and nothing was reading it. Deepgram runs in
``multi`` mode by default, so every ``TranscriptionFrame`` has carried a
detected ``language`` since before any of this — it just went nowhere. This
processor sits directly after STT, watches that field, and when the caller has
genuinely changed language pushes two things downstream: a
``TTSUpdateSettingsFrame`` so the voice follows, and an
``LLMMessagesAppendFrame`` so the model does. Both halves are needed. The voice
alone produced the worst outcome of the three -- the model kept answering in the
language its prompt was written in and the new voice read that text out, so a
caller who moved to Tamil got English sentences in a Tamil voice.

**The whole difficulty is deciding when a change is genuine.** Speech
recognition mis-detects language constantly on short utterances — "hmm",
"okay", "achha" are near-coin-flips — and an agent that changes voice on each
one is far worse than one that never changes at all. Three rules do the work:

* **Compare primary subtags only.** ``hi`` and ``hi-IN`` are the same language.
  Treating them as different produces a voice change on every other turn.
* **Require consecutive confirmations.** One turn in a new language is noise;
  two in a row is a conversation that moved. This is the parameter that decides
  whether the feature is usable, and it is deliberately not configurable per
  call — an operator tuning it in production is a sign it is wrong here.
* **Ask before committing.** Two turns is enough to raise the question and not
  enough to answer it. At two the agent *asks*, in the new language, whether to
  continue in it, and goes on answering in the old one; only a caller who
  stays in the new language after being asked moves it. A caller who wanted
  English says so — in English — which resets the run and no switch happens.
  Silently switching on a guess is what this used to do, and it fought the
  operator's own prompt: every one of these agents says "change language only
  if the caller asks you to, in words", and the switch instruction was later
  and more specific, so it won. The agent drifted into Hindi mid-call against
  its own instructions, which is exactly what a tester reported.
* **Ignore short utterances entirely.** They are both the least reliable
  detections and the most frequent, so they dominate the error rate while
  carrying almost no information.

Realtime speech-to-speech models are excluded, and not because it is hard:
Gemini Live and its peers hear the caller's audio directly and answer in the
language they hear, natively. Pushing TTS settings at a model that generates its
own speech would do nothing, and pinning a language on one would make it *worse*
at exactly what it is already good at.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from loguru import logger

from pipecat.frames.frames import (
    Frame,
    LLMMessagesAppendFrame,
    TranscriptionFrame,
    TTSUpdateSettingsFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.services.settings import TTSSettings

#: Consecutive turns in a new language before the agent *asks* whether to
#: change. One is noise — a single "yes" mis-detected as English inside a Hindi
#: call must not flip the agent. Two in a row is worth a question.
CONFIRMATIONS_BEFORE_ASKING = 2

#: Consecutive turns before the voice actually follows. One more than the ask,
#: so the caller has heard the question and stayed in the new language anyway.
#: A caller who did not want to change answers in the language they were
#: already speaking, which resets the run and switches nothing.
CONFIRMATIONS_BEFORE_SWITCH = 3

#: Utterances shorter than this are ignored for detection. Backchannel — "hmm",
#: "ok", "achha" — is where language detection is least reliable and most
#: frequent, so it would dominate the error rate while carrying no information.
MIN_CHARS_FOR_DETECTION = 12

#: Values that sit in a provider's language field without being languages.
#: Decibyl-managed STT defaults to ``multi`` and several providers offer
#: ``auto``; both mean "work it out from the audio". Treating one as a
#: configured language makes the first two genuine turns look like a change.
NOT_A_LANGUAGE = frozenset({"multi", "auto"})


def primary_subtag(language) -> str | None:
    """``hi-IN`` and ``hi`` are the same language for this purpose.

    Providers disagree about how specific a tag they return, and the same
    provider varies within one call. Comparing full tags means switching the
    voice between two spellings of Hindi.
    """
    if language is None:
        return None
    raw = getattr(language, "value", language)
    if not isinstance(raw, str) or not raw.strip():
        return None
    return raw.strip().lower().split("-")[0] or None


def configured_language(tts_config, stt_config) -> str | None:
    """The language the agent was set up to speak, if it was set up with one.

    Reads TTS first and falls back to STT, because the two disagree about where
    the setting lives and the disagreement covers the common cases rather than
    the exotic ones: ElevenLabs' TTS config has no language field at all, and
    the Decibyl-managed path carries the operator's choice on STT. Neither
    carrying one is a normal state, not a misconfiguration — it means nothing
    was pinned, so the caller decides and the first turn heard is the baseline.
    """
    for config in (tts_config, stt_config):
        tag = primary_subtag(getattr(config, "language", None))
        if tag is not None and tag not in NOT_A_LANGUAGE:
            return tag
    return None


#: What to call a language when instructing the model. A model told to answer
#: in "ta" is being asked to guess; told to answer in Tamil it does not have to.
#: Anything not listed falls back to the tag itself, which is still a better
#: instruction than none.
LANGUAGE_NAMES = {
    "as": "Assamese",
    "bn": "Bengali",
    "en": "English",
    "gu": "Gujarati",
    "hi": "Hindi",
    "kn": "Kannada",
    "ml": "Malayalam",
    "mr": "Marathi",
    "ne": "Nepali",
    "or": "Odia",
    "pa": "Punjabi",
    "sa": "Sanskrit",
    "ta": "Tamil",
    "te": "Telugu",
    "ur": "Urdu",
}


def language_name(tag: str) -> str:
    """The human name for a primary subtag, for putting in a prompt."""
    return LANGUAGE_NAMES.get(tag, tag)


def ask_instruction(current: str | None, language: str) -> str:
    """What the model is told when the caller *may* have changed language.

    It asks and keeps answering in the language it was already speaking. Both
    halves matter: an agent that asks the question in the old language is
    asking somebody who may not understand it, and an agent that switches
    while asking has not really asked.
    """
    name = language_name(language)
    staying = language_name(current) if current else None
    keep = f" Keep answering in {staying} until they say yes." if staying else ""
    return (
        f"The caller may have moved to {name}. In your next turn, before "
        f"anything else, ask them in {name} — one short sentence — whether "
        f"they would like to continue in {name}. Ask once and do not repeat "
        f"the question later in the call.{keep}"
    )


def switch_instruction(language: str) -> str:
    """What the model is told when the caller changes language.

    Stated as the caller's own doing rather than as a style note, because a
    model that is merely told a preference argues with it: it answers the first
    turn in the new language, then drifts back to whatever the agent's prompt
    is written in.
    """
    name = language_name(language)
    return (
        f"The caller has switched to {name}. Reply only in {name} from now on, "
        f"and keep doing so for the rest of the call unless they change again. "
        f"Do not explain the switch or apologise for it."
    )


class LanguageFollower(FrameProcessor):
    """Switch the agent's voice when the caller changes language.

    Args:
        initial_language: What the agent was configured to speak. A first turn
            in this language is not a change.
        allowed: Languages this agent is permitted to speak, as primary subtags.
            A detection outside this set is ignored entirely: it does not
            switch, and it does not even count towards a switch. None allows
            any language, which is how every agent behaved before operators
            could declare a set.
        voice_for_language: Optional map of primary subtag to voice id. Some
            providers ship genuinely multilingual voices (ElevenLabs multilingual,
            Sarvam's Bulbul) where only the language needs to change and the
            voice should stay put — for those, leave this empty. Others key the
            voice to the language, and switching one without the other produces
            an English voice reading Hindi phonetically.
        on_switch: Called with (from, to) after a switch is pushed. Used for
            observability; failures here never affect the call.
    """

    def __init__(
        self,
        *,
        initial_language: str | None = None,
        voice_for_language: dict[str, str] | None = None,
        allowed: frozenset[str] | None = None,
        on_switch: Callable[[str | None, str], Awaitable[None]] | None = None,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self._current = primary_subtag(initial_language)
        #: Languages this agent may answer in, or None for any. A detection
        #: outside the set is noise, not a language change -- see
        #: language_following.allowed_languages for what that cost.
        self._allowed = allowed
        self._voices = {
            primary_subtag(k) or k: v for k, v in (voice_for_language or {}).items()
        }
        self._on_switch = on_switch

        #: The language being considered, and how many consecutive turns have
        #: agreed on it. Reset the moment a turn disagrees — confirmations must
        #: be consecutive, not cumulative, or a caller alternating between two
        #: languages would eventually trip a switch in both.
        self._candidate: str | None = None
        self._agreements = 0

        #: Languages the caller has already been asked about, so the question
        #: is put once per call and not on every run of two turns. A caller
        #: who said no to Hindi and drifts back into it is not asked again;
        #: they can still ask in words, which the prompt already honours.
        self._asked: set[str] = set()

        #: Every language heard, in order of first appearance. Read at teardown:
        #: "which languages did this call actually involve" is not answerable
        #: from a single column, and it is the question asked when a campaign
        #: underperforms in one region.
        self.languages_heard: list[str] = []

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if isinstance(frame, TranscriptionFrame):
            await self._observe(frame)

        # Always forwarded, whatever was decided. This processor observes; it
        # must never be the reason a transcription fails to reach the LLM.
        await self.push_frame(frame, direction)

    async def _observe(self, frame: TranscriptionFrame) -> None:
        detected = primary_subtag(getattr(frame, "language", None))
        if detected is None:
            return

        if detected not in self.languages_heard:
            self.languages_heard.append(detected)

        if self._allowed is not None and detected not in self._allowed:
            # Recorded above, because "what did recognition think it heard" is
            # worth knowing when a caller complains. Not acted on: an agent
            # whose operator listed Tamil, Kannada, English and Hindi must not
            # be talked into Telugu by one bad guess on a short utterance.
            logger.debug(
                "Ignoring detected language {} — not one this agent speaks ({})",
                detected,
                ", ".join(sorted(self._allowed)),
            )
            return

        text = (getattr(frame, "text", "") or "").strip()
        if len(text) < MIN_CHARS_FOR_DETECTION:
            # Deliberately does not reset the candidate: a short interjection in
            # the middle of a genuine switch should neither confirm nor deny it.
            return

        if self._current is None:
            # Nothing configured to compare against — adopt what was heard as
            # the baseline rather than treating the first turn as a change.
            self._current = detected
            return

        if detected == self._current:
            self._candidate = None
            self._agreements = 0
            return

        if detected == self._candidate:
            self._agreements += 1
        else:
            self._candidate = detected
            self._agreements = 1

        if self._agreements == CONFIRMATIONS_BEFORE_ASKING:
            await self._ask_about(detected)
            return

        if self._agreements < CONFIRMATIONS_BEFORE_SWITCH:
            return

        await self._switch_to(detected)

    async def _ask_about(self, language: str) -> None:
        """Put the question, once, and go on speaking as before.

        No TTS change: the agent is still answering in the language it was,
        and the question rides in that voice. The only thing pushed is an
        instruction to the model.
        """
        if language in self._asked:
            return
        self._asked.add(language)
        logger.info(
            "Caller may have moved {} → {}; asking before following",
            self._current,
            language,
        )
        await self.push_frame(
            LLMMessagesAppendFrame(
                messages=[
                    {
                        "role": "system",
                        "content": ask_instruction(self._current, language),
                    }
                ],
                run_llm=False,
            ),
            FrameDirection.DOWNSTREAM,
        )

    async def _switch_to(self, language: str) -> None:
        previous = self._current
        self._current = language
        self._candidate = None
        self._agreements = 0

        # A typed delta rather than the settings dict: unset fields stay
        # NOT_GIVEN, so a provider with a multilingual voice keeps the voice it
        # has instead of having it cleared. The dict form is also deprecated and
        # goes away in pipecat 2.0.
        voice = self._voices.get(language)
        delta = (
            TTSSettings(language=language)
            if not voice
            else TTSSettings(language=language, voice=voice)
        )

        logger.info(
            "Caller switched language {} → {}; following{}",
            previous,
            language,
            f" with voice {voice}" if voice else "",
        )
        await self.push_frame(
            TTSUpdateSettingsFrame(delta=delta), FrameDirection.DOWNSTREAM
        )

        # Switching the voice alone was half a feature, and the visible half was
        # the wrong one. Nothing ever told the language model, so it kept
        # answering in whatever language its prompt was written in and the new
        # voice read that text out: an English sentence spoken by a Tamil voice,
        # which is worse than never switching at all. The instruction goes into
        # the context rather than being spoken, and run_llm is False because
        # this rides just ahead of the caller's own transcription — that turn is
        # what the model should answer, in the language it has just been given.
        await self.push_frame(
            LLMMessagesAppendFrame(
                messages=[{"role": "system", "content": switch_instruction(language)}],
                run_llm=False,
            ),
            FrameDirection.DOWNSTREAM,
        )

        if self._on_switch is not None:
            try:
                await self._on_switch(previous, language)
            except Exception as exc:  # noqa: BLE001 - observability is not the call
                logger.error("Language switch callback failed: {}", exc)

    @property
    def current_language(self) -> str | None:
        return self._current
