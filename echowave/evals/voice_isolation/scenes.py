"""One phone call's worth of audio, with the moments that matter marked.

Every scene follows the same script, so the numbers compare across conditions:

1. The agent has asked how it can help. The caller answers in two sentences
   (~6 s) while the agent is silent -- this is all the voice lock gets to
   learn from, and the background is already there while it listens.
2. The agent starts speaking and talks for 8 s. The caller is silent; the room
   is not. **Any interruption here is a false one.**
3. The caller cuts in (``barge_in``) while the agent is still talking. **No
   interruption within the caller's sentence plus 1.5 s is a missed one**, and
   the time from the caller's first sound to the interruption is the
   acceptance time.

Interferers: ``talker`` (two people near the caller talking to each other),
``tv`` (a presenter over music through a small loudspeaker), ``cafe`` (DEMAND's
cafeteria: babble and crockery), ``traffic`` (DEMAND's street) and ``fan``.
Talkers, television and fan are placed in the room at a distance; the DEMAND
recordings already carry their own acoustics. Levels are set as caller :
interferer at the microphone, so 0 dB means the room is as loud as the caller.
"""

from __future__ import annotations

import zlib
from dataclasses import asdict, dataclass

import numpy as np

from evals.voice_isolation import acoustics, corpus

#: Synthetic (Kokoro) and real (``en_libri``: LibriSpeech; ``hinglish_real``:
#: MUCS 2021) speech.
LANGUAGES = ("hi", "hinglish", "en", "en_libri", "hinglish_real")
REAL = ("en_libri", "hinglish_real")
INTERFERERS = ("talker", "tv", "cafe", "traffic", "fan")
SNRS = (0, 5, 10, 20)
DISTANCES = (1.0, 3.0)
PLACED = ("talker", "tv", "fan")

#: Caller speech level at the microphone, dBFS of active speech. A typical
#: handset level after the carrier's AGC.
CALLER_DBFS = -24.0
NOISE_FLOOR_DBFS = -66.0

AGENT_TALKS_SECS = 8.0
TAIL_SECS = 2.5


@dataclass(frozen=True)
class Scene:
    language: str
    interferer: str
    snr_db: int
    distance_m: float | None
    seed: int

    @property
    def id(self) -> str:
        d = "amb" if self.distance_m is None else f"{self.distance_m:g}m"
        return f"{self.language}-{self.interferer}-{self.snr_db}dB-{d}-s{self.seed}"

    @property
    def asr_language(self) -> str:
        return "en" if self.language.startswith("en") else "hi"


@dataclass
class Rendered:
    scene: Scene
    audio: np.ndarray  # 8 kHz int16, what the carrier hands us
    caller_segments: list[tuple[float, float]]
    enrol_end: float
    bot_start: float
    barge_in: float
    barge_in_end: float
    duration: float
    caller_id: str
    background_ids: list[str]

    def meta(self) -> dict:
        out = {k: v for k, v in asdict(self).items() if k not in ("audio", "scene")}
        out["scene"] = asdict(self.scene)
        out["id"] = self.scene.id
        return out


def grid(seeds: int = 2) -> list[Scene]:
    scenes = []
    for seed in range(seeds):
        for language in LANGUAGES:
            for interferer in INTERFERERS:
                for snr in SNRS:
                    distances = DISTANCES if interferer in PLACED else (None,)
                    for distance in distances:
                        scenes.append(Scene(language, interferer, snr, distance, seed))
    return scenes


