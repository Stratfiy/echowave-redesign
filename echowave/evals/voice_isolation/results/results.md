# Voice isolation: results

## All scenes

| Configuration | False interruptions / min of agent speech | Scenes with ≥1 false | Missed barge-ins | Accept p50 ms | Accept p90 ms |
| --- | --- | --- | --- | --- | --- |
| baseline normal mw3 | 4.21 | 26% | 3.4% | 1360 | 2100 |
| baseline noisy mw3 | 4.21 | 26% | 3.4% | 1360 | 2100 |
| baseline normal mw4 | 3.07 | 20% | 5.6% | 1720 | 2480 |
| baseline noisy mw4 | 3.07 | 20% | 5.6% | 1720 | 2480 |
| lock resnet34 t0.25 | 2.86 | 22% | 4.7% | 1380 | 2140 |
| lock resnet34 t0.25 no-loud | 2.81 | 21% | 4.7% | 1380 | 2140 |
| lock resnet34 t0.30 | 2.22 | 19% | 4.7% | 1420 | 2140 |
| lock resnet34 t0.30 no-loud | 2.17 | 19% | 4.7% | 1420 | 2140 |
| lock resnet34 t0.35 | 1.63 | 17% | 4.7% | 1460 | 2160 |
| lock resnet34 t0.35 no-loud | 1.58 | 16% | 5.0% | 1460 | 2160 |
| lock ecapa512 t0.25 | 2.78 | 22% | 4.1% | 1420 | 2140 |
| lock ecapa512 t0.25 no-loud | 2.76 | 21% | 4.1% | 1420 | 2140 |
| lock ecapa512 t0.30 | 2.12 | 20% | 4.1% | 1480 | 2200 |
| lock ecapa512 t0.30 no-loud | 2.07 | 19% | 4.1% | 1480 | 2200 |
| lock ecapa512 t0.35 | 1.53 | 15% | 4.4% | 1480 | 2200 |
| lock ecapa512 t0.35 no-loud | 1.48 | 14% | 5.0% | 1500 | 2200 |
| dfn normal mw3 | 8.20 | 41% | 0.6% | 1200 | 1900 |
| dfn noisy mw4 | 7.19 | 37% | 1.6% | 1580 | 2260 |
| dfn+lock resnet34 t0.25 | 4.37 | 29% | 0.9% | 1300 | 1940 |
| dfn+lock resnet34 t0.30 | 3.20 | 23% | 0.9% | 1300 | 1940 |
| dfn+lock resnet34 t0.35 | 2.16 | 17% | 1.2% | 1340 | 1980 |
| dfn+lock ecapa512 t0.25 | 3.96 | 27% | 0.9% | 1300 | 1940 |
| dfn+lock ecapa512 t0.30 | 2.69 | 19% | 0.9% | 1340 | 2000 |
| dfn+lock ecapa512 t0.35 | 2.08 | 17% | 2.2% | 1380 | 2000 |

_320 scenes._

## Background speech (talker, TV, cafeteria)

| Configuration | False interruptions / min of agent speech | Scenes with ≥1 false | Missed barge-ins | Accept p50 ms | Accept p90 ms |
| --- | --- | --- | --- | --- | --- |
| baseline normal mw3 | 6.74 | 42% | 3.5% | 1260 | 2100 |
| baseline noisy mw3 | 6.74 | 42% | 3.5% | 1260 | 2100 |
| baseline normal mw4 | 4.90 | 32% | 5.5% | 1620 | 2500 |
| baseline noisy mw4 | 4.90 | 32% | 5.5% | 1620 | 2500 |
| lock resnet34 t0.30 | 3.55 | 31% | 4.5% | 1320 | 2140 |
| lock resnet34 t0.30 no-loud | 3.47 | 30% | 4.5% | 1320 | 2140 |
| lock ecapa512 t0.30 | 3.39 | 32% | 3.5% | 1400 | 2200 |
| lock ecapa512 t0.30 no-loud | 3.31 | 30% | 3.5% | 1400 | 2200 |
| dfn normal mw3 | 12.99 | 64% | 0.0% | 660 | 1740 |
| dfn noisy mw4 | 11.45 | 59% | 0.5% | 940 | 2160 |
| dfn+lock resnet34 t0.30 | 5.02 | 36% | 0.0% | 1060 | 1820 |
| dfn+lock ecapa512 t0.30 | 4.21 | 30% | 0.0% | 1160 | 1960 |

