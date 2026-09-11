"""Speak the reply in the language the reply is written in.

A cascade agent decides its words in one place and says them in another, and
nothing connected the two. The language model answers a Tamil caller in Tamil;
the speech service is still configured with whatever language the agent was set
up with, and reads that Tamil out under a Hindi language code. A tester put it
plainly: *"it reads digits in Hindi"*.

This is not the same problem as following the caller, and it is not fixed by
:mod:`api.services.pipecat.language_follower`. That one watches the *caller*,
deliberately waits for three consecutive turns before committing, and ignores
utterances under twelve characters -- all correct, because switching a call on
one mis-detected "hmm" is worse than never switching. But a caller answering
"Tamil" to the greeting's own question is a short utterance, so the follower
never counts it, while the model reads the same answer and switches
immediately. From that turn on the two halves disagree, and the caller hears the
disagreement.

There is nothing to guess about here. The text is already written. If it is in
Tamil script, it will be spoken, and the only right language code for speaking
it is Tamil. So this processor sits between the model and the voice, reads the
script of what is about to be said, and tells the voice.

**Only non-Latin scripts count.** Romanised Hindi -- "haan, TT number confirm
karo" -- is Latin, and a rule that read it as English would flap between two
languages inside a single Hinglish sentence for no gain. Latin text is left
alone entirely, which also means an English agent never sees this processor do
anything. What is left is unambiguous: a Devanagari sentence is not English, a
Tamil sentence is not Hindi, and no amount of confidence intervals makes it so.

Why this is on for every cascade call rather than behind the follow-the-caller
switch: the switch asks "may this agent change language?", and the answer here
is that it already did -- in the model, a turn ago. Refusing to move the voice
does not keep the agent in one language, it only makes it unintelligible in the
one it moved to. The processor is silent for every agent whose voice already
matches its words, which is every agent that is not currently broken.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from loguru import logger

from api.services.pipecat.language_follower import NOT_A_LANGUAGE, primary_subtag
from pipecat.frames.frames import (
    Frame,
    InterimTranscriptionFrame,
    LLMFullResponseStartFrame,
    TextFrame,
    TranscriptionFrame,
    TTSSpeakFrame,
    TTSUpdateSettingsFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.services.settings import TTSSettings

#: Unicode blocks, and the language a run of one means. Every Indian script
#: here is used by one major language each, with the exception noted below.
#:
#: Devanagari is the exception: Hindi and Marathi share it, and no amount of
#: character inspection separates them. It resolves to Hindi unless the agent
#: is already speaking Marathi, in which case Marathi text is what a Marathi
#: agent was always going to produce and moving it to Hindi would be the bug.
_SCRIPTS: tuple[tuple[int, int, str], ...] = (
    (0x0900, 0x097F, "hi"),  # Devanagari — Hindi, Marathi
    (0x0980, 0x09FF, "bn"),  # Bengali
    (0x0A00, 0x0A7F, "pa"),  # Gurmukhi
    (0x0A80, 0x0AFF, "gu"),  # Gujarati
    (0x0B00, 0x0B7F, "or"),  # Odia
    (0x0B80, 0x0BFF, "ta"),  # Tamil
    (0x0C00, 0x0C7F, "te"),  # Telugu
    (0x0C80, 0x0CFF, "kn"),  # Kannada
    (0x0D00, 0x0D7F, "ml"),  # Malayalam
)

#: Characters of one script before the reply is called that language. Four,
#: because the decision has to land inside the first streamed token or two --
#: the voice service starts synthesising a sentence at a time, and a language
#: that arrives after the sentence does is a language that arrives too late.
#: It is set this low safely only because the signal is a script and not a
#: guess: four Tamil letters are four Tamil letters.
MIN_SCRIPT_CHARS = 4

#: Devanagari belongs to whichever of these the agent is already speaking.
_DEVANAGARI_KEEP = frozenset({"hi", "mr"})


def script_language(text: str, *, current: str | None = None) -> str | None:
    """The language of ``text`` when its script says so, else ``None``.

    ``None`` for Latin text, for punctuation and digits, and for anything with
    less than :data:`MIN_SCRIPT_CHARS` of a single Indian script -- all of
    which mean "this tells us nothing", never "this is English".
    """
    counts: dict[str, int] = {}
    for character in text:
        code = ord(character)
        for start, end, language in _SCRIPTS:
            if start <= code <= end:
                counts[language] = counts.get(language, 0) + 1
                break

    if not counts:
        return None

    language, seen = max(counts.items(), key=lambda item: item[1])
    if seen < MIN_SCRIPT_CHARS:
        return None
    if language == "hi" and current in _DEVANAGARI_KEEP:
        return current
    return language


class SpokenLanguageFollower(FrameProcessor):
    """Point the voice at the language of the words it is about to speak.

    Sits after the language model and before the speech service, so the
    settings frame it pushes is ahead of the text that prompted it in the same
    queue -- the voice is told before it synthesises, not after.

    Args:
        initial_language: The language the voice is configured with, as a
            primary subtag. Text in this language changes nothing.
        allowed: Languages this agent may speak, as primary subtags. A reply
            outside the set is left to the voice's own configuration rather
            than pointing it at a language the operator never sanctioned --
            and, for a managed tier, possibly one the vendor cannot speak.
            ``None`` allows any.
        on_change: Called with (from, to) after a change is pushed. For
            observability only; a failure here never affects the call.
    """

    def __init__(
        self,
        *,
        initial_language: str | None = None,
        allowed: frozenset[str] | None = None,
        on_change: Callable[[str | None, str], Awaitable[None]] | None = None,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self._current = _configured(initial_language)
        self._allowed = allowed
        self._on_change = on_change

        #: What this reply has said so far. A reply arrives as a stream of
        #: tokens, and the first of them is routinely one character of
        #: punctuation, so the decision is made on the run rather than on
        #: whichever fragment happens to arrive first.
        self._buffer = ""
        #: Set once this reply's language is settled, so a long answer pushes
        #: one settings frame and not one per token.
        self._decided = False

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        # A new reply starts a new decision: an agent that answered in Tamil
        # last turn and in English this one has to be able to move back.
        if isinstance(frame, LLMFullResponseStartFrame):
            self._buffer = ""
            self._decided = False

        # The caller's own words also travel downstream, and they are not what
        # is about to be spoken. Following them is a different decision, made
        # with a deliberately higher bar, and it is made by LanguageFollower.
        elif isinstance(frame, (TranscriptionFrame, InterimTranscriptionFrame)):
            pass

        elif isinstance(frame, (TextFrame, TTSSpeakFrame)):
            await self._observe(getattr(frame, "text", "") or "")

        await self.push_frame(frame, direction)

    async def _observe(self, text: str) -> None:
        if self._decided or not text:
            return

        # Bounded: a decision that has not been reached in a few hundred
        # characters of one reply is not going to be, and an unbounded buffer
        # on a long call is a leak with no upside.
        self._buffer = (self._buffer + text)[-512:]

        language = script_language(self._buffer, current=self._current)
        if language is None:
            return

        self._decided = True
        if language == self._current:
            return

        if self._allowed is not None and language not in self._allowed:
            logger.warning(
                "The reply is in {} but this agent is set up for {}; leaving the "
                "voice as it is. It will read {} text under a {} language code.",
                language,
                ", ".join(sorted(self._allowed)),
                language,
                self._current or "default",
            )
            return

        previous = self._current
        self._current = language
        logger.info(
            "Reply is written in {}; pointing the voice at it (was {})",
            language,
            previous or "the configured default",
        )
        await self.push_frame(
            TTSUpdateSettingsFrame(delta=TTSSettings(language=language)),
            FrameDirection.DOWNSTREAM,
        )

        if self._on_change is not None:
            try:
                await self._on_change(previous, language)
            except Exception as exc:  # noqa: BLE001 - observability is not the call
                logger.error("Spoken-language callback failed: {}", exc)

    @property
    def current_language(self) -> str | None:
        return self._current


def _configured(language) -> str | None:
    """The voice's language as a bare subtag, or ``None`` for "not pinned".

    ``unknown``, ``multi`` and ``auto`` are the vendors' ways of saying nothing
    was pinned. Carrying one as if it were a language would make every reply
    look like a change from it.
    """
    tag = primary_subtag(language)
    return None if tag is None or tag in NOT_A_LANGUAGE else tag
