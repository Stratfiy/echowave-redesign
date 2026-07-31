"""Follow the caller's language, and switch the agent's voice to match.

An agent is configured with one language. A caller in India frequently is not:
they open in English, switch to Hindi when the conversation gets substantive,
and mix both in a sentence. A fixed-language agent answers the whole call in the
language it was configured with, which is the single most common reason a voice
agent gets hung up on here.

The detection already exists and nothing was reading it. Deepgram runs in
``multi`` mode by default, so every ``TranscriptionFrame`` has carried a
detected ``language`` since before any of this — it just went nowhere. This
processor sits directly after STT, watches that field, and pushes a
``TTSUpdateSettingsFrame`` downstream when the caller has genuinely changed
language.

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

from pipecat.frames.frames import Frame, TranscriptionFrame, TTSUpdateSettingsFrame
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.services.settings import TTSSettings

#: Consecutive turns in a new language before the voice follows. One is noise —
#: a single "yes" mis-detected as English inside a Hindi call must not flip the
#: agent. Two in a row is a conversation that actually moved.
CONFIRMATIONS_BEFORE_SWITCH = 2

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


class LanguageFollower(FrameProcessor):
    """Switch the agent's voice when the caller changes language.

    Args:
        initial_language: What the agent was configured to speak. A first turn
            in this language is not a change.
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
        on_switch: Callable[[str | None, str], Awaitable[None]] | None = None,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self._current = primary_subtag(initial_language)
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

        if self._agreements < CONFIRMATIONS_BEFORE_SWITCH:
            return

        await self._switch_to(detected)

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

        if self._on_switch is not None:
            try:
                await self._on_switch(previous, language)
            except Exception as exc:  # noqa: BLE001 - observability is not the call
                logger.error("Language switch callback failed: {}", exc)

    @property
    def current_language(self) -> str | None:
        return self._current
