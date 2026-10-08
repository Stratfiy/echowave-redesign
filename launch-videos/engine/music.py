"""Soundtrack for any Decibyl video, generated from its own sound events.

    python3 music.py work/meta.json work/soundtrack.wav

Same palette as the hero (brag-output/work/music.py): 100 BPM, F major,
F - Dm - Bb - C, one chord per bar, warm pad, sub bass, plucked arpeggio,
soft kick and hat, every effect pitched in F major pentatonic and sent to one
shared room. The progression loops until the outro, then lands on F exactly
where the outro starts. Tutorials get a calmer bed: no drums, quarter-note
arpeggio, everything lower.
"""

import json
import sys
import wave

import numpy as np

SR = 48000
BEAT = 0.6
BAR = 4 * BEAT
rng = np.random.default_rng(7)

meta = json.load(open(sys.argv[1]))
DUR = float(meta["duration"])
N = int(SR * DUR)
TUTORIAL = meta.get("music") == "tutorial"
bounds = meta["bounds"]
OUTRO = next((b["t0"] for b in bounds if b["type"] == "outro"), DUR)
FIRST = bounds[0]["t1"] if bounds and bounds[0]["type"] in ("ask", "title") else 0.0


def hz(m):
    return 440.0 * 2 ** ((m - 69) / 12)


def buf():
    return np.zeros((N, 2))


def add(bus, sig, t0, pan=0.0, gain=1.0):
    i = int(t0 * SR)
    if i >= N or i < 0:
        return
    sig = sig[: N - i] * gain
    bus[i : i + len(sig), 0] += sig * np.sqrt(0.5 * (1 - pan))
    bus[i : i + len(sig), 1] += sig * np.sqrt(0.5 * (1 + pan))


def lowpass(x, cutoff):
    spec = np.fft.rfft(x, axis=0)
    f = np.fft.rfftfreq(x.shape[0], 1 / SR)
    resp = 1 / np.sqrt(1 + (f / cutoff) ** 4)
    return np.fft.irfft(spec * (resp[:, None] if x.ndim == 2 else resp), n=x.shape[0], axis=0)


def pad_note(f, dur):
    t = np.arange(int(dur * SR)) / SR
    out = np.zeros_like(t)
    for cents in (-6, 6):
        ff = f * 2 ** (cents / 1200) * (1 + 0.0015 * np.sin(2 * np.pi * 4.8 * t))
        ph = 2 * np.pi * np.cumsum(ff) / SR
        for n in range(1, 7):
            out += np.sin(n * ph + n) / n**1.7
    env = np.minimum(1, t / 0.45) * np.clip(np.minimum(1, (dur - t) / 0.8), 0, None)
    return out * env * 0.25


def mallet(f, dur=1.2, bright=1.0):
    t = np.arange(int(dur * SR)) / SR
    s = (np.sin(2 * np.pi * f * t) * np.exp(-t * 4.5)
         + 0.35 * bright * np.sin(4 * np.pi * f * t) * np.exp(-t * 9)
         + 0.12 * bright * np.sin(6 * np.pi * f * t) * np.exp(-t * 16))
    return s * np.minimum(1, t / 0.003)


def bell(f, dur=2.2):
    t = np.arange(int(dur * SR)) / SR
    s = (np.sin(2 * np.pi * f * t) * np.exp(-t * 2.6)
         + 0.4 * np.sin(2 * np.pi * 2.76 * f * t) * np.exp(-t * 5)
         + 0.18 * np.sin(2 * np.pi * 5.4 * f * t) * np.exp(-t * 9))
    return s * np.minimum(1, t / 0.002)


def kick():
    t = np.arange(int(0.45 * SR)) / SR
    f = 45 + 75 * np.exp(-t * 32)
    return np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t * 8.5)


def noise_hit(dur, decay):
    t = np.arange(int(dur * SR)) / SR
    return np.diff(rng.standard_normal(len(t) + 1)) * np.exp(-t * decay) * 0.12


def whoosh(dur=0.7):
    n = int(dur * SR)
    t = np.arange(n) / SR
    return lowpass(rng.standard_normal(n), 1400) * np.sin(np.pi * (t / dur) ** 0.7) ** 2


def bass_note(f, dur):
    t = np.arange(int(dur * SR)) / SR
    s = np.sin(2 * np.pi * f * t) + 0.25 * np.sin(4 * np.pi * f * t)
    return s * np.minimum(1, t / 0.01) * np.exp(-t * 1.4) * np.clip(np.minimum(1, (dur - t) / 0.05), 0, None)


F_ = (41, [53, 57, 60, 64])
DM = (38, [50, 53, 57, 60])
BB = (34, [46, 50, 53, 57])
C_ = (36, [52, 55, 60, 62])
PROG = [F_, DM, BB, C_]

pad, bass, arp, drums, sfx = buf(), buf(), buf(), buf(), buf()


