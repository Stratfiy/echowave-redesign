"""Only the caller can interrupt the agent.

A phone test showed it plainly: whenever somebody near the caller spoke, the
agent stopped mid-sentence. ``vad_sensitivity`` explains why nothing already
shipped can fix that -- RNNoise preserves speech, and other people talking *is*
speech; raising the VAD bar or the word count only narrows the gap. What every
background voice has in common is that it is not the caller's voice.

So this learns the caller's voice and asks one question when it matters: while
the agent is speaking and the transcriber has heard enough to interrupt, *does
the speech just heard sound like the caller?* If clearly not, the interruption
is dropped and the stranger's words with it.

**Learning the voice.** Whenever the agent is silent and the transcriber hears
words -- the caller answering the greeting, giving their name -- the loudest
frames of that stretch are pooled into a speaker embedding. Loudest, because
the caller is the one holding the phone: background voices arrive quieter and
mostly fall outside the band. The network is a WeSpeaker speaker-verification
model (``MODEL_LICENCE``) run by onnxruntime on CPU at 16 kHz, telephony audio
resampled up first as RNNoise's is.

**Everything here is biased towards behaving exactly as before.**

* Off unless the ``caller_voice_lock`` flag is on for the organisation.
* Until the caller has been enrolled, every interruption counts, as today.
* Too little speech to judge: it counts (after waiting up to half a second
  for more).
* Model file missing, or anything raises: it counts, and the log says so,
  because a lock that silently does nothing is the failure ``api/AGENTS.md``
  warns about.
* Speech at least as loud as the caller usually is counts whatever the score:
  a caller who is clearly, loudly on the line is never ignored.
* Only *interruptions while the agent speaks* are judged. When the agent is
  silent, the caller's first word starts the turn exactly as before.

What it cannot do: once the caller is genuinely speaking, anyone else in the
room still reaches the transcriber with them. This decides *who may
interrupt*; it does not separate voices.
"""

from __future__ import annotations

import asyncio
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar

import numpy as np
from loguru import logger

from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    Frame,
    InputAudioRawFrame,
    InterimTranscriptionFrame,
    TranscriptionFrame,
    UserStoppedSpeakingFrame,
    VADUserStoppedSpeakingFrame,
)
from pipecat.turns.types import ProcessFrameResult
from pipecat.turns.user_start.base_user_turn_start_strategy import (
    BaseUserTurnStartStrategy,
    UserTurnStartedParams,
)

#: The flag in ``services/features.py``.
FEATURE = "caller_voice_lock"

#: Where the model is looked for when ``CALLER_VOICE_LOCK_MODEL_PATH`` is unset.
#: The Dockerfile's ``WITH_VOICE_ISOLATION`` build argument puts it here.
DEFAULT_MODEL_PATH = "/app/models/voice_isolation/wespeaker_resnet34_lm.onnx"

MODEL_LICENCE = (
    "WeSpeaker ResNet34-LM (voxceleb_resnet34_LM.onnx, huggingface.co/Wespeaker/"
    "wespeaker-voxceleb-resnet34-LM): WeSpeaker code Apache-2.0; model weights "
    "CC BY 4.0, trained on VoxCeleb2 (CC BY 4.0). Commercial use permitted "
    "with attribution."
)

#: The network's own rate. Everything is resampled to it before features.
MODEL_RATE = 16000

#: Cosine similarity below which speech is judged not to be the caller.
#: Chosen from ``evals/voice_isolation`` -- see its README for the sweep.
DEFAULT_THRESHOLD = 0.30

#: Seconds of the caller's voice before the lock does anything at all. Two
#: seconds of the loud end of one answer -- one caller turn is usually enough.
DEFAULT_ENROL_SECS = 2.0

#: The profile keeps improving from matching speech up to this much.
MAX_ENROL_SECS = 12.0

