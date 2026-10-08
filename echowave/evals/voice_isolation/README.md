# Voice isolation: does the agent stop for the caller, and only the caller?

Offline evaluation behind two flags, both off by default:

* `caller_voice_lock` (`api/services/pipecat/caller_voice_lock.py`): while the
  agent speaks, an interruption counts only if the speech sounds like the
  caller, learnt from the caller's first answer.
* `deepfilternet_filter` (`api/services/pipecat/deepfilternet.py`):
  DeepFilterNet3 instead of RNNoise as the inbound noise filter.

Results, and the recommendation they support, are in
[`results/results.md`](results/results.md) and the PR that added this.

## Run it

```bash
cd echowave
pip install -r evals/voice_isolation/requirements.txt   # on top of the API's
python -m evals.voice_isolation.assets                  # models + corpora, ~2 GB, once
# The DeepFilterNet graph is not published anywhere; build it (needs torch):
evals/voice_isolation/dfn_export/build.sh ~/.cache/decibyl/voice_isolation/models
OMP_NUM_THREADS=1 PYTHONPATH=. python -m evals.voice_isolation.run --workers 4   # ~1 h
PYTHONPATH=. python -m evals.voice_isolation.bench > evals/voice_isolation/results/bench.json
PYTHONPATH=. python -m evals.voice_isolation.report
```

No network is needed after `assets`; nothing touches a database, Redis or a
provider.

## What a scene is

`scenes.py`. Every scene follows one script so the numbers compare:

1. The agent has asked how it can help. The caller answers (two sentences,
   ~6 s) while the agent is silent. The room is already there.
2. The agent talks for 8 s; the caller is silent. **Any interruption here is
   false.**
3. The caller cuts in while the agent is still talking. **No interruption
   within their sentence plus 1.5 s is a miss**; time from their first sound to
   the interruption is the acceptance time.

The grid: 5 speech sources × 5 interferers × 4 SNRs (0, 5, 10, 20 dB, caller :
interferer at the microphone) × 2 distances (1 m, 3 m, for interferers placed
in a synthetic room; the DEMAND recordings carry their own room) × 2 seeds =
320 scenes. Everything goes through the carrier's leg -- 8 kHz, G.711 mu-law --
before anything under test sees it.

| Speech source | What | Licence |
| --- | --- | --- |
| `hi`, `hinglish`, `en` | Kokoro-82M TTS, lines in `corpus.py` | Apache-2.0 |
| `en_libri` | LibriSpeech test-clean, real people | CC BY 4.0 |
| `hinglish_real` | MUCS 2021 Hindi-English (OpenSLR 104), real people | CC BY-SA 4.0 |

| Interferer | What |
| --- | --- |
| `talker` | two people near the caller talking to each other (other voices of the same source) |
| `tv` | a presenter over a music bed, through a small loudspeaker, on DEMAND's living room |
| `cafe` | DEMAND cafeteria: babble and crockery |
| `traffic` | DEMAND street traffic |
| `fan` | synthetic pedestal fan |

Two honest caveats about the speech:

* **Kokoro's same-gender voices are near-twins** to a speaker-verification
  network: cosine 0.46-0.67 between, say, its two Hindi women, where two real
  strangers in LibriSpeech score about 0.1 (p99 0.41). The synthetic scenes
  are a sound-alike worst case, not a typical room. Read the real-voice rows.
* **MUCS's test set is two narrators**, not thirty: its 30 speaker ids are
  tutorial recordings, and they collapse into two voice clusters
  (`corpus.MUCS_VOICES`). Hinglish scenes therefore use one real voice per side.
  Larger Indian multi-speaker corpora (AI4Bharat Lahaja, IndicVoices, Kathbath;
  ARTPARK Vaani) are permissive but gated behind a Hugging Face login; adding
  one to `corpus.py` is the obvious next step.

## What runs, and what stands in

`simulate.py` drives the real parts, 20 ms at a time: the filter a transport
would build (`noise_suppression.build_audio_in_filter`, or DeepFilterNet),
Silero VAD with the thresholds `vad_sensitivity` sets for `normal` and
`noisy`, pipecat's `MinWordsUserTurnStartStrategy`, and the voice lock.

The transcriber is the one stand-in: **Vosk** small models (Hindi; Indian
English) give streaming partial results, which is what MinWords counts words
in. It hears background speech imperfectly, as Deepgram and Sarvam do, so it
decides whether MinWords fires in the same way; its absolute latency is its
own, so compare acceptance times between rows, not with production.

The agent's own voice is not in the input: production lines arrive
echo-cancelled.

## Files

| File | |
| --- | --- |
| `assets.py` | downloads, cache, licence table |
| `corpus.py` | caller and background lines, TTS, LibriSpeech, MUCS, noises |
| `acoustics.py` | room, distance, SNR, the telephone leg |
| `scenes.py` | the script and the grid |
| `simulate.py` | one scene through filter, VAD, transcriber and strategies |
| `run.py` | every scene × every configuration, in parallel |
| `bench.py` | latency, CPU and memory on one core, one process per measurement |
| `report.py` | `results/results.md` |
| `dfn_export/` | the DeepFilterNet3 streaming export and its parity check |
