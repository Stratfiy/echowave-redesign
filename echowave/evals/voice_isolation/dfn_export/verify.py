#!/usr/bin/env python
"""Verify DeepFilterNetStream (numpy + onnxruntime, one 10 ms hop at a time) against the official
offline DeepFilterNet3 (deepfilternet 0.5.6 torch model, full-sequence forward via df.enhance.enhance).

Checks
  1. Features: numpy libdf stub (libdf_stub/, used by the official df_features in this py3.13
     env) vs an independent vectorised implementation of the libDF formulas written here, and
     vs the genuine DeepFilterLib 0.5.6 Rust extension (a py3.11 venv at ./venv311 running make_golden.py) if present.
  2. Offline torch model (official enhance(), stub libdf) vs offline with genuine libdf.
  3. Streaming (20 ms chunks) vs official offline, after removing the 1440-sample delay.
  4. Noise attenuation on noise-only / speech-only inputs, SI-SDR on mixtures.
  5. Timing: ms per 10 ms hop, RTF on 1 thread; peak RSS of a numpy+onnxruntime-only process.

Usage: venv/bin/python verify.py            (writes verify_results.json)
"""
import glob
import json
import os
import subprocess
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ONNX = os.environ.get("DFN_ONNX_OUT", os.path.join(HERE, "dfn3_streaming.onnx"))
# The evaluation's cache (python -m evals.voice_isolation.assets fetches it).
DATA = os.path.expanduser(os.environ.get("VOICE_ISOLATION_CACHE", "~/.cache/decibyl/voice_isolation"))
WORK = os.path.join(HERE, "work")
sys.path.insert(0, HERE)


def rss_probe(path):
    """Run the stream in a process that imports only numpy + onnxruntime; print peak RSS."""
    from dfn_stream import DeepFilterNetStream

    x = np.load(path)
    dn = DeepFilterNetStream(ONNX)
    for i in range(0, len(x), 960):
        dn.process(x[i:i + 960])
    mods = sorted(m for m in sys.modules if m.split(".")[0] in ("torch", "df", "libdf", "scipy"))
    import onnxruntime

    print(json.dumps({"peak_rss_mb_VmHWM": _vmhwm_mb(), "heavy_modules_loaded": mods,
                      "python": sys.version.split()[0], "numpy": np.__version__,
                      "onnxruntime": onnxruntime.__version__}))


def _vmhwm_mb():
    """Peak resident set of *this* process (VmHWM is reset on exec, unlike ru_maxrss)."""
    for line in open("/proc/self/status"):
        if line.startswith("VmHWM:"):
            return int(line.split()[1]) / 1024


# ----------------------------------------------------------------------------- data
def load_audio():
    import soundfile as sf
    import soxr

    files = sorted(glob.glob(f"{DATA}/librispeech/LibriSpeech/test-clean/**/*.flac", recursive=True))
    rng = np.random.default_rng(1234)
    picks = rng.choice(len(files), 3, replace=False)
    speech = []
    for i in picks:
        s, sr = sf.read(files[i], dtype="float32")
        assert sr == 16000
        s = s[: 16000 * 6]
        speech.append((os.path.relpath(files[i], DATA), soxr.resample(s, 16000, 48000).astype(np.float32)))
    noises = {}
    for name in ("STRAFFIC", "PCAFETER", "DLIVING"):
        n, sr = sf.read(f"{DATA}/demand/{name}/ch01.wav", dtype="float32")
        assert sr == 16000
        n = n[16000 * 10: 16000 * 16]
        noises[name] = soxr.resample(n, 16000, 48000).astype(np.float32)
    return speech, noises


def rms(x):
    return float(np.sqrt(np.mean(np.asarray(x, np.float64) ** 2)))


def build_signals():
    speech, noises = load_audio()
    sig, meta = {}, {}
    for (fname, s), (nname, n), snr in zip(speech, noises.items(), (5.0, 0.0, 10.0)):
        L = min(len(s), len(n))
        s = s[:L] * (0.05 / rms(s[:L]))  # speech at about -26 dBFS RMS
        n = n[:L] * (rms(s) / rms(n[:L]) / 10 ** (snr / 20))
        key = f"mix_{nname}_{int(snr)}dB"
        sig[key] = (s + n).astype(np.float32)
        sig[f"clean_{nname}"] = s.astype(np.float32)
        sig[f"noise_{nname}"] = n.astype(np.float32)
        meta[key] = {"speech": fname, "noise": f"demand/{nname}/ch01.wav [10s:16s]", "snr_db": snr}
    return sig, meta


