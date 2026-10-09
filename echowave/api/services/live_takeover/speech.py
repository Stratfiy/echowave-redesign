"""The supervisor's words: transcribed, labelled, and read for the agent's name.

While a supervisor speaks into a call from the browser, their microphone is
transcribed by a second copy of the call's own speech-to-text -- the same
provider and settings, made by the same factory -- running in a small
pipeline of its own. Their audio never goes through the call's transcriber,
so their words can never be taken for the caller's: they are never a user
turn, never trip the caller's interruption logic, and the transcript says
who spoke.

``SupervisorTranscriber`` starts on the first slice of the supervisor's
voice and stops when they leave the call. The controller tells it when a
stretch of speech starts and stops (its own loudness check), as voice
activity frames, so a transcriber that works in segments gets segments.

``addressed`` decides whether a line was said *to the agent*: the plan's
rule is that while a person is on the call the agent speaks only when
addressed, or when "Let the agent answer" is clicked. Addressed means the
agent's name used to call on it ("Asha, what's the refund window?") or in a
clear question or request to it, or a plain "assistant"/"AI" used the same
way. A name merely mentioned ("I'll check with Asha later") is not.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Awaitable, Callable, Iterable
from typing import Any

from loguru import logger
from pipecat.frames.frames import (
    Frame,
    InputAudioRawFrame,
    InterimTranscriptionFrame,
    TranscriptionFrame,
    VADUserStartedSpeakingFrame,
    VADUserStoppedSpeakingFrame,
)
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.workers.runner import WorkerRunner

#: The rate the supervisor's audio is given to the transcriber at.
DEFAULT_SAMPLE_RATE = 16000

#: Words that call on the agent without its name, only when used to call on
#: it (first, or last after a comma): "AI, can you answer that?".
GENERIC_NAMES = ("assistant", "ai", "bot")
#: Opening words of a question or a request to someone.
_ASKING = frozenset(
    {
        "what",
        "when",
        "where",
        "who",
        "why",
        "how",
        "which",
        "can",
        "could",
        "would",
        "will",
        "do",
        "does",
        "did",
        "is",
        "are",
        "should",
        "please",
        "tell",
        "answer",
        "explain",
        "check",
        "confirm",
        "go",
    }
)
#: First words of an agent's name too ordinary to listen for on their own
#: ("Front desk" is not called "front").
_ORDINARY = frozenset(
    {
        "the",
        "my",
        "our",
        "front",
        "main",
        "sales",
        "support",
        "customer",
        "help",
        "desk",
        "agent",
        "new",
        "test",
    }
)


def names_for(agent_name: str | None) -> tuple[str, ...]:
    """What the agent may be called: its whole name and, when it is not an
    ordinary word, the first word of it ("Asha" for "Asha Receptionist")."""
    whole = " ".join(str(agent_name or "").split()).strip()
    if not whole:
        return ()
    out = [whole.lower()]
    first = re.sub(r"[^\w]", "", whole.split()[0]).lower()
    if len(first) >= 3 and first not in _ORDINARY and first != out[0]:
        out.append(first)
    return tuple(out)


def _clean(text: str) -> str:
    text = " ".join(str(text or "").lower().split())
    return re.sub(r"[^\w\s,?'-]", "", text).strip()


def _is_asking(text: str) -> bool:
    """A question, or a request that opens with what is asked for."""
    if text.endswith("?"):
        return True
    words = re.findall(r"[a-z]+", text)
    return bool(words) and words[0] in _ASKING


def _called_on(text: str, name: str) -> bool:
    """``name`` used to call on someone: first in the line ("Asha, ...",
    "Okay Asha ..."), or last after a comma ("..., Asha?")."""
    n = re.escape(name)
    return bool(
        re.match(rf"^(hey |ok |okay |so |and )?{n}\b", text)
        or re.search(rf",\s*{n}\s*[?]?$", text)
    )


def addressed(text: str, names: Iterable[str]) -> bool:
    """Whether the supervisor said this to the agent."""
    cleaned = _clean(text)
    if not cleaned:
        return False
    for name in names:
        name = _clean(name)
        if not name or not re.search(rf"\b{re.escape(name)}\b", cleaned):
            continue
        if _called_on(cleaned, name) or _is_asking(cleaned):
            return True
    return any(_called_on(cleaned, generic) for generic in GENERIC_NAMES)


class _Collector(FrameProcessor):
    """The end of the transcriber's pipeline: hands the words over."""

    def __init__(self, on_text: Callable[[str, bool], Awaitable[None]]):
        super().__init__()
        self._on_text = on_text

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, (TranscriptionFrame, InterimTranscriptionFrame)):
            text = (frame.text or "").strip()
            if text:
                try:
                    await self._on_text(text, isinstance(frame, TranscriptionFrame))
                except Exception as exc:  # noqa: BLE001 - words lost, call unharmed
                    logger.warning("Supervisor transcript not delivered: {}", exc)
            return
        await self.push_frame(frame, direction)