_200 scenes._

## Real voices only (LibriSpeech, MUCS), every interferer

| Configuration | False interruptions / min of agent speech | Scenes with ≥1 false | Missed barge-ins | Accept p50 ms | Accept p90 ms |
| --- | --- | --- | --- | --- | --- |
| baseline normal mw3 | 2.92 | 22% | 8.6% | 1400 | 2480 |
| baseline noisy mw3 | 2.92 | 22% | 8.6% | 1400 | 2480 |
| baseline normal mw4 | 2.01 | 15% | 13.3% | 1700 | 2760 |
| baseline noisy mw4 | 2.01 | 15% | 13.3% | 1700 | 2760 |
| lock resnet34 t0.25 | 2.01 | 17% | 11.7% | 1440 | 2580 |
| lock resnet34 t0.25 no-loud | 1.88 | 16% | 11.7% | 1440 | 2580 |
| lock resnet34 t0.30 | 1.49 | 14% | 11.7% | 1480 | 2580 |
| lock resnet34 t0.30 no-loud | 1.36 | 12% | 11.7% | 1480 | 2580 |
| lock resnet34 t0.35 | 1.10 | 11% | 11.7% | 1540 | 2600 |
| lock resnet34 t0.35 no-loud | 0.97 | 9% | 12.5% | 1620 | 2640 |
| lock ecapa512 t0.25 | 1.75 | 16% | 10.2% | 1540 | 2540 |
| lock ecapa512 t0.25 no-loud | 1.68 | 16% | 10.2% | 1540 | 2540 |
| lock ecapa512 t0.30 | 1.49 | 15% | 10.2% | 1620 | 2600 |
| lock ecapa512 t0.30 no-loud | 1.36 | 13% | 10.2% | 1700 | 2600 |
| lock ecapa512 t0.35 | 0.91 | 10% | 10.9% | 1620 | 2600 |
| lock ecapa512 t0.35 no-loud | 0.78 | 9% | 12.5% | 1720 | 2600 |
| dfn normal mw3 | 7.39 | 38% | 1.6% | 1260 | 2140 |
| dfn noisy mw4 | 6.30 | 34% | 2.3% | 1520 | 2560 |
| dfn+lock resnet34 t0.25 | 3.86 | 25% | 2.3% | 1340 | 2180 |
| dfn+lock resnet34 t0.30 | 2.76 | 19% | 2.3% | 1340 | 2240 |
| dfn+lock resnet34 t0.35 | 1.93 | 12% | 3.1% | 1400 | 2240 |
| dfn+lock ecapa512 t0.25 | 3.73 | 24% | 2.3% | 1360 | 2220 |
| dfn+lock ecapa512 t0.30 | 3.09 | 18% | 2.3% | 1400 | 2260 |
| dfn+lock ecapa512 t0.35 | 2.25 | 16% | 5.5% | 1460 | 2240 |

_128 scenes._

## Real voices only (LibriSpeech, MUCS), background speech

| Configuration | False interruptions / min of agent speech | Scenes with ≥1 false | Missed barge-ins | Accept p50 ms | Accept p90 ms |
| --- | --- | --- | --- | --- | --- |
| baseline normal mw3 | 4.68 | 35% | 8.8% | 1340 | 2540 |
| baseline noisy mw3 | 4.68 | 35% | 8.8% | 1340 | 2540 |
| baseline normal mw4 | 3.23 | 24% | 12.5% | 1600 | 2840 |
| baseline noisy mw4 | 3.23 | 24% | 12.5% | 1600 | 2840 |
| lock resnet34 t0.30 | 2.39 | 22% | 11.2% | 1440 | 2600 |
| lock resnet34 t0.30 no-loud | 2.18 | 20% | 11.2% | 1440 | 2600 |
| lock ecapa512 t0.30 | 2.39 | 24% | 8.8% | 1540 | 2600 |
| lock ecapa512 t0.30 no-loud | 2.18 | 21% | 8.8% | 1700 | 2600 |
| dfn normal mw3 | 11.75 | 60% | 0.0% | 760 | 2000 |
| dfn noisy mw4 | 10.10 | 55% | 0.0% | 1160 | 2360 |
| dfn+lock resnet34 t0.30 | 4.33 | 29% | 0.0% | 1240 | 2140 |
| dfn+lock ecapa512 t0.30 | 4.84 | 28% | 0.0% | 1320 | 2340 |