# ----------------------------------------------------------------------------- features
def formula_features(x, widths, alpha=0.99):
    """Independent vectorised libDF analysis + erb/erb_norm + unit_norm (float32)."""
    win_i = np.arange(960, dtype=np.float64)
    s_ = np.sin(0.5 * np.pi * (win_i + 0.5) / 480)
    win = np.sin(0.5 * np.pi * s_ * s_).astype(np.float32)
    T = len(x) // 480
    xp = np.concatenate([np.zeros(480, np.float32), x[: T * 480]])
    idx = np.arange(T)[:, None] * 480 + np.arange(960)[None]
    spec = (np.fft.rfft(xp[idx] * win, axis=-1) / 960.0).astype(np.complex64)
    w = np.asarray(widths, int)
    bands = np.split(np.abs(spec.astype(np.complex128)) ** 2, np.cumsum(w)[:-1], axis=-1)
    e = 10 * np.log10(np.stack([b.mean(-1) for b in bands], -1) + 1e-10)
    st = np.linspace(-60, -90, 32)
    erb = np.empty_like(e)
    for t in range(T):
        st = e[t] * (1 - alpha) + st * alpha
        erb[t] = (e[t] - st) / 40
    su = np.linspace(0.001, 0.0001, 96)
    c = spec[:, :96].astype(np.complex128)
    cpl = np.empty_like(c)
    for t in range(T):
        su = np.abs(c[t]) * (1 - alpha) + su * alpha
        cpl[t] = c[t] / np.sqrt(su)
    return spec, erb.astype(np.float32), cpl.astype(np.complex64)


def snr_db(ref, est):
    ref = np.asarray(ref, np.float64)
    err = ref - np.asarray(est, np.float64)
    return float(10 * np.log10(np.sum(ref**2) / max(np.sum(err**2), 1e-30)))


def si_sdr(ref, est):
    ref = np.asarray(ref, np.float64)
    est = np.asarray(est, np.float64)
    a = np.dot(est, ref) / np.dot(ref, ref)
    return float(10 * np.log10(np.sum((a * ref) ** 2) / np.sum((a * ref - est) ** 2)))


def stream_run(dn, x, chunk=960):
    dn.reset()
    outs = [dn.process(x[i:i + chunk]) for i in range(0, len(x), chunk)]
    outs.append(dn.flush())
    y = np.concatenate(outs)
    return y[dn.delay_samples: dn.delay_samples + len(x)]