class SupervisorTranscriber:
    """A second transcriber for the supervisor's voice, in its own pipeline."""

    def __init__(
        self,
        *,
        make_stt: Callable[[], FrameProcessor],
        on_text: Callable[[str, bool], Awaitable[None]],
        sample_rate: int = DEFAULT_SAMPLE_RATE,
    ):
        self._make_stt = make_stt
        self._on_text = on_text
        self.sample_rate = int(sample_rate or DEFAULT_SAMPLE_RATE)
        self._worker: PipelineWorker | None = None
        self._running: asyncio.Task | None = None
        self._resampler: Any = None
        self.failed = False
        self.fed = 0

    @property
    def started(self) -> bool:
        return self._worker is not None

    async def _start(self) -> bool:
        if self._worker is not None:
            return True
        if self.failed:
            return False
        try:
            pipeline = Pipeline([self._make_stt(), _Collector(self._on_text)])
            self._worker = PipelineWorker(
                pipeline,
                params=PipelineParams(audio_in_sample_rate=self.sample_rate),
                cancel_on_idle_timeout=False,
                idle_timeout_secs=None,
                enable_rtvi=False,
                enable_turn_tracking=False,
                check_dangling_tasks=False,
                name="supervisor-transcriber",
            )
            runner = WorkerRunner(handle_sigint=False)
            await runner.add_workers(self._worker)
            self._running = asyncio.create_task(runner.run())
            return True
        except Exception as exc:  # noqa: BLE001 - untranscribed, still heard
            logger.warning("Supervisor transcriber did not start: {}", exc)
            self.failed = True
            self._worker = None
            return False

    async def _queue(self, frame: Frame) -> None:
        if self._worker is None:
            return
        try:
            await self._worker.queue_frame(frame)
        except Exception as exc:  # noqa: BLE001
            logger.debug("Supervisor transcriber dropped a frame: {}", exc)

    async def audio(self, pcm: bytes, rate: int, channels: int) -> None:
        """One slice of the supervisor's voice."""
        if not pcm or channels != 1 or not await self._start():
            return
        if rate != self.sample_rate:
            if self._resampler is None:
                from pipecat.audio.utils import create_stream_resampler

                self._resampler = create_stream_resampler()
            pcm = await self._resampler.resample(pcm, rate, self.sample_rate)
        self.fed += 1
        await self._queue(
            InputAudioRawFrame(audio=pcm, sample_rate=self.sample_rate, num_channels=1)
        )

    async def speaking(self, started: bool) -> None:
        """A stretch of the supervisor speaking starts or ends."""
        if self._worker is None:
            return
        await self._queue(
            VADUserStartedSpeakingFrame() if started else VADUserStoppedSpeakingFrame()
        )

    async def close(self) -> None:
        """Stop transcribing. Safe to call twice; never raises."""
        worker, running = self._worker, self._running
        self._worker = None
        self._running = None
        if worker is None:
            return
        try:
            await worker.cancel()
        except Exception as exc:  # noqa: BLE001
            logger.debug("Supervisor transcriber did not cancel cleanly: {}", exc)
        if running is not None:
            try:
                await asyncio.wait_for(running, 5)
            except (TimeoutError, asyncio.CancelledError, Exception):  # noqa: BLE001
                running.cancel()


__all__ = ["SupervisorTranscriber", "addressed", "names_for"]