_80 scenes._

## Non-speech noise (traffic, fan)

| Configuration | False interruptions / min of agent speech | Scenes with ≥1 false | Missed barge-ins | Accept p50 ms | Accept p90 ms |
| --- | --- | --- | --- | --- | --- |
| baseline normal mw3 | 0.00 | 0% | 3.3% | 1520 | 2140 |
| baseline noisy mw3 | 0.00 | 0% | 3.3% | 1520 | 2140 |
| baseline normal mw4 | 0.00 | 0% | 5.8% | 1940 | 2460 |
| baseline noisy mw4 | 0.00 | 0% | 5.8% | 1940 | 2460 |
| lock resnet34 t0.30 | 0.00 | 0% | 5.0% | 1520 | 2140 |
| lock resnet34 t0.30 no-loud | 0.00 | 0% | 5.0% | 1520 | 2140 |
| lock ecapa512 t0.30 | 0.00 | 0% | 5.0% | 1520 | 2160 |
| lock ecapa512 t0.30 no-loud | 0.00 | 0% | 5.0% | 1520 | 2160 |
| dfn normal mw3 | 0.14 | 2% | 1.7% | 1520 | 2020 |
| dfn noisy mw4 | 0.00 | 0% | 3.3% | 1860 | 2440 |
| dfn+lock resnet34 t0.30 | 0.14 | 2% | 2.5% | 1520 | 2040 |
| dfn+lock ecapa512 t0.30 | 0.14 | 2% | 2.5% | 1520 | 2020 |

_120 scenes._

## By interferer (false / min · missed)

| Interferer | baseline normal mw3 | baseline noisy mw4 | lock ecapa512 t0.30 | lock resnet34 t0.30 | dfn normal mw3 | dfn+lock ecapa512 t0.30 |
| --- | --- | --- | --- | --- | --- | --- |
| cafe | 0.61 / 5% | 0.20 / 8% | 0.20 / 5% | 0.00 / 8% | 1.23 / 0% | 0.00 / 0% |
| fan | 0.00 / 5% | 0.00 / 6% | 0.00 / 6% | 0.00 / 6% | 0.20 / 2% | 0.20 / 4% |
| talker | 11.87 / 2% | 9.54 / 4% | 5.79 / 2% | 6.70 / 2% | 16.77 / 0% | 6.33 / 0% |
| traffic | 0.00 / 0% | 0.00 / 5% | 0.00 / 2% | 0.00 / 2% | 0.00 / 0% | 0.00 / 0% |
| tv | 4.63 / 4% | 2.57 / 6% | 2.57 / 4% | 2.16 / 5% | 15.01 / 0% | 4.16 / 0% |

## By SNR, background speech only (false / min · missed)

| SNR dB | baseline normal mw3 | baseline noisy mw4 | lock ecapa512 t0.30 | lock resnet34 t0.30 | dfn normal mw3 | dfn+lock ecapa512 t0.30 |
| --- | --- | --- | --- | --- | --- | --- |
| 0 | 10.00 / 8% | 6.88 / 14% | 5.57 / 8% | 5.57 / 10% | 19.15 / 0% | 9.33 / 0% |
| 5 | 9.22 / 6% | 7.28 / 6% | 4.04 / 6% | 4.53 / 6% | 18.01 / 0% | 4.94 / 0% |
| 10 | 5.74 / 0% | 4.10 / 0% | 2.63 / 0% | 2.79 / 2% | 12.19 / 0% | 1.79 / 0% |
| 20 | 1.97 / 0% | 1.31 / 2% | 1.31 / 0% | 1.31 / 0% | 2.31 / 0% | 0.66 / 0% |

## By distance, talker and TV (false / min · missed)

| Distance m | baseline normal mw3 | baseline noisy mw4 | lock ecapa512 t0.30 | lock resnet34 t0.30 | dfn normal mw3 | dfn+lock ecapa512 t0.30 |
| --- | --- | --- | --- | --- | --- | --- |
| 1.0 | 10.68 / 4% | 8.14 / 6% | 4.78 / 4% | 5.60 / 5% | 17.81 / 0% | 6.27 / 0% |
| 3.0 | 5.85 / 2% | 4.00 / 4% | 3.59 / 2% | 3.28 / 2% | 13.92 / 0% | 4.20 / 0% |

