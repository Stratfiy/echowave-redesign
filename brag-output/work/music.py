"""Soundtrack for the Decibyl brag video: music and effects as one piece.

100 BPM in F major, F - Dm - Bb - C, one chord per bar (2.4 s), so the outro
lands on F at 19.2 s exactly. Every effect is pitched in F major pentatonic
and shares the music's reverb. Event times match launch.html.

    python3 music.py  ->  soundtrack.wav (48 kHz stereo, 22 s)
"""

import wave
from pathlib import Path

import numpy as np

SR = 48000
DUR = 22.0
N = int(SR * DUR)
BEAT = 0.6
BAR = 4 * BEAT
rng = np.random.default_rng(7)


def hz(midi):
    return 440.0 * 2 ** ((midi - 69) / 12)


def buf():
    return np.zeros((N, 2))


def add(bus, sig, t0, pan=0.0, gain=1.0):
    i = int(t0 * SR)
    if i >= N:
        return
    sig = sig[: N - i] * gain
    bus[i : i + len(sig), 0] += sig * np.sqrt(0.5 * (1 - pan))
    bus[i : i + len(sig), 1] += sig * np.sqrt(0.5 * (1 + pan))


def lowpass(x, cutoff):
    """Gentle FFT low-pass (Butterworth-like magnitude, order 2)."""
    spec = np.fft.rfft(x, axis=0)
    f = np.fft.rfftfreq(x.shape[0], 1 / SR)
    resp = 1 / np.sqrt(1 + (f / cutoff) ** 4)
    return np.fft.irfft(spec * resp[:, None] if x.ndim == 2 else spec * resp, n=x.shape[0], axis=0)


def highpass(x, cutoff):
    return x - lowpass(x, cutoff)


# --- voices ------------------------------------------------------------------

def pad_note(f, dur):
    t = np.arange(int(dur * SR)) / SR
    out = np.zeros_like(t)
    for cents in (-6, 6):
        ff = f * 2 ** (cents / 1200) * (1 + 0.0015 * np.sin(2 * np.pi * 4.8 * t))
        ph = 2 * np.pi * np.cumsum(ff) / SR
        for n in range(1, 7):
            out += np.sin(n * ph + n) / n**1.7
    att, rel = 0.45, 0.8
    env = np.minimum(1, t / att) * np.minimum(1, (dur - t) / rel).clip(0)
    return out * env * 0.25


def mallet(f, dur=1.2, bright=1.0):
    t = np.arange(int(dur * SR)) / SR
    s = (np.sin(2 * np.pi * f * t) * np.exp(-t * 4.5)
         + 0.35 * bright * np.sin(2 * np.pi * 2 * f * t) * np.exp(-t * 9)
         + 0.12 * bright * np.sin(2 * np.pi * 3 * f * t) * np.exp(-t * 16))
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


def hat():
    t = np.arange(int(0.06 * SR)) / SR
    return np.diff(rng.standard_normal(len(t) + 1)) * np.exp(-t * 70) * 0.12


def tick():
    t = np.arange(int(0.012 * SR)) / SR
    return np.diff(rng.standard_normal(len(t) + 1)) * np.exp(-t * 500)


def whoosh(dur=0.7):
    n = int(dur * SR)
    t = np.arange(n) / SR
    noise = lowpass(rng.standard_normal(n), 1400)
    env = np.sin(np.pi * (t / dur) ** 0.7) ** 2
    return noise * env


def bass_note(f, dur):
    t = np.arange(int(dur * SR)) / SR
    s = np.sin(2 * np.pi * f * t) + 0.25 * np.sin(4 * np.pi * f * t)
    return s * np.minimum(1, t / 0.01) * np.exp(-t * 1.4) * np.minimum(1, (dur - t) / 0.05).clip(0)


# --- the music ---------------------------------------------------------------

# chord: (bass midi, pad voicing)
F = (41, [53, 57, 60, 64])     # Fmaj7
Dm = (38, [50, 53, 57, 60])    # Dm7
Bb = (34, [46, 50, 53, 57])    # Bbmaj7
C = (36, [52, 55, 60, 62])     # Cadd9
PROG = [F, Dm, Bb, C, F, Dm, Bb, C, F, F]

pad, bass, arp, drums, sfx = buf(), buf(), buf(), buf(), buf()

for b, (root, voicing) in enumerate(PROG):
    t0 = b * BAR
    last = b >= 8
    dur = (DUR - t0) if last else BAR + 0.8
    for m in voicing:
        add(pad, pad_note(hz(m), dur), t0, pan=rng.uniform(-0.4, 0.4))
    # bass: from 3.6 s, root on beats 1 and 3 (one long note in the outro)
    for beat in ([0] if last else [0, 2]):
        tb = t0 + beat * BEAT
        if tb >= 3.6:
            add(bass, bass_note(hz(root), 2.6 if last else 1.15), tb)
    # arpeggio: eighths from 3.3 s until 21.0 s
    tones = [m + 12 for m in voicing]
    pattern = [0, 1, 2, 3, 2, 1, 2, 3]
    for k, p in enumerate(pattern):
        te = t0 + k * BEAT / 2
        if 3.3 <= te < 21.0 and not (last and k % 2):
            vel = 1.0 if k == 0 else 0.7
            add(arp, mallet(hz(tones[p]), 1.0, bright=0.8), te, pan=0.3 if k % 2 else -0.3, gain=vel)
    # drums: 6.6 s to 19.2 s
    for beat in range(4):
        tb = t0 + beat * BEAT
        if 6.6 <= tb < 19.2:
            if beat in (0, 2):
                add(drums, kick(), tb, gain=0.55)
            add(drums, hat(), tb + BEAT / 2, pan=0.25, gain=0.5)

