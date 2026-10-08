"""What the two options cost on one CPU core: latency, CPU and memory.

    PYTHONPATH=. python -m evals.voice_isolation.bench

Each measurement runs in its own fresh process so resident memory is the
option's own, and on one thread, as production runs them. The evaluation run
(``run.py``) is parallel and its timings are not used for these figures.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import time

import numpy as np

from evals.voice_isolation.assets import path

RATE = 8000


def rss_mb() -> float:
    for line in open("/proc/self/status"):
        if line.startswith("VmRSS:"):
            return int(line.split()[1]) / 1024
    return float("nan")


def speech(secs: float) -> np.ndarray:
    """Speech-like audio: a LibriSpeech utterance through the phone line."""
    from evals.voice_isolation import acoustics, corpus

    clip = corpus.libri_utterances(corpus.libri_speakers()[0], 1, min_secs=secs)[0]
    return acoustics.telephone(clip[: int(secs * corpus.RATE)])


def bench_embedder(model: str) -> dict:
    from api.services.pipecat import caller_voice_lock as cvl

    audio = speech(4.0)
    window = audio[: int(cvl.WINDOW_SECS * RATE)]
    before = rss_mb()
    emb = cvl.SpeakerEmbedder(str(path(f"models/wespeaker_{model}.onnx")))
    emb.embed(window, RATE)  # load and warm
    loaded = rss_mb()
    times = []
    for _ in range(100):
        t0 = time.perf_counter()
        emb.embed(window, RATE)
        times.append((time.perf_counter() - t0) * 1000)
    enrol = []
    lock = cvl.VoiceLock(embedder=emb)
    for _ in range(10):
        t0 = time.perf_counter()
        lock._pieces = []
        lock.profile = None
        lock.enrol(np.tile(audio, 2))
        enrol.append((time.perf_counter() - t0) * 1000)
    # Per call: the judged window and up to MAX_QUIET_SECS of bot-silent audio.
    per_call_kb = (cvl.WINDOW_SECS + cvl.MAX_QUIET_SECS) * RATE * 2 / 1024
    return {
        "model": model,
        "model_mb_on_disk": path(f"models/wespeaker_{model}.onnx").stat().st_size / 1e6,
        "rss_added_by_model_mb": loaded - before,
        "judge_ms_p50": float(np.percentile(times, 50)),
        "judge_ms_p95": float(np.percentile(times, 95)),
        "enrol_ms_p50": float(np.percentile(enrol, 50)),
        "per_call_buffer_kb_max": per_call_kb,
    }


async def _filter_cost(name: str, calls: int = 10) -> dict:
    from api.services.pipecat import deepfilternet, noise_suppression

    audio = speech(10.0)
    chunks = [audio[i : i + 160].tobytes() for i in range(0, audio.size - 160, 160)]
    before = rss_mb()
    filters = []
    for _ in range(calls):
        if name == "dfn":
            f = deepfilternet.build_filter(model_path=str(path("models/dfn3_streaming.onnx")))
        else:
            f = await noise_suppression.build_audio_in_filter({})
        await f.start(RATE)
        filters.append(f)
    for c in chunks[:50]:  # warm every call
        for f in filters:
            await f.filter(c)
    after = rss_mb()
    f = filters[0]
    per_chunk = []
    cpu0 = time.process_time()
    for c in chunks:
        t0 = time.perf_counter()
        await f.filter(c)
        per_chunk.append((time.perf_counter() - t0) * 1000)
    cpu = time.process_time() - cpu0
    return {
        "filter": name,
        "cpu_ms_per_audio_s": 1000 * cpu / (len(chunks) * 0.02),
        "per_20ms_chunk_ms_p50": float(np.percentile(per_chunk, 50)),
        "per_20ms_chunk_ms_p99": float(np.percentile(per_chunk, 99)),
        "rss_for_%d_calls_mb" % calls: after - before,
    }


def _child(kind: str, arg: str) -> None:
    if kind == "embedder":
        print(json.dumps(bench_embedder(arg)))
    else:
        print(json.dumps(asyncio.run(_filter_cost(arg))))


def main() -> None:
    results = []
    for kind, arg in [
        ("embedder", "resnet34_lm"),
        ("embedder", "ecapa512_lm"),
        ("filter", "rnnoise"),
        ("filter", "dfn"),
    ]:
        out = subprocess.run(
            [sys.executable, "-m", "evals.voice_isolation.bench", "--child", kind, arg],
            capture_output=True,
            text=True,
            check=True,
        )
        results.append(json.loads(out.stdout.strip().splitlines()[-1]))
    print(json.dumps(results, indent=1))


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--child":
        import os

        os.environ.setdefault("OMP_NUM_THREADS", "1")
        _child(sys.argv[2], sys.argv[3])
    else:
        main()