## By speech source (false / min · missed)

| Source | baseline normal mw3 | baseline noisy mw4 | lock ecapa512 t0.30 | lock resnet34 t0.30 | dfn normal mw3 | dfn+lock ecapa512 t0.30 |
| --- | --- | --- | --- | --- | --- | --- |
| en | 2.45 / 0% | 1.55 / 2% | 0.77 / 0% | 0.64 / 0% | 7.74 / 0% | 1.55 / 0% |
| en_libri | 2.30 / 17% | 1.28 / 27% | 1.15 / 20% | 0.77 / 22% | 7.00 / 3% | 3.43 / 5% |
| hi | 6.99 / 0% | 5.11 / 0% | 3.99 / 0% | 3.87 / 0% | 9.24 / 0% | 3.20 / 0% |
| hinglish | 5.67 / 0% | 4.53 / 0% | 2.77 / 0% | 3.53 / 0% | 9.17 / 0% | 2.51 / 0% |
| hinglish_real | 3.54 / 0% | 2.76 / 0% | 1.84 / 0% | 2.23 / 2% | 7.80 / 0% | 2.73 / 0% |

## Filter CPU in the evaluation (parallel run, indicative)

| Filter | CPU ms per s of 8 kHz audio | Worst single call ms |
| --- | --- | --- |
| dfn | 121 | 49.3 |
| rnnoise | 89 | 1044.2 |

## Cost on one core (bench.py)

```json
[
 {
  "model": "resnet34_lm",
  "model_mb_on_disk": 26.530309,
  "rss_added_by_model_mb": 86.50390625,
  "judge_ms_p50": 55.06549849951625,
  "judge_ms_p95": 72.61042785007703,
  "enrol_ms_p50": 144.23683550012356,
  "per_call_buffer_kb_max": 492.1875
 },
 {
  "model": "ecapa512_lm",
  "model_mb_on_disk": 24.861931,
  "rss_added_by_model_mb": 56.33203125,
  "judge_ms_p50": 23.306260499794007,
  "judge_ms_p95": 29.92138090012304,
  "enrol_ms_p50": 61.53274999996938,
  "per_call_buffer_kb_max": 492.1875
 },
 {
  "filter": "rnnoise",
  "cpu_ms_per_audio_s": 83.83557575250835,
  "per_20ms_chunk_ms_p50": 1.6724619999877177,
  "per_20ms_chunk_ms_p99": 2.541932839685612,
  "rss_for_10_calls_mb": 69.29296875
 },
 {
  "filter": "dfn",
  "cpu_ms_per_audio_s": 108.5885774247491,
  "per_20ms_chunk_ms_p50": 2.042220000475936,
  "per_20ms_chunk_ms_p99": 3.784997579896287,
  "rss_for_10_calls_mb": 37.5
 }
]
```

## Licences

| Asset | Licence | Used for |
| --- | --- | --- |
| wespeaker-resnet34-lm | WeSpeaker code Apache-2.0; weights CC BY 4.0 (trained on VoxCeleb2) | caller voice lock (speaker embedding) |
| wespeaker-ecapa512-lm | WeSpeaker code Apache-2.0; weights CC BY 4.0 (trained on VoxCeleb2) | caller voice lock (speaker embedding, faster candidate) |
| vosk-small-en-in | Apache-2.0 | interim transcripts, English |
| vosk-small-hi | Apache-2.0 | interim transcripts, Hindi and Hinglish |
| kokoro-82m | Apache-2.0 (Kokoro-82M weights); kokoro-onnx runtime MIT; espeak-ng GPL-3.0 (eval only, not shipped) | synthesised callers and background talkers |
| librispeech-test-clean | CC BY 4.0 | real English callers and background talkers |
| mucs-hinglish-test | CC BY-SA 4.0 (evaluation only; nothing derived is redistributed) | real Hinglish callers and background talkers |
| demand-straffic | CC BY 4.0 | traffic |
| demand-pcafeter | CC BY 4.0 | cafeteria babble |
| demand-dliving | CC BY 4.0 | living-room bed under the television |
