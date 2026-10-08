# DeepFilterNet3 as a streaming ONNX graph

`api/services/pipecat/deepfilternet.py` runs DeepFilterNet3 one 10 ms hop at a
time with only numpy and onnxruntime. Nobody publishes such a graph -- the
community ONNX exports either take a whole sequence (no streaming) or call the
network one frame at a time while silently dropping the temporal context of its
causal convolutions and deep-filter taps (wrong output). This directory makes
it, from the official checkpoint, and proves it.

```bash
./build.sh OUT_DIR    # needs python3 with torch (CPU), onnx, onnxruntime
python verify.py      # parity, suppression, timing, memory -> work/verify_results.json
```

`build.sh` is what the API image's `WITH_VOICE_ISOLATION` stage runs; the
result is byte-identical across clean environments
(sha256 `bedbfb772fd375fbaae820f80533ebaf0e10c297d40549accb881a41e5a205cc`,
8,640,865 bytes) and the Dockerfile checks it.

## Source

* Checkpoint: `models/DeepFilterNet3.zip` at tag v0.5.6 of
  github.com/Rikorose/DeepFilterNet (zip sha256 `49c52edc…22284d2`;
  `model_120.ckpt.best` sha256 `23b92884…e6003`).
* Model code: the `deepfilternet==0.5.6` wheel, installed without
  dependencies. Its Rust extension `libdf` has no wheel for Python 3.13 and
  pins numpy below 2, so `libdf_stub/libdf.py` is a numpy port of the parts it
  needs, line for line from libDF v0.5.6. `verify.py` checks it against the
  genuine Rust library when a Python 3.11 env with it exists at `./venv311`
  (features agree to 3e-6, offline output to ≥94.8 dB SNR).
* Licence: the repository is MIT OR Apache-2.0 at your option (LICENSE,
  LICENSE-MIT, LICENSE-APACHE, README "License"); the wheel says MIT. The
  checkpoint sits in that repository under no separate licence; reading the
  repository licence as covering it is an inference, worth a legal glance
  before relying on it.

## The graph

One call = one hop. Inputs are the newest STFT frame's normalised ERB feature
`[1,1,1,32]`, complex feature `[1,2,1,96]` and raw spectrum `[1,1,1,481,2]`,
plus state; output is the enhanced spectrum of the frame two hops earlier
(the model's lookahead), a local-SNR estimate, and the new state:

| State | Shape | Why |
| --- | --- | --- |
| `erb_hist` | 1,1,2,32 | `enc.erb_conv0` has a time kernel of 3 |
| `spec_feat_hist` | 1,2,2,96 | `enc.df_conv0`, time kernel 3 |
| `c0_hist` | 1,64,4,96 | `df_dec.df_convp`, time kernel 5 |
| `spec_hist` | 1,1,4,481,2 | the deep filter's 5 taps (2 past, 2 lookahead) |
| `h_enc`, `h_erb`, `h_df` | 1/2,1,256 | the three GRUs |

Every other temporal operator in DFN3 has a time kernel of 1, so this is the
whole state: the stream equals the official offline `df.enhance.enhance` to
≤3e-7 (108-136 dB SNR) after the 30 ms delay, on LibriSpeech mixed with DEMAND
traffic, cafeteria and living-room noise, fed in 20 ms chunks. Output is
identical for any chunking. For the first two hops the graph is not run and
the output is silence, which is what the offline model does too.

## Measured here (one thread, Xeon 2.8 GHz)

| | |
| --- | --- |
| algorithmic delay | 1440 samples at 48 kHz = 30 ms |
| CPU per 10 ms hop | 1.03 ms mean (0.86 ms ONNX), p99 1.9 ms; RTF 0.10 |
| process RSS, numpy + onnxruntime only | 86 MB peak |
| traffic / cafeteria / living room, noise alone | -47.8 / -21.6 / -35.7 dB |
| speech alone | -0.2 to -0.3 dB |

Not in the graph: the post-filter and attenuation limit, both off in the
official defaults.