#: The judged window: the last this-many seconds of input when an
#: interruption is proposed. Long enough for a stable embedding, short enough
#: to be mostly the speech that produced the words.
WINDOW_SECS = 1.5

#: Less voiced audio than this in the window and there is nothing to judge.
MIN_VERIFY_SECS = 0.6

#: Longest a proposed interruption waits for enough speech to be judged.
MAX_DEFER_SECS = 0.5

#: A window at least this loud relative to the caller's enrolled level counts
#: whatever its score. 0 dB is "as loud as the caller usually is".
DEFAULT_LOUD_MARGIN_DB = 0.0

#: A refusal is re-judged only after this much new audio, so a transcriber
#: that sends an interim every 100 ms does not cost an inference every 100 ms.
REJUDGE_SECS = 0.25

#: Frames are measured in 20 ms blocks, the grain the transports move audio in.
FRAME_SECS = 0.02

#: Below this a frame is line noise, not anybody's voice.
SILENCE_DBFS = -55.0

#: Within the judged window, a frame this far below the loudest is the gap
#: between words.
ACTIVE_RANGE_DB = 30.0

#: Enrolment keeps frames within this many dB of the stretch's loud end (its
#: 90th percentile): the caller at the handset, not the room behind them.
#: Narrower and a softly spoken caller never gathers enough to enrol from one
#: answer; the evaluation measured 10 dB failing on read speech.
ENROL_RANGE_DB = 15.0

#: At most this much of each silent stretch is learnt from, loudest first.
#: Three pieces, so one that disagrees with the other two can be dropped.
ENROL_TAKE_SECS = 4.5

#: Enrolment audio is embedded in pieces this long, so a piece that disagrees
#: with the rest can be left out.
ENROL_PIECE_SECS = 1.5

#: Bot-silent audio kept for enrolment between agent turns.
MAX_QUIET_SECS = 30.0


# ---------------------------------------------------------------------------
# Features: Kaldi-compatible log-mel filterbank, as WeSpeaker was trained on.
# ---------------------------------------------------------------------------

_FRAME_LENGTH = 400  # 25 ms at 16 kHz
_FRAME_SHIFT = 160  # 10 ms
_FFT = 512
_MEL_BINS = 80
_PREEMPH = 0.97


def _mel(freq):
    return 1127.0 * np.log(1.0 + np.asarray(freq) / 700.0)