# --- effects, timed to launch.html -------------------------------------------

PENTA = [77, 79, 81, 84, 86, 89]  # F5 G5 A5 C6 D6 F6

# typing in the hook composer
for k, tt in enumerate(np.arange(0.14, 1.57, 0.055)):
    add(sfx, tick(), tt + rng.uniform(-0.01, 0.01), pan=rng.uniform(-0.2, 0.2), gain=rng.uniform(0.15, 0.3))
# send
add(sfx, mallet(hz(84), 0.6, bright=0.4), 2.75, gain=0.55)
# transitions: soft air swells peaking at each cut
for tc in (3.05, 6.3, 12.2, 16.5, 19.15):
    add(sfx, whoosh(0.7), tc - 0.45, gain=0.22)
# faces pop in (reveal and outro)
for row_t in (3.3, 19.3):
    for k in range(5):
        add(sfx, mallet(hz(PENTA[k]), 0.8), row_t + k * 0.1, pan=-0.4 + k * 0.2, gain=0.32)
# preview card appears
add(sfx, mallet(hz(81), 0.5, bright=0.3), 8.0, gain=0.25)
# click and approve
add(sfx, tick(), 9.4, gain=0.2)
for k, m in enumerate((81, 84, 89)):
    add(sfx, bell(hz(m)), 9.45 + k * 0.07, pan=-0.2 + k * 0.2, gain=0.3)
# confirmations
add(sfx, mallet(hz(84), 0.8), 9.8, gain=0.3)
add(sfx, mallet(hz(89), 0.8), 10.05, gain=0.3)
# WhatsApp messages (incoming one sits lower)
add(sfx, mallet(hz(81), 0.6), 13.1, pan=0.3, gain=0.3)
add(sfx, mallet(hz(77), 0.6), 13.6, pan=0.3, gain=0.3)
add(sfx, mallet(hz(84), 0.6), 14.2, pan=0.3, gain=0.3)
# language chips
for k in range(5):
    add(sfx, mallet(hz(PENTA[k] + 12), 0.5, bright=0.5), 14.0 + k * 0.15, pan=-0.5 + k * 0.25, gain=0.16)
# Today rows
for k, m in enumerate((81, 84, 86)):
    add(sfx, mallet(hz(m), 0.6), 16.8 + k * 0.2, gain=0.22)
# CTA
for k, m in enumerate((77, 81, 84, 89)):
    add(sfx, bell(hz(m), 2.5), 19.9 + k * 0.05, pan=-0.3 + k * 0.2, gain=0.25)

# --- mix ---------------------------------------------------------------------

# soften effect transients so nothing pokes out of the mix
sfx = lowpass(sfx, 7000)
sfx = np.tanh(sfx * 2.0) / 2.0
pad = lowpass(pad, 1800)
arp = lowpass(arp, 5000)
drums[:, :] = drums  # kick/hat already shaped
dry = 0.30 * pad + 0.50 * bass + 0.20 * arp + 0.45 * drums + 0.55 * sfx

# hook: hold the pad back slightly so the typing reads
t = np.arange(N) / SR
dry *= (0.75 + 0.25 * np.clip((t - 2.6) / 0.8, 0, 1))[:, None]

# shared room
ir_n = int(2.2 * SR)
ti = np.arange(ir_n) / SR
ir = rng.standard_normal((ir_n, 2)) * np.exp(-ti * 3.2)[:, None]
ir = lowpass(ir, 5000)
ir[: int(0.012 * SR)] = 0  # pre-delay
send = 0.35 * pad + 0.45 * arp + 0.6 * sfx + 0.1 * drums
L = N + ir_n
wet = np.fft.irfft(np.fft.rfft(send, n=L, axis=0) * np.fft.rfft(ir, n=L, axis=0), n=L, axis=0)[:N]
wet *= 0.25 / (np.abs(wet).max() + 1e-9) * np.abs(send).max()
mix = highpass(dry + wet, 30)

# gentle glue and fades
mix = np.tanh(mix / np.abs(mix).max() * 0.8) / np.tanh(0.8)
fade = np.ones(N)
fade[: int(0.03 * SR)] = np.linspace(0, 1, int(0.03 * SR))
fo = (t > 20.8)
fade[fo] = np.cos(np.clip((t[fo] - 20.8) / 1.2, 0, 1) * np.pi / 2) ** 2
mix *= fade[:, None] * 0.89

out = Path(__file__).with_name("soundtrack.wav")
with wave.open(str(out), "wb") as w:
    w.setnchannels(2)
    w.setsampwidth(2)
    w.setframerate(SR)
    w.writeframes((mix * 32767).astype("<i2").tobytes())
print("wrote", out)

if __name__ == "__main__" and __import__("os").environ.get("STEMS"):
    db = lambda x: 20 * np.log10(np.sqrt(np.mean(x**2)) + 1e-12)
    for name, s, g in (("pad", pad, 0.30), ("bass", bass, 0.50), ("arp", arp, 0.20), ("drums", drums, 0.45), ("sfx", sfx, 0.55)):
        print(f"{name:6s} rms {db(s * g):6.1f} dB   peak {20*np.log10(np.abs(s*g).max()):6.1f} dB")
    print("wet/dry", round(db(wet) - db(dry), 1), "dB")
    for a, b in ((0, 3.2), (3.2, 6.5), (6.5, 12.2), (12.2, 16.5), (16.5, 19.2), (19.2, 22)):
        seg = mix[int(a*SR):int(b*SR)]
        print(f"{a:5.1f}-{b:4.1f}s  rms {db(seg):6.1f} dB")