def main():
    import torch

    sys.path.insert(0, HERE)
    sys.path.insert(0, os.path.join(HERE, "libdf_stub"))
    import libdf
    from dfn_official import _stub_df_io, load_official, offline_enhance
    _stub_df_io()
    from df.enhance import df_features
    from dfn_stream import DELAY_SAMPLES, ERB_WIDTHS, DeepFilterNetStream

    torch.set_num_threads(1)
    os.makedirs(WORK, exist_ok=True)
    R = {"libdf_in_py313_env": libdf.__file__}
    sig, meta = build_signals()
    R["signals"] = meta
    np.savez(os.path.join(WORK, "_signals.npz"), **sig)

    model, df_state, p, epoch = load_official()
    assert tuple(int(v) for v in df_state.erb_widths()) == ERB_WIDTHS

    # ---- 1. features ----
    feat = {}
    for k, x in sig.items():
        xp = np.concatenate([x, np.zeros(960, np.float32)])
        spec, erb_f, cpl_f = df_features(torch.from_numpy(xp[None]), df_state, p.nb_df)
        s2, e2, c2 = formula_features(xp, ERB_WIDTHS)
        sc = spec.numpy()[0, 0]
        feat[k] = {
            "spec_rel": float(np.abs(sc[..., 0] + 1j * sc[..., 1] - s2).max() / np.abs(s2).max()),
            "erb_abs": float(np.abs(erb_f.numpy()[0, 0] - e2).max()),
            "cplx_abs": float(np.abs(cpl_f.numpy()[0, 0, ..., 0] + 1j * cpl_f.numpy()[0, 0, ..., 1] - c2).max()),
        }
    R["features_stub_vs_formula_maxabs"] = {
        m: max(v[m] for v in feat.values()) for m in ("spec_rel", "erb_abs", "cplx_abs")}

    # ---- 2. offline (official enhance) ----
    off = {k: offline_enhance(model, df_state, x) for k, x in sig.items()}

    gold_path = os.path.join(WORK, "_golden.npz")
    py311 = os.path.join(HERE, "venv311", "bin", "python")
    if os.path.exists(py311):
        subprocess.run([py311, "-I", os.path.join(HERE, "make_golden.py"),
                        os.path.join(WORK, "_signals.npz"), gold_path], check=True)
        g = np.load(gold_path)
        gd = {"libdf": str(g["libdf_file"]), "erb_feat_maxabs": 0.0, "cplx_feat_maxabs": 0.0,
              "spec_maxabs_rel": 0.0, "offline_stub_vs_genuine_snr_db_min": 1e9,
              "offline_stub_vs_genuine_maxabs": 0.0}
        for k, x in sig.items():
            xp = np.concatenate([x, np.zeros(960, np.float32)])
            spec, erb_f, cpl_f = df_features(torch.from_numpy(xp[None]), df_state, p.nb_df)
            gd["erb_feat_maxabs"] = max(gd["erb_feat_maxabs"], float(np.abs(erb_f.numpy() - g[f"{k}__erb"]).max()))
            gd["cplx_feat_maxabs"] = max(gd["cplx_feat_maxabs"], float(np.abs(cpl_f.numpy() - g[f"{k}__cplx"]).max()))
            gd["spec_maxabs_rel"] = max(gd["spec_maxabs_rel"], float(np.abs(spec.numpy() - g[f"{k}__spec"]).max() / np.abs(g[f"{k}__spec"]).max()))
            go = g[f"{k}__offline"]
            gd["offline_stub_vs_genuine_maxabs"] = max(gd["offline_stub_vs_genuine_maxabs"], float(np.abs(go - off[k]).max()))
            gd["offline_stub_vs_genuine_snr_db_min"] = min(gd["offline_stub_vs_genuine_snr_db_min"], snr_db(go, off[k]))
        R["genuine_libdf_crosscheck"] = gd
    else:
        g = None

    # ---- 3. streaming vs offline ----
    dn = DeepFilterNetStream(ONNX)
    par = {}
    tail = DELAY_SAMPLES  # last 30 ms: offline sees zero *features* past the end, stream sees real
    for k, x in sig.items():
        ys = stream_run(dn, x, chunk=960)
        yo = off[k]
        d = {"max_abs_err": float(np.abs(ys - yo).max()), "snr_db": snr_db(yo, ys),
             "max_abs_err_excl_last30ms": float(np.abs(ys[:-tail] - yo[:-tail]).max()),
             "snr_db_excl_last30ms": snr_db(yo[:-tail], ys[:-tail]),
             "offline_peak": float(np.abs(yo).max())}
        if g is not None:
            d["snr_db_vs_genuine_libdf_offline"] = snr_db(g[f"{k}__offline"], ys)
        par[k] = d
    # chunking independence + delay sanity on one signal
    k0 = next(k for k in sig if k.startswith("mix_"))
    x0 = sig[k0]
    y480 = stream_run(dn, x0, chunk=480)
    y_odd = stream_run(dn, x0, chunk=137)
    par["_chunking"] = {"20ms_vs_10ms_maxabs": float(np.abs(stream_run(dn, x0, 960) - y480).max()),
                        "20ms_vs_137samples_maxabs": float(np.abs(stream_run(dn, x0, 960) - y_odd).max())}
    dn.reset()
    yfull = np.concatenate([dn.process(x0[i:i + 960]) for i in range(0, len(x0), 960)] + [dn.flush(), np.zeros(960, np.float32)])
    lag_snr = {}
    for lag in (DELAY_SAMPLES - 480, DELAY_SAMPLES, DELAY_SAMPLES + 480):
        lag_snr[str(lag)] = snr_db(off[k0][:-tail], yfull[lag:lag + len(x0)][:-tail])
    par["_delay_check_snr_db_by_lag"] = lag_snr
    R["stream_vs_offline"] = par

    # ---- 4. attenuation / quality (streaming output, delay removed) ----
    q = {}
    for name in ("STRAFFIC", "PCAFETER", "DLIVING"):
        n, s = sig[f"noise_{name}"], sig[f"clean_{name}"]
        mk = next(k for k in sig if k.startswith(f"mix_{name}"))
        yn, ys_, ym = stream_run(dn, n), stream_run(dn, s), stream_run(dn, sig[mk])
        sl = slice(48000, None)  # skip first second (norm-state warm-up)
        q[name] = {
            "noise_only_attenuation_db": 10 * np.log10(np.sum(n[sl].astype(np.float64) ** 2) / np.sum(yn[sl].astype(np.float64) ** 2)),
            "speech_only_level_change_db": 10 * np.log10(np.sum(ys_[sl].astype(np.float64) ** 2) / np.sum(s[sl].astype(np.float64) ** 2)),
            "speech_only_si_sdr_db": si_sdr(s[sl], ys_[sl]),
            "mixture": mk,
            "mix_si_sdr_in_db": si_sdr(s[sl], sig[mk][sl]),
            "mix_si_sdr_out_db": si_sdr(s[sl], ym[sl]),
        }
    R["quality_streaming"] = q

    # ---- 5. timing (1 ORT thread, torch set to 1 thread but idle) ----
    x = np.concatenate([sig[k] for k in sig if k.startswith("mix_")])
    dn.reset()
    for i in range(0, 48000, 480):  # warm-up
        dn.process(x[i:i + 480])
    dn.reset()
    ts = []
    for i in range(0, len(x) - 479, 480):
        c = x[i:i + 480]
        t0 = time.perf_counter()
        dn.process(c)
        ts.append(time.perf_counter() - t0)
    ts = np.array(ts) * 1e3
    # split: ONNX run only
    feeds = {"feat_erb": np.zeros((1, 1, 1, 32), np.float32), "feat_spec": np.zeros((1, 2, 1, 96), np.float32),
             "spec": np.zeros((1, 1, 1, 481, 2), np.float32)}
    feeds.update(dn._states)
    to = []
    for _ in range(500):
        t0 = time.perf_counter()
        dn.sess.run(None, feeds)
        to.append(time.perf_counter() - t0)
    R["timing"] = {"hops": int(len(ts)), "audio_s": len(ts) * 0.01,
                   "mean_ms_per_hop": float(ts.mean()), "p50_ms": float(np.percentile(ts, 50)),
                   "p99_ms": float(np.percentile(ts, 99)), "max_ms": float(ts.max()),
                   "rtf": float(ts.sum() / 1e3 / (len(ts) * 0.01)),
                   "onnx_run_only_mean_ms": float(np.mean(to) * 1e3),
                   "ort_threads": "intra=1, inter=1", "cpu": _cpu_name()}

    # ---- RSS of a numpy+onnxruntime-only process ----
    np.save(os.path.join(WORK, "_probe.npy"), x)
    out = subprocess.run([sys.executable, os.path.abspath(__file__), "--rss-probe",
                          os.path.join(WORK, "_probe.npy")], capture_output=True, text=True, check=True)
    R["rss_probe_numpy_ort_only"] = json.loads(out.stdout.strip().splitlines()[-1])
    R["verify_process_peak_rss_mb_incl_torch"] = _vmhwm_mb()
    R["onnx_bytes"] = os.path.getsize(ONNX)

    with open(os.path.join(WORK, "verify_results.json"), "w") as f:
        json.dump(R, f, indent=1, default=float)
    print(json.dumps(R, indent=1, default=float))


def _cpu_name():
    try:
        for line in open("/proc/cpuinfo"):
            if line.startswith("model name"):
                return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return "unknown"


if __name__ == "__main__":
    if len(sys.argv) > 2 and sys.argv[1] == "--rss-probe":
        rss_probe(sys.argv[2])
    else:
        main()