def _mel_banks() -> np.ndarray:
    """Kaldi's triangular mel bank, ``[80, 257]``, from 20 Hz to Nyquist."""
    fft_bin_width = MODEL_RATE / _FFT
    mel_low, mel_high = _mel(20.0), _mel(MODEL_RATE / 2)
    delta = (mel_high - mel_low) / (_MEL_BINS + 1)
    bins = _mel(fft_bin_width * np.arange(_FFT // 2))
    banks = np.zeros((_MEL_BINS, _FFT // 2 + 1), dtype=np.float64)
    for i in range(_MEL_BINS):
        left = mel_low + i * delta
        center, right = left + delta, left + 2 * delta
        up = (bins - left) / (center - left)
        down = (right - bins) / (right - center)
        banks[i, : _FFT // 2] = np.maximum(0.0, np.minimum(up, down))
    return banks


_BANKS_T = _mel_banks().T
_WINDOW = np.hamming(_FRAME_LENGTH)
_EPS = float(np.finfo(np.float32).eps)


def fbank(samples: np.ndarray) -> np.ndarray:
    """80-dim log-mel features of 16 kHz float audio, mean-normalised.

    The same computation as ``torchaudio.compliance.kaldi.fbank`` the way
    WeSpeaker calls it (hamming window, no dither, input at 16-bit scale);
    ``test_caller_voice_lock`` pins it against reference values.
    """
    x = np.asarray(samples, dtype=np.float64) * 32768.0
    if x.size < _FRAME_LENGTH:
        return np.zeros((0, _MEL_BINS), dtype=np.float32)
    n_frames = 1 + (x.size - _FRAME_LENGTH) // _FRAME_SHIFT
    idx = (
        np.arange(_FRAME_LENGTH)[None, :] + _FRAME_SHIFT * np.arange(n_frames)[:, None]
    )
    frames = x[idx]
    frames = frames - frames.mean(axis=1, keepdims=True)
    previous = np.concatenate([frames[:, :1], frames[:, :-1]], axis=1)
    frames = (frames - _PREEMPH * previous) * _WINDOW
    power = np.abs(np.fft.rfft(frames, n=_FFT, axis=1)) ** 2
    feats = np.log(np.maximum(power @ _BANKS_T, _EPS))
    return (feats - feats.mean(axis=0, keepdims=True)).astype(np.float32)


# ---------------------------------------------------------------------------
# The embedder: one onnxruntime session per process, shared by every call.
# ---------------------------------------------------------------------------


def _to_model_rate(samples: np.ndarray, rate: int) -> np.ndarray:
    audio = samples.astype(np.float32) / 32768.0
    if rate == MODEL_RATE:
        return audio
    import soxr

    return soxr.resample(audio, rate, MODEL_RATE, quality="HQ").astype(np.float32)


def resolve_model_path() -> str:
    from api import constants

    return constants.CALLER_VOICE_LOCK_MODEL_PATH or DEFAULT_MODEL_PATH


class SpeakerEmbedder:
    """A speaker-verification network behind one method.

    ``embed`` takes int16 audio at any rate and returns a unit vector. Sessions
    are cached per path, so a worker holding forty calls loads the model once,
    and each run uses one thread: forty calls each grabbing every core is how
    a worker stops answering.
    """

    _sessions: ClassVar[dict[str, Any]] = {}
    _lock: ClassVar[threading.Lock] = threading.Lock()

    def __init__(self, model_path: str | None = None) -> None:
        self.model_path = model_path or resolve_model_path()

    @property
    def available(self) -> bool:
        return Path(self.model_path).is_file()

    def _session(self):
        with self._lock:
            session = self._sessions.get(self.model_path)
            if session is None:
                import onnxruntime as ort

                options = ort.SessionOptions()
                options.intra_op_num_threads = 1
                options.inter_op_num_threads = 1
                session = ort.InferenceSession(
                    self.model_path,
                    sess_options=options,
                    providers=["CPUExecutionProvider"],
                )
                self._sessions[self.model_path] = session
            return session

    def embed(self, samples: np.ndarray, sample_rate: int) -> np.ndarray:
        feats = fbank(_to_model_rate(samples, sample_rate))
        session = self._session()
        (emb,) = session.run(None, {session.get_inputs()[0].name: feats[None]})
        vector = emb[0].astype(np.float32)
        return vector / (np.linalg.norm(vector) + 1e-9)


def cosine(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.dot(a, b) / ((np.linalg.norm(a) * np.linalg.norm(b)) + 1e-9))


def frame_levels(samples: np.ndarray, frame_len: int) -> tuple[np.ndarray, np.ndarray]:
    """``samples`` cut into frames, and each frame's level in dBFS."""
    n = samples.size // frame_len
    frames = samples[: n * frame_len].reshape(n, frame_len).astype(np.float64)
    rms = np.sqrt(np.mean(frames**2, axis=1)) if n else np.zeros(0)
    levels = 20.0 * np.log10(np.maximum(rms, 1e-12) / 32768.0)
    return frames, levels


def active_frames(samples: np.ndarray, frame_len: int) -> tuple[np.ndarray, np.ndarray]:
    """The voiced frames of ``samples`` (gaps and line noise dropped), joined,
    and their levels."""
    frames, levels = frame_levels(samples, frame_len)
    if not len(levels):
        return np.zeros(0, dtype=np.int16), levels
    keep = (levels > SILENCE_DBFS) & (levels >= levels.max() - ACTIVE_RANGE_DB)
    return frames[keep].reshape(-1).astype(np.int16), levels[keep]


# ---------------------------------------------------------------------------
# The decision, with no pipeline in it.
# ---------------------------------------------------------------------------


@dataclass
class Decision:
    """Whether a proposed interruption counts, and why."""

    accept: bool
    reason: str
    score: float | None = None
    level_db: float | None = None
    elapsed_ms: float = 0.0


@dataclass
class VoiceLock:
    """The caller's voice, and the judgement of everything else against it.

    Pure state and arithmetic plus an embedder: fed audio, told when the agent
    starts and stops and when words were heard, asked to ``judge``. The tests
    drive it with a fake embedder so every fallback is pinned without a model.
    """

    embedder: Any
    sample_rate: int = 8000
    threshold: float = DEFAULT_THRESHOLD
    enrol_secs: float = DEFAULT_ENROL_SECS
    loud_margin_db: float = DEFAULT_LOUD_MARGIN_DB

    profile: np.ndarray | None = field(default=None, init=False)
    profile_secs: float = field(default=0.0, init=False)
    caller_level_db: float | None = field(default=None, init=False)
    bot_speaking: bool = field(default=False, init=False)
    _window: deque = field(default_factory=deque, init=False)
    _window_samples: int = field(default=0, init=False)
    _quiet: deque = field(default_factory=deque, init=False)
    _quiet_samples: int = field(default=0, init=False)
    _quiet_heard_words: bool = field(default=False, init=False)
    _pieces: list = field(default_factory=list, init=False)
    _samples_seen: int = field(default=0, init=False)
    _last_judged_at: int | None = field(default=None, init=False)
    _last_decision: Decision | None = field(default=None, init=False)
    _failed: bool = field(default=False, init=False)

    @property
    def enrolled(self) -> bool:
        return self.profile is not None

    @property
    def frame_len(self) -> int:
        return int(self.sample_rate * FRAME_SECS)

    @property
    def samples_seen(self) -> int:
        return self._samples_seen

    # -- input --------------------------------------------------------------

    def feed(self, samples: np.ndarray) -> None:
        """Take a chunk of input audio (int16 at ``sample_rate``)."""
        if samples.size == 0:
            return
        self._samples_seen += samples.size
        self._window_samples = _push(
            self._window, self._window_samples, samples, WINDOW_SECS * self.sample_rate
        )
        if not self.bot_speaking:
            self._quiet_samples = _push(
                self._quiet,
                self._quiet_samples,
                samples,
                MAX_QUIET_SECS * self.sample_rate,
            )

    def heard_words(self) -> None:
        """The transcriber heard something. While the agent is silent that
        makes the stretch worth enrolling from."""
        if not self.bot_speaking:
            self._quiet_heard_words = True

    def set_bot_speaking(self, speaking: bool) -> np.ndarray | None:
        """The agent started or stopped. When it starts, returns the silent
        stretch just ended if words were heard in it, for ``enrol``."""
        self.bot_speaking = speaking
        self._last_judged_at = None
        self._last_decision = None
        stretch = None
        if speaking:
            if self._quiet_heard_words and self._quiet:
                stretch = np.concatenate(list(self._quiet))
            self._quiet.clear()
            self._quiet_samples = 0
            self._quiet_heard_words = False
        if stretch is None or (self.enrolled and self.profile_secs >= MAX_ENROL_SECS):
            return None
        return stretch

    def _window_audio(self) -> np.ndarray:
        if not self._window:
            return np.zeros(0, dtype=np.int16)
        return np.concatenate(list(self._window))

    def window_speech_secs(self) -> float:
        """Seconds of voiced audio in the judged window."""
        speech, _ = active_frames(self._window_audio(), self.frame_len)
        return speech.size / self.sample_rate

    # -- enrolment ----------------------------------------------------------

    def enrol(self, stretch: np.ndarray) -> None:
        """Learn from a stretch in which the agent was silent and words were
        heard.

        Takes the loudest frames of the stretch, loudest first, up to
        ``ENROL_TAKE_SECS`` and never more than ``ENROL_RANGE_DB`` below its
        loud end: the handset, not the room. A background voice 5 dB under the
        caller is in the band but rarely among the loudest frames. They are
        embedded in pieces; before the profile exists, pieces are pooled until
        there is ``enrol_secs`` of them, and any piece that disagrees with the
        rest (someone else answered the greeting, the television was loudest
        for a moment) is left out. After, a piece joins only if it already
        matches, so the profile cannot drift towards the room.
        """
        frames, levels = frame_levels(stretch, self.frame_len)
        voiced = levels > SILENCE_DBFS
        if voiced.sum() < MIN_VERIFY_SECS / FRAME_SECS:
            return
        loud_end = float(np.percentile(levels[voiced], 90))
        eligible = np.flatnonzero(voiced & (levels >= loud_end - ENROL_RANGE_DB))
        take = int(ENROL_TAKE_SECS / FRAME_SECS)
        chosen = np.sort(eligible[np.argsort(-levels[eligible], kind="stable")][:take])
        speech = frames[chosen].reshape(-1).astype(np.int16)
        kept_levels = levels[chosen]
        piece_len = int(ENROL_PIECE_SECS * self.sample_rate)
        frames_per_piece = piece_len // self.frame_len
        for start in range(0, speech.size, piece_len):
            piece = speech[start : start + piece_len]
            if piece.size < MIN_VERIFY_SECS * self.sample_rate:
                break
            first = start // self.frame_len
            self._add_piece(piece, kept_levels[first : first + frames_per_piece])

    def _add_piece(self, piece: np.ndarray, levels: np.ndarray) -> None:
        secs = piece.size / self.sample_rate
        emb = self.embedder.embed(piece, self.sample_rate)
        if self.enrolled:
            if cosine(emb, self.profile) < self.threshold + 0.1:
                return
            total = self.profile_secs + secs
            mixed = self.profile * (self.profile_secs / total) + emb * (secs / total)
            self.profile = mixed / (np.linalg.norm(mixed) + 1e-9)
            self.profile_secs = total
            return

        self._pieces.append((emb, secs, levels))
        if sum(s for _, s, _ in self._pieces) < self.enrol_secs:
            return
        kept = self._consistent(self._pieces)
        if sum(s for _, s, _ in kept) < self.enrol_secs:
            self._pieces = kept  # the disagreeing pieces are dropped for good
            return
        weights = np.array([s for _, s, _ in kept])
        mean = (np.stack([e for e, _, _ in kept]) * weights[:, None]).sum(axis=0)
        self.profile = mean / (np.linalg.norm(mean) + 1e-9)
        self.profile_secs = float(weights.sum())
        self.caller_level_db = float(
            np.median(np.concatenate([lv for _, _, lv in kept]))
        )
        self._pieces = []

    def _consistent(self, pieces: list) -> list:
        if len(pieces) < 3:
            # Two pieces that disagree give no way to say which is the
            # caller; keep both rather than guess.
            return pieces
        kept = []
        for i, (emb, secs, levels) in enumerate(pieces):
            others = np.mean(
                [e for j, (e, _, _) in enumerate(pieces) if j != i], axis=0
            )
            if cosine(emb, others) >= self.threshold:
                kept.append((emb, secs, levels))
        return kept or pieces

    # -- judgement ----------------------------------------------------------

    def judge(self) -> Decision:
        """Does the speech just heard count as the caller interrupting?"""
        started = time.perf_counter()

        def done(decision: Decision) -> Decision:
            decision.elapsed_ms = (time.perf_counter() - started) * 1000.0
            return decision

        if self._failed:
            return done(Decision(True, "unavailable"))
        if not self.enrolled:
            return done(Decision(True, "not_enrolled"))

        last = self._last_decision
        if (
            last is not None
            and self._last_judged_at is not None
            and self._samples_seen - self._last_judged_at
            < REJUDGE_SECS * self.sample_rate
        ):
            return done(Decision(last.accept, last.reason, last.score, last.level_db))

        speech, levels = active_frames(self._window_audio(), self.frame_len)
        if speech.size < MIN_VERIFY_SECS * self.sample_rate:
            return done(Decision(True, "too_short"))
        level = float(np.median(levels))

        try:
            score = cosine(self.embedder.embed(speech, self.sample_rate), self.profile)
        except Exception as error:  # noqa: BLE001 - fall back to today, loudly
            self._failed = True
            logger.warning(f"Caller voice lock failed, interruptions count: {error}")
            return done(Decision(True, "unavailable"))

        if score >= self.threshold:
            decision = Decision(True, "match", score, level)
        elif (
            self.caller_level_db is not None
            and level >= self.caller_level_db + self.loud_margin_db
        ):
            decision = Decision(True, "loud", score, level)
        else:
            decision = Decision(False, "other_voice", score, level)

        self._last_judged_at = self._samples_seen
        self._last_decision = decision
        return done(decision)


def _push(buffer: deque, total: int, samples: np.ndarray, limit: float) -> int:
    """Append to a bounded rolling buffer of chunks; returns its new length."""
    buffer.append(samples)
    total += samples.size
    while buffer and total - buffer[0].size >= limit:
        total -= buffer.popleft().size
    return total


# ---------------------------------------------------------------------------
# The pipecat strategy.
# ---------------------------------------------------------------------------


class CallerVoiceLockUserTurnStartStrategy(BaseUserTurnStartStrategy):
    """Wraps another start strategy and lets only the caller interrupt.

    The inner strategy still decides *when* a turn might start:
    ``MinWordsUserTurnStartStrategy`` on the transcriber's word count, or
    ``ExternalUserTurnStartStrategy`` when the transcriber (Deepgram Flux) calls
    turns itself. This decides, while the agent is speaking, whether the turn
    belongs to the caller. A refused turn is dropped with its words, the way
    MinWords drops words that fall short of its count; anything else passes
    through untouched.

    When the inner strategy fires before there is enough speech to judge --
    Flux calls a turn on its first syllable -- the decision waits up to
    ``MAX_DEFER_SECS`` for more audio, then is made on whatever there is, and
    "too little to judge" counts as the caller.

    It reads only frames the user aggregator already hands every strategy:
    input audio, transcriptions and the agent's speaking state.
    """

    def __init__(
        self,
        inner: BaseUserTurnStartStrategy,
        *,
        embedder: Any | None = None,
        threshold: float = DEFAULT_THRESHOLD,
        enrol_secs: float = DEFAULT_ENROL_SECS,
        loud_margin_db: float = DEFAULT_LOUD_MARGIN_DB,
        background_inference: bool = True,
        **kwargs,
    ) -> None:
        """Wrap ``inner``.

        Args:
            inner: The strategy that proposes turn starts.
            embedder: Anything with ``available`` and ``embed(samples, rate)``;
                the shared ONNX embedder by default.
            threshold: Cosine similarity below which speech is not the caller.
            enrol_secs: Seconds of the caller's voice before the lock acts.
            loud_margin_db: Speech this loud relative to the caller's enrolled
                level always counts.
            background_inference: Run the network off the event loop
                (production). The offline evaluation turns it off to stay
                deterministic.
            **kwargs: Passed to ``BaseUserTurnStartStrategy``.
        """
        super().__init__(**kwargs)
        self._inner = inner
        self._embedder = embedder or SpeakerEmbedder()
        self._lock_params = {
            "threshold": threshold,
            "enrol_secs": enrol_secs,
            "loud_margin_db": loud_margin_db,
        }
        self._background = background_inference
        self._lock: VoiceLock | None = None
        self._suppressed = False
        #: The proposal of a refused turn, while its words keep arriving.
        self._refused: UserTurnStartedParams | None = None
        self._pending: tuple[UserTurnStartedParams, int] | None = None
        self._enrol_task: asyncio.Future | None = None
        self._warned_missing = False
        #: Every judgement made on this call, for the log and the evaluation.
        self.decisions: list[Decision] = []

        inner.add_event_handler("on_push_frame", self._on_inner_push_frame)
        inner.add_event_handler("on_broadcast_frame", self._on_inner_broadcast_frame)
        inner.add_event_handler(
            "on_reset_aggregation", self._on_inner_reset_aggregation
        )
        inner.add_event_handler("on_user_turn_started", self._on_inner_turn_started)

    def __str__(self) -> str:
        return f"CallerVoiceLock({self._inner})"

    @property
    def lock(self) -> VoiceLock | None:
        return self._lock

    @property
    def inner(self) -> BaseUserTurnStartStrategy:
        return self._inner

    async def setup(self, task_manager):
        await super().setup(task_manager)
        await self._inner.setup(task_manager)
        if not self._embedder.available and not self._warned_missing:
            self._warned_missing = True
            logger.warning(
                "caller_voice_lock is on but the speaker model is missing at "
                f"{getattr(self._embedder, 'model_path', '?')}: every "
                "interruption counts, as without the lock"
            )

    async def cleanup(self):
        await super().cleanup()
        await self._inner.cleanup()
        if self._enrol_task is not None and not self._enrol_task.done():
            self._enrol_task.cancel()

    async def reset(self):
        # Called on every turn start. The caller's voice outlives a turn; only
        # the transient state resets.
        await super().reset()
        await self._inner.reset()
        self._pending = None
        self._refused = None

    def _ensure_lock(self, sample_rate: int) -> VoiceLock:
        if self._lock is None or self._lock.sample_rate != sample_rate:
            self._lock = VoiceLock(
                embedder=self._embedder, sample_rate=sample_rate, **self._lock_params
            )
        return self._lock

    async def process_frame(self, frame: Frame) -> ProcessFrameResult:
        lock = self._lock
        if isinstance(frame, InputAudioRawFrame):
            lock = self._ensure_lock(frame.sample_rate)
            lock.feed(np.frombuffer(frame.audio, dtype=np.int16))
            if self._pending is not None and await self._resolve_pending(
                lock, final=False
            ):
                return ProcessFrameResult.STOP
        elif lock is not None:
            if isinstance(frame, BotStartedSpeakingFrame):
                stretch = lock.set_bot_speaking(True)
                if stretch is not None and self._embedder.available:
                    await self._enrol(lock, stretch)
            elif isinstance(frame, BotStoppedSpeakingFrame):
                lock.set_bot_speaking(False)
                self._refused = None
                if self._pending is not None:
                    # Nothing left to interrupt: the turn is simply the caller's.
                    params, _ = self._pending
                    self._pending = None
                    await self._call_event_handler("on_user_turn_started", params)
                    return ProcessFrameResult.STOP
            elif isinstance(frame, VADUserStoppedSpeakingFrame):
                if self._pending is not None and await self._resolve_pending(
                    lock, final=True
                ):
                    return ProcessFrameResult.STOP
            elif isinstance(frame, UserStoppedSpeakingFrame):
                self._refused = None
            elif isinstance(frame, (TranscriptionFrame, InterimTranscriptionFrame)):
                if frame.text.strip():
                    lock.heard_words()
                if self._refused is not None:
                    return await self._rejudge_refused(lock)

        self._suppressed = False
        result = await self._inner.process_frame(frame)
        if self._suppressed:
            self._suppressed = False
            return ProcessFrameResult.CONTINUE
        return result

    async def _rejudge_refused(self, lock: VoiceLock) -> ProcessFrameResult:
        """More words of a refused turn.

        Judged again on the newest audio -- the caller may have started
        talking over the stranger -- and otherwise dropped like the first
        ones. Unlike a fresh proposal, too little to judge does not interrupt
        here: the last thing heard with enough audio was not the caller.
        """
        params = self._refused
        decision = await self._judge(lock)
        if decision.reason == "too_short":
            decision = Decision(False, "too_short_after_refusal")
        if await self._finish(decision, params):
            return ProcessFrameResult.STOP
        return ProcessFrameResult.CONTINUE

    async def _enrol(self, lock: VoiceLock, stretch: np.ndarray) -> None:
        if not self._background:
            try:
                lock.enrol(stretch)
            except Exception as error:  # noqa: BLE001 - enrolment is best effort
                logger.warning(f"Caller voice enrolment failed: {error}")
            return

        # Never on the path of a decision: the agent has just started talking.
        # Chained, so stretches are folded in the order heard.
        previous = self._enrol_task

        async def run():
            if previous is not None:
                await asyncio.gather(previous, return_exceptions=True)
            try:
                await asyncio.to_thread(lock.enrol, stretch)
            except Exception as error:  # noqa: BLE001 - enrolment is best effort
                logger.warning(f"Caller voice enrolment failed: {error}")

        self._enrol_task = asyncio.ensure_future(run())

    async def _judge(self, lock: VoiceLock) -> Decision:
        try:
            if self._background:
                decision = await asyncio.to_thread(lock.judge)
            else:
                decision = lock.judge()
        except Exception as error:  # noqa: BLE001 - never lose the caller
            # pipecat swallows a handler's exception, so raising here would
            # mean no turn at all: the one outcome this must never have.
            logger.warning(f"Caller voice lock raised, interruption counts: {error}")
            decision = Decision(True, "unavailable")
        self.decisions.append(decision)
        return decision

    async def _resolve_pending(self, lock: VoiceLock, *, final: bool) -> bool:
        """Decide a deferred turn once there is enough to go on.

        Returns True when the turn was started.
        """
        params, since = self._pending
        waited = (lock.samples_seen - since) / lock.sample_rate
        if (
            not final
            and waited < MAX_DEFER_SECS
            and lock.window_speech_secs() < MIN_VERIFY_SECS
        ):
            return False
        self._pending = None
        return await self._finish(await self._judge(lock), params)

    async def _finish(self, decision: Decision, params: UserTurnStartedParams) -> bool:
        if decision.accept:
            logger.debug(
                f"{self}: interruption counts ({decision.reason}, "
                f"score={decision.score}, {decision.elapsed_ms:.1f}ms)"
            )
            self._refused = None
            await self._call_event_handler("on_user_turn_started", params)
            return True
        logger.debug(
            f"{self}: interruption refused ({decision.reason}, "
            f"score={decision.score}, level={decision.level_db}, "
            f"{decision.elapsed_ms:.1f}ms)"
        )
        self._refused = params
        await self.trigger_reset_aggregation()
        return False

    async def _on_inner_push_frame(self, _inner, frame, direction=None):
        if direction is None:
            await self.push_frame(frame)
        else:
            await self.push_frame(frame, direction)

    async def _on_inner_broadcast_frame(self, _inner, frame_cls, **kwargs):
        await self.broadcast_frame(frame_cls, **kwargs)

    async def _on_inner_reset_aggregation(self, _inner):
        await self.trigger_reset_aggregation()

    async def _on_inner_turn_started(self, _inner, params: UserTurnStartedParams):
        lock = self._lock
        if lock is None or not lock.bot_speaking or not self._embedder.available:
            await self._call_event_handler("on_user_turn_started", params)
            return
        # From here the turn starts only through _finish.
        self._suppressed = True
        if self._pending is not None:
            return  # already waiting on this turn
        decision = await self._judge(lock)
        if decision.reason == "too_short":
            self.decisions.pop()
            self._pending = (params, lock.samples_seen)
            return
        if await self._finish(decision, params):
            # Started: the proposing frame is consumed, as the inner meant.
            self._suppressed = False
