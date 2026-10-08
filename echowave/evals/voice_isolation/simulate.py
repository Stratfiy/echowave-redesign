"""Run one scene through the inbound path, frame by frame, with the real parts.

What is real: the noise filters (the same classes the transports build), Silero
VAD with the thresholds ``vad_sensitivity`` sets for each caller environment,
pipecat's ``MinWordsUserTurnStartStrategy`` and the caller voice lock -- all
driven with the frames the user aggregator would hand them, 20 ms at a time.

What stands in: the transcriber. Interim words come from Vosk's streaming
partial results (Apache-2.0, small Hindi and Indian-English models) instead of
Deepgram or Sarvam. It hears background speech the way a real transcriber
does -- imperfectly, and more at higher levels -- which is exactly what decides
whether MinWords fires; absolute acceptance times are Vosk's, so compare them
between rows, not with production dashboards.

And the agent's own voice is not in the input: production lines arrive
echo-cancelled, and modelling the residue would only add noise to the
comparison.
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from functools import lru_cache

import numpy as np

from api.services.pipecat import caller_voice_lock, noise_suppression, vad_sensitivity
from evals.voice_isolation.assets import path
from evals.voice_isolation.scenes import Rendered
from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    InputAudioRawFrame,
    InterimTranscriptionFrame,
    TranscriptionFrame,
    VADUserStartedSpeakingFrame,
    VADUserStoppedSpeakingFrame,
)
from pipecat.turns.user_start.min_words_user_turn_start_strategy import (
    MinWordsUserTurnStartStrategy,
)

RATE = 8000
CHUNK = 160  # 20 ms, the transports' grain

MODELS = {
    "resnet34": "models/wespeaker_resnet34_lm.onnx",
    "ecapa512": "models/wespeaker_ecapa512_lm.onnx",
}


# --- Filters -----------------------------------------------------------------


async def _build_filter(name: str):
    if name == "none":
        return None
    if name == "rnnoise":
        # Exactly what a transport builds for an agent with suppression on.
        return await noise_suppression.build_audio_in_filter({})
    if name == "dfn":
        from api.services.pipecat import deepfilternet

        return deepfilternet.build_filter(model_path=str(path("models/dfn3_streaming.onnx")))
    raise ValueError(name)


async def run_filter(audio: np.ndarray, name: str) -> tuple[list[np.ndarray], float, float]:
    """The filter's output per 20 ms input chunk, its CPU seconds, and the
    longest single call in ms."""
    chunks = [audio[i : i + CHUNK] for i in range(0, len(audio) - CHUNK + 1, CHUNK)]
    flt = await _build_filter(name)
    if flt is None:
        return chunks, 0.0, 0.0
    await flt.start(RATE)
    out = []
    cpu = 0.0
    worst = 0.0
    for chunk in chunks:
        t0 = time.process_time()
        w0 = time.perf_counter()
        data = await flt.filter(chunk.tobytes())
        worst = max(worst, (time.perf_counter() - w0) * 1000)
        cpu += time.process_time() - t0
        out.append(np.frombuffer(data, dtype=np.int16).copy())
    await flt.stop()
    return out, cpu, worst


# --- Voice activity ----------------------------------------------------------


def run_vad(chunks: list[np.ndarray], environment: str) -> list[tuple[int, str]]:
    from pipecat.audio.vad.silero import SileroVADAnalyzer
    from pipecat.audio.vad.vad_analyzer import VADState

    analyzer = SileroVADAnalyzer(
        sample_rate=RATE,
        params=vad_sensitivity.params({"caller_environment": environment}),
    )
    analyzer.set_sample_rate(RATE)
    state = VADState.QUIET
    events = []
    for i, chunk in enumerate(chunks):
        if chunk.size == 0:
            continue
        new = analyzer._run_analyzer(chunk.tobytes())
        if new != state and new not in (VADState.STARTING, VADState.STOPPING):
            events.append((i, "start" if new == VADState.SPEAKING else "stop"))
            state = new
    return events


# --- Transcriber stand-in ----------------------------------------------------


@lru_cache(maxsize=2)
def _vosk_model(language: str):
    from vosk import Model, SetLogLevel

    SetLogLevel(-1)
    name = "vosk-model-small-en-in-0.4" if language == "en" else "vosk-model-small-hi-0.22"
    return Model(str(path(f"vosk/{name}")))


def run_asr(chunks: list[np.ndarray], language: str) -> list[tuple[int, str, str]]:
    from vosk import KaldiRecognizer

    rec = KaldiRecognizer(_vosk_model(language), RATE)
    events = []
    last = ""
    for i, chunk in enumerate(chunks):
        if chunk.size == 0:
            continue
        if rec.AcceptWaveform(chunk.tobytes()):
            text = json.loads(rec.Result()).get("text", "")
            if text:
                events.append((i, "final", text))
            last = ""
        else:
            text = json.loads(rec.PartialResult()).get("partial", "")
            if text and text != last:
                events.append((i, "interim", text))
                last = text
    return events


# --- The strategies ----------------------------------------------------------


@dataclass
class Config:
    name: str
    filter: str = "rnnoise"
    vad: str = "normal"
    min_words: int = 3
    lock_model: str | None = None
    threshold: float = caller_voice_lock.DEFAULT_THRESHOLD
    loud_margin_db: float | None = caller_voice_lock.DEFAULT_LOUD_MARGIN_DB


@dataclass
class Outcome:
    scene: str
    config: str
    false_interruptions: int
    accepted: bool
    accept_ms: float | None
    enrolled_at: float | None
    agent_secs: float = 0.0
    lock_cpu_s: float = 0.0
    judge_ms: list[float] = field(default_factory=list)
    decisions: list[tuple[str, float | None]] = field(default_factory=list)
    triggers: list[float] = field(default_factory=list)


class _Embedders:
    cache: dict[str, caller_voice_lock.SpeakerEmbedder] = {}

    @classmethod
    def get(cls, model: str) -> caller_voice_lock.SpeakerEmbedder:
        if model not in cls.cache:
            cls.cache[model] = caller_voice_lock.SpeakerEmbedder(str(path(MODELS[model])))
        return cls.cache[model]


async def run_strategy(
    rendered: Rendered,
    chunks: list[np.ndarray],
    vad_events: list[tuple[int, str]],
    asr_events: list[tuple[int, str, str]],
    config: Config,
) -> Outcome:
    inner = MinWordsUserTurnStartStrategy(min_words=config.min_words)
    if config.lock_model:
        strategy = caller_voice_lock.CallerVoiceLockUserTurnStartStrategy(
            inner,
            embedder=_Embedders.get(config.lock_model),
            threshold=config.threshold,
            loud_margin_db=(
                config.loud_margin_db if config.loud_margin_db is not None else 1e9
            ),
            background_inference=False,
        )
    else:
        strategy = inner

    secs = CHUNK / RATE
    # The agent answers once the caller's turn is final: a second after the
    # transcriber's last final for the answer, never before the script says.
    finals = [
        i * secs
        for i, kind, _ in asr_events
        if kind == "final" and rendered.enrol_end <= i * secs <= rendered.enrol_end + 2.5
    ]
    agent_starts = max([rendered.bot_start] + [t + 1.0 for t in finals])
    bot_start = int(agent_starts / secs)
    barge = int(rendered.barge_in / secs)
    barge_window_end = int((rendered.barge_in_end + 1.5) / secs)

    state = {"turn": False, "turn_started": None, "now": 0}
    #: Turns started while the agent was talking: one per turn, as the
    #: controller allows (a second proposal inside a turn is ignored).
    triggers: list[int] = []
    inject: list = []

    async def on_started(_strategy, _params):
        i = state["now"]
        if i < bot_start or state["turn"]:
            return
        triggers.append(i)
        state["turn"] = True
        state["turn_started"] = i
        await strategy.reset()  # the controller resets every start strategy
        inject.append(BotStoppedSpeakingFrame())  # the agent is cut off

    async def noop(*_args, **_kwargs):
        return None

    strategy.add_event_handler("on_user_turn_started", on_started)
    strategy.add_event_handler("on_reset_aggregation", noop)
    strategy.add_event_handler("on_push_frame", noop)
    strategy.add_event_handler("on_broadcast_frame", noop)

    vad_at: dict[int, list[str]] = {}
    for i, kind in vad_events:
        vad_at.setdefault(i, []).append(kind)
    asr_at: dict[int, list[tuple[str, str]]] = {}
    for i, kind, text in asr_events:
        asr_at.setdefault(i, []).append((kind, text))

    cpu = 0.0
    enrolled_at = None
    for i, chunk in enumerate(chunks):
        state["now"] = i
        frames = []
        if i == bot_start:
            frames.append(BotStartedSpeakingFrame())
        if i == barge - 5 and state["turn"]:
            # Measure the barge-in on its own: whatever the room did before
            # is over, and the agent is talking again.
            state["turn"] = False
            frames.append(BotStartedSpeakingFrame())
        if chunk.size:
            frames.append(InputAudioRawFrame(audio=chunk.tobytes(), sample_rate=RATE, num_channels=1))
        for kind in vad_at.get(i, []):
            frames.append(
                VADUserStartedSpeakingFrame() if kind == "start" else VADUserStoppedSpeakingFrame()
            )
        ended_turn = False
        for kind, text in asr_at.get(i, []):
            cls = TranscriptionFrame if kind == "final" else InterimTranscriptionFrame
            frames.append(cls(text=text, user_id="caller", timestamp=""))
            if kind == "final" and state["turn"] and i > (state["turn_started"] or 0):
                ended_turn = True
        if state["turn"] and not ended_turn and i - (state["turn_started"] or 0) > int(2.5 / secs):
            ended_turn = True
        for frame in frames:
            t0 = time.process_time()
            await strategy.process_frame(frame)
            while inject:
                await strategy.process_frame(inject.pop(0))
            cpu += time.process_time() - t0
        if ended_turn:
            # The caller's (or the room's) turn is over; the agent answers.
            state["turn"] = False
            t0 = time.process_time()
            await strategy.process_frame(BotStartedSpeakingFrame())
            cpu += time.process_time() - t0
        lock = getattr(strategy, "lock", None)
        if enrolled_at is None and lock is not None and lock.enrolled:
            enrolled_at = i * secs

    false = sum(1 for i in triggers if bot_start <= i < barge - 5)
    accepted = [i for i in triggers if barge - 5 <= i <= barge_window_end]
    accept_ms = (accepted[0] - barge) * secs * 1000 if accepted else None
    decisions = getattr(strategy, "decisions", [])
    return Outcome(
        scene=rendered.scene.id,
        config=config.name,
        false_interruptions=false,
        accepted=bool(accepted),
        accept_ms=accept_ms,
        enrolled_at=enrolled_at,
        agent_secs=(barge - 5 - bot_start) * secs,
        lock_cpu_s=cpu if config.lock_model else 0.0,
        judge_ms=[d.elapsed_ms for d in decisions if d.score is not None],
        decisions=[(d.reason, d.score) for d in decisions],
        triggers=[round(i * secs, 2) for i in triggers],
    )


def run_config_sync(*args) -> Outcome:
    return asyncio.run(run_strategy(*args))