def _speech(scene: Scene, rng: np.random.Generator):
    """(enrolment clips, barge-in clip, background clips, caller id, bg ids)."""
    if scene.language == "en_libri":
        speakers = corpus.libri_speakers()
        order = rng.permutation(len(speakers))
        caller = speakers[order[0]]
        others = [speakers[i] for i in order[1:3]]
        own = corpus.libri_utterances(caller, 3)
        enrol = [u[: int(3.2 * corpus.RATE)] for u in own[:2]]
        barge = own[2][: int(3.0 * corpus.RATE)]
        background = []
        for other in others:
            background += [
                u[: int(4.0 * corpus.RATE)] for u in corpus.libri_utterances(other, 6)
            ]
        rng.shuffle(background)
        return (
            enrol,
            barge,
            background,
            f"libri:{caller}",
            [f"libri:{o}" for o in others],
        )

    if scene.language == "hinglish_real":
        callers, talkers = (
            corpus.MUCS_VOICES[::-1] if scene.seed % 2 else corpus.MUCS_VOICES
        )
        caller = callers[int(rng.integers(0, len(callers)))]
        others = list(rng.choice(talkers, size=2, replace=False))
        own = corpus.mucs_utterances(caller, 3, secs=3.2)
        enrol, barge = own[:2], own[2]
        background = []
        for other in others:
            background += corpus.mucs_utterances(other, 6, secs=4.0)
        rng.shuffle(background)
        return enrol, barge, background, f"mucs:{caller}", [f"mucs:{o}" for o in others]

    language = scene.language
    callers, talkers = corpus.VOICES[language]
    caller = callers[
        (scene.seed + zlib.crc32(scene.interferer.encode())) % len(callers)
    ]
    lines = corpus.CALLER_LINES[language]
    picks = rng.choice(len(lines), size=2, replace=False)
    enrol = [corpus.tts(lines[i], caller, language) for i in picks]
    barge_lines = corpus.BARGE_IN_LINES[language]
    barge = corpus.tts(
        barge_lines[int(rng.integers(0, len(barge_lines)))], caller, language
    )
    chatter = corpus.CHATTER_LINES[language]
    background = []
    for k, i in enumerate(rng.permutation(len(chatter))):
        background.append(corpus.tts(chatter[i], talkers[k % len(talkers)], language))
    return (
        enrol,
        barge,
        background,
        f"kokoro:{caller}",
        [f"kokoro:{t}" for t in talkers],
    )


def render(scene: Scene) -> Rendered:
    rng = np.random.default_rng(zlib.crc32(scene.id.encode()))
    rate = corpus.RATE
    enrol, barge, background, caller_id, bg_ids = _speech(scene, rng)

    # The caller's timeline.
    t = 0.6
    segments = []
    pieces = []
    for clip in enrol:
        segments.append((t, t + len(clip) / rate))
        pieces.append((t, clip))
        t += len(clip) / rate + float(rng.uniform(0.6, 0.9))
    enrol_end = segments[-1][1]
    # The agent answers after the caller's turn is finalised and the reply
    # is synthesised: about a second and a half on a good line.
    bot_start = enrol_end + 1.5
    barge_in = bot_start + AGENT_TALKS_SECS
    barge_end = barge_in + len(barge) / rate
    segments.append((barge_in, barge_end))
    pieces.append((barge_in, barge))
    duration = barge_end + TAIL_SECS
    n = int(duration * rate)

    caller = np.zeros(n, dtype=np.float32)
    for start, clip in pieces:
        i = int(start * rate)
        caller[i : i + len(clip)] += clip[: n - i]
    caller = acoustics.place(caller, 0.05, rng)
    caller *= 10 ** (CALLER_DBFS / 20) / acoustics.active_rms(caller)

    # The room.
    if scene.interferer == "talker":
        room = np.zeros(n, dtype=np.float32)
        i = int(0.2 * rate)
        k = 0
        while i < n:
            clip = background[k % len(background)]
            room[i : i + len(clip)] += clip[: n - i]
            i += len(clip) + int(rng.uniform(0.25, 0.9) * rate)
            k += 1
    elif scene.interferer == "tv":
        tv_language = {"en_libri": "en", "hinglish_real": "hinglish"}.get(
            scene.language, scene.language
        )
        room = corpus.television(tv_language, duration, rng)[:n]
    elif scene.interferer == "cafe":
        room = corpus.demand("PCAFETER", duration, rng)[:n]
    elif scene.interferer == "traffic":
        room = corpus.demand("STRAFFIC", duration, rng)[:n]
    elif scene.interferer == "fan":
        room = corpus.fan(duration, rng)[:n]
    else:
        raise ValueError(scene.interferer)
    if len(room) < n:
        room = np.pad(room, (0, n - len(room)))
    if scene.distance_m is not None:
        room = acoustics.place(room, scene.distance_m, rng)
    room = acoustics.scale_to_snr(caller, room, scene.snr_db)

    floor = rng.standard_normal(n).astype(np.float32) * 10 ** (NOISE_FLOOR_DBFS / 20)
    mix = caller + room + floor
    peak = np.max(np.abs(mix))
    if peak > 0.99:
        mix *= 0.99 / peak
    return Rendered(
        scene=scene,
        audio=acoustics.telephone(mix),
        caller_segments=segments,
        enrol_end=enrol_end,
        bot_start=bot_start,
        barge_in=barge_in,
        barge_in_end=barge_end,
        duration=duration,
        caller_id=caller_id,
        background_ids=bg_ids if scene.interferer == "talker" else [scene.interferer],
    )