def play_bar(root, voicing, t0, dur, k_bar, final=False):
    for m in voicing:
        add(pad, pad_note(hz(m), dur + 0.8), t0, pan=rng.uniform(-0.4, 0.4))
    for beat in ([0] if final else [0, 2]):
        tb = t0 + beat * BEAT
        if tb < t0 + dur and (tb >= FIRST or TUTORIAL):
            add(bass, bass_note(hz(root), min(2.6 if final else 1.15, dur + 1.5)), tb, gain=0.6 if TUTORIAL else 1.0)
    tones = [m + 12 for m in voicing]
    pattern = [0, 1, 2, 3, 2, 1, 2, 3]
    step = BEAT if TUTORIAL else BEAT / 2
    k = 0
    while k * step < dur - 1e-6:
        te = t0 + k * step
        if te >= max(FIRST, 0.3) and te < DUR - 1.0 and not (final and k % 2):
            add(arp, mallet(hz(tones[pattern[k % 8]]), 1.0, bright=0.8), te, pan=0.3 if k % 2 else -0.3, gain=1.0 if k == 0 else 0.7)
        k += 1
    if not TUTORIAL and not final:
        for beat in range(4):
            tb = t0 + beat * BEAT
            if FIRST + 0.1 <= tb < min(t0 + dur, OUTRO):
                if beat in (0, 2):
                    add(drums, kick(), tb, gain=0.55)
                add(drums, noise_hit(0.06, 70), tb + BEAT / 2, pan=0.25, gain=0.5)


# progression up to the outro, then F from the outro to the end
t, b = 0.0, 0
while t < OUTRO - 1e-6:
    dur = min(BAR, OUTRO - t)
    root, voicing = PROG[b % 4]
    play_bar(root, voicing, t, dur, b)
    t += BAR
    b += 1
if OUTRO < DUR:
    play_bar(*F_, OUTRO, DUR - OUTRO, b, final=True)

# effects, from the video's own events
PENTA = [77, 79, 81, 84, 86, 89]
for e in meta["events"]:
    t, k = e["t"], e["kind"]
    if k == "typing":
        for tt in np.arange(t, e["until"], 0.055):
            add(sfx, noise_hit(0.012, 500), tt + rng.uniform(-0.01, 0.01), pan=rng.uniform(-0.2, 0.2), gain=rng.uniform(1.2, 2.4))
    elif k == "send":
        add(sfx, mallet(hz(84), 0.6, bright=0.4), t, gain=0.55)
    elif k == "whoosh":
        add(sfx, whoosh(0.7), t - 0.4, gain=0.22)
    elif k == "pop":
        add(sfx, mallet(hz(PENTA[e.get("i", 0) % 6]), 0.8), t, pan=-0.4 + 0.2 * (e.get("i", 0) % 5), gain=0.3)
    elif k == "card":
        add(sfx, mallet(hz(81), 0.5, bright=0.3), t, gain=0.28)
    elif k == "click":
        add(sfx, noise_hit(0.012, 500), t, gain=1.6)
    elif k == "approve":
        for j, m in enumerate((81, 84, 89)):
            add(sfx, bell(hz(m)), t + j * 0.07, pan=-0.2 + j * 0.2, gain=0.28)
    elif k == "done":
        add(sfx, mallet(hz([84, 89, 86][e.get("i", 0) % 3]), 0.8), t, gain=0.28)
    elif k == "msg":
        add(sfx, mallet(hz(77 if e.get("dir") == "in" else 81), 0.6), t, pan=0.3, gain=0.28)
    elif k == "row":
        add(sfx, mallet(hz([81, 84, 86, 89][e.get("i", 0) % 4]), 0.6), t, gain=0.2)
    elif k == "focus":
        add(sfx, mallet(hz(86), 0.7, bright=0.5), t, gain=0.22)
    elif k == "cta":
        for j, m in enumerate((77, 81, 84, 89)):
            add(sfx, bell(hz(m), 2.5), t + j * 0.05, pan=-0.3 + j * 0.2, gain=0.24)

# mix
sfx = np.tanh(lowpass(sfx, 7000) * 2.0) / 2.0
pad = lowpass(pad, 1800)
arp = lowpass(arp, 5000)
g = 0.6 if TUTORIAL else 1.0
dry = g * (0.30 * pad + 0.50 * bass + 0.20 * arp + 0.45 * drums) + 0.55 * sfx

ir_n = int(2.2 * SR)
ti = np.arange(ir_n) / SR
ir = lowpass(rng.standard_normal((ir_n, 2)) * np.exp(-ti * 3.2)[:, None], 5000)
ir[: int(0.012 * SR)] = 0
send = 0.35 * pad + 0.45 * arp + 0.6 * sfx + 0.1 * drums
L = N + ir_n
wet = np.fft.irfft(np.fft.rfft(send, n=L, axis=0) * np.fft.rfft(ir, n=L, axis=0), n=L, axis=0)[:N]
wet *= 0.25 / (np.abs(wet).max() + 1e-9) * np.abs(send).max()
mix = dry + wet
mix -= lowpass(mix, 30)
mix = np.tanh(mix / np.abs(mix).max() * 0.8) / np.tanh(0.8)
t = np.arange(N) / SR
fade = np.ones(N)
fade[: int(0.03 * SR)] = np.linspace(0, 1, int(0.03 * SR))
fo = t > DUR - 1.2
fade[fo] = np.cos(np.clip((t[fo] - (DUR - 1.2)) / 1.2, 0, 1) * np.pi / 2) ** 2
mix *= fade[:, None] * 0.89

with wave.open(sys.argv[2], "wb") as w:
    w.setnchannels(2)
    w.setsampwidth(2)
    w.setframerate(SR)
    w.writeframes((mix * 32767).astype("<i2").tobytes())
print(f"soundtrack: {DUR:.1f} s, outro at {OUTRO:.1f} s, {'tutorial' if TUTORIAL else 'story'} bed")
