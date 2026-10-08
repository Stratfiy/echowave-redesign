"""The speech and noise the scenes are built from.

Callers and background talkers in three languages:

* **Hindi** and **Hinglish**: Kokoro-82M's four Hindi voices (two women, two
  men) reading the lines below. Hinglish lines mix Devanagari and English words
  the way callers actually speak; espeak-ng's Hindi front end reads both.
* **English**: Kokoro's American and British voices.
* **Real people**: LibriSpeech test-clean (English) and MUCS 2021 (OpenSLR
  104, Hindi-English code-switched speech from 30 speakers). These matter
  most: Kokoro's same-gender voices sound alike to a speaker-verification
  network (cosine 0.5-0.67, where two real strangers score about 0.1), so the
  synthetic scenes are a sound-alike worst case, not a typical room.

Every line is original to this file. Synthesis is cached by text and voice, so
the corpus is built once and every run after is identical.
"""

from __future__ import annotations

import glob
import hashlib
import os
from functools import lru_cache

import numpy as np
import soundfile as sf
import soxr

from evals.voice_isolation.assets import path

RATE = 16000

#: What a caller says while the agent waits: answering the greeting.
CALLER_LINES = {
    "en": [
        "Hi, I am calling about my order, it still has not arrived.",
        "My name is Rohan Mehta and the order number is four five two one.",
        "I paid last week and the tracking page has not moved since Monday.",
        "Can you tell me when the delivery will reach Andheri East?",
        "I would also like to change the phone number on my account.",
        "Yes, that is the right address, flat twelve, second floor.",
    ],
    "hi": [
        "नमस्ते, मैं अपने ऑर्डर के बारे में बात करना चाहता हूँ, वह अभी तक नहीं आया।",
        "मेरा नाम सुनीता शर्मा है और मेरा ऑर्डर नंबर चार पाँच दो एक है।",
        "मैंने पिछले हफ्ते पैसे दे दिए थे लेकिन अभी तक कोई खबर नहीं है।",
        "कृपया बताइए कि सामान कब तक पहुँच जाएगा।",
        "मुझे अपने खाते का फ़ोन नंबर भी बदलवाना है।",
        "हाँ, पता सही है, मकान नंबर बारह, दूसरी मंज़िल।",
    ],
    "hinglish": [
        "Hello, मेरा order अभी तक deliver नहीं हुआ, please check कर दीजिए।",
        "मेरा नाम Amit है, order number है four five two one।",
        "Payment तो last week ही हो गया था, फिर भी tracking update नहीं हुई।",
        "Delivery कब तक हो जाएगी, कुछ idea है आपको?",
        "और मुझे account में phone number भी change करवाना है।",
        "हाँ, address correct है, flat number twelve, second floor।",
    ],
}

#: What a caller says to cut the agent off.
BARGE_IN_LINES = {
    "en": [
        "Wait, wait, that is not what I asked, I need the delivery date.",
        "Sorry, stop, I already told you my order number.",
        "Hold on, can you just connect me to a person please?",
    ],
    "hi": [
        "रुकिए रुकिए, मैंने यह नहीं पूछा, मुझे डिलीवरी की तारीख चाहिए।",
        "माफ़ कीजिए, मैंने अपना ऑर्डर नंबर पहले ही बता दिया है।",
        "एक मिनट, कृपया मेरी बात किसी व्यक्ति से करवा दीजिए।",
    ],
    "hinglish": [
        "Wait wait, मैंने ये नहीं पूछा, मुझे delivery date चाहिए।",
        "Sorry, रुकिए, मैंने order number already बता दिया है।",
        "एक minute, please मुझे किसी person से connect कर दीजिए।",
    ],
}

#: What the people around the caller are saying to each other.
CHATTER_LINES = {
    "en": [
        "Did you put the milk back in the fridge or is it still out?",
        "The bus is late again, we should have taken the train today.",
        "I told him to call back after lunch but he never listens.",
        "Can you turn that down a little, I cannot hear anything.",
        "We need to leave in ten minutes if we want to make it on time.",
        "She said the meeting moved to Thursday, did you get the email?",
        "Put the bags over there, next to the door, thank you.",
        "Has anyone seen my keys, I left them right here a minute ago.",
    ],
    "hi": [
        "दूध फ्रिज में वापस रखा या अभी भी बाहर ही पड़ा है?",
        "बस फिर से लेट है, आज हमें ट्रेन से जाना चाहिए था।",
        "मैंने उससे कहा था खाने के बाद फ़ोन करना, पर वह सुनता ही नहीं।",
        "थोड़ा आवाज़ कम करो, मुझे कुछ सुनाई नहीं दे रहा।",
        "अगर समय पर पहुँचना है तो दस मिनट में निकलना होगा।",
        "उसने कहा मीटिंग गुरुवार को हो गई है, तुम्हें बताया क्या?",
        "थैले वहाँ दरवाज़े के पास रख दो, धन्यवाद।",
        "किसी ने मेरी चाबी देखी है, अभी यहीं रखी थी।",
    ],
    "hinglish": [
        "Milk fridge में वापस रखा या अभी भी बाहर है?",
        "Bus फिर से late है yaar, आज train लेनी चाहिए थी।",
        "मैंने बोला था lunch के बाद call करना, but वो सुनता ही नहीं।",
        "थोड़ा volume कम करो, मुझे कुछ सुनाई नहीं दे रहा।",
        "Time पे पहुँचना है तो ten minutes में निकलना पड़ेगा।",
        "उसने बोला meeting Thursday को shift हो गई, email आया क्या?",
        "Bags वहाँ door के पास रख दो, thank you।",
        "किसी ने मेरी keys देखी हैं, अभी यहीं रखी थीं।",
    ],
}

#: Voices per language: (callers, background talkers). Disjoint, so a caller
#: is never their own background.
VOICES = {
    "en": (["af_heart", "am_michael", "bf_emma"], ["am_adam", "af_bella", "bm_george"]),
    "hi": (["hf_alpha", "hm_omega"], ["hm_psi", "hf_beta"]),
    "hinglish": (["hm_psi", "hf_beta"], ["hf_alpha", "hm_omega"]),
}

#: The voice on the television, never a caller's.
TV_VOICES = {"en": "bm_lewis", "hi": "am_eric", "hinglish": "af_nicole"}

KOKORO_LANG = {"en": "en-us", "hi": "hi", "hinglish": "hi"}


@lru_cache(maxsize=1)
def _kokoro():
    from kokoro_onnx import Kokoro

    return Kokoro(str(path("kokoro/model.onnx")), str(path("kokoro/voices.npz")))


def tts(text: str, voice: str, language: str) -> np.ndarray:
    """``text`` in ``voice`` at 16 kHz, cached on disk."""
    key = hashlib.sha1(f"{voice}|{language}|{text}".encode()).hexdigest()[:16]
    cached = path(f"tts/{key}.npy")
    if cached.exists():
        return np.load(cached)
    lang = KOKORO_LANG[language]
    if voice.startswith("b") and lang == "en-us":
        lang = "en-gb"
    audio, rate = _kokoro().create(text, voice=voice, lang=lang, speed=1.0)
    audio = soxr.resample(audio.astype(np.float32), rate, RATE).astype(np.float32)
    cached.parent.mkdir(parents=True, exist_ok=True)
    np.save(cached, audio)
    return audio


# --- LibriSpeech -------------------------------------------------------------


def libri_speakers() -> list[str]:
    root = path("librispeech/LibriSpeech/test-clean")
    return sorted(os.path.basename(p) for p in glob.glob(str(root / "*")))


def libri_utterances(
    speaker: str, n: int, *, min_secs: float = 2.0
) -> list[np.ndarray]:
    root = path("librispeech/LibriSpeech/test-clean")
    out = []
    for flac in sorted(glob.glob(str(root / speaker / "*" / "*.flac"))):
        audio, rate = sf.read(flac, dtype="float32")
        assert rate == RATE
        if len(audio) >= min_secs * RATE:
            out.append(audio[: int(6 * RATE)])
        if len(out) >= n:
            break
    return out


# --- MUCS 2021 Hindi-English (OpenSLR 104) -----------------------------------


@lru_cache(maxsize=1)
def _mucs_index() -> dict[str, list[tuple[str, float, float]]]:
    """speaker -> [(recording, start, end)] from the Kaldi segment files."""
    root = path("mucs/test/transcripts")
    rec_of = {}
    for line in open(root / "segments", encoding="utf-8"):
        utt, rec, start, end = line.split()
        rec_of[utt] = (rec, float(start), float(end))
    by_speaker: dict[str, list] = {}
    for line in open(root / "utt2spk", encoding="utf-8"):
        utt, spk = line.split()
        by_speaker.setdefault(spk, []).append(rec_of[utt])
    return by_speaker


#: The test set's 30 speaker ids are spoken-tutorial recordings, and most are
#: the same few narrators: a speaker-verification network puts them in two
#: groups (single-linkage at cosine 0.55 on telephony audio; ids in the same
#: group score 0.8-0.9 against each other). So a scene takes its caller from
#: one group and its background from the other -- two real voices, not thirty.
MUCS_VOICES = (
    (
        "103085 124478 137494 146881 161768 270589 323507 347099 453832 508088 "
        "521245 598753 610773 628028 834022 847066 957491"
    ).split(),
    (
        "103725 118638 133511 318923 360746 388577 408467 478254 656144 656852 "
        "791308 918821 921151"
    ).split(),
)


def mucs_speakers() -> list[str]:
    return sorted(_mucs_index())


def mucs_utterances(speaker: str, n: int, *, secs: float = 4.0) -> list[np.ndarray]:
    """``n`` clips of ``speaker``, each the first ``secs`` of a segment."""
    out = []
    for rec, start, end in _mucs_index()[speaker][1:]:  # skip the title card
        if end - start < secs:
            continue
        audio, rate = sf.read(
            str(path(f"mucs/test/{rec}.wav")),
            start=int(start * RATE),
            stop=int((start + secs) * RATE),
            dtype="float32",
        )
        assert rate == RATE
        if np.sqrt(np.mean(audio**2)) > 1e-3:
            out.append(audio)
        if len(out) >= n:
            break
    return out


# --- Noise -------------------------------------------------------------------


def demand(name: str, seconds: float, rng: np.random.Generator) -> np.ndarray:
    """A random stretch of a DEMAND environment (channel 1), 16 kHz."""
    wav = path(f"demand/{name}/ch01.wav")
    audio, rate = sf.read(wav, dtype="float32")
    assert rate == RATE
    n = int(seconds * RATE)
    start = int(rng.integers(0, max(1, len(audio) - n)))
    return audio[start : start + n]


def fan(seconds: float, rng: np.random.Generator) -> np.ndarray:
    """A pedestal fan: broadband turbulence with a low tilt, a blade-pass hum
    and its harmonics, slowly wobbling as it oscillates."""
    n = int(seconds * RATE)
    white = rng.standard_normal(n).astype(np.float32)
    spectrum = np.fft.rfft(white)
    freqs = np.fft.rfftfreq(n, 1 / RATE)
    spectrum /= np.sqrt(np.maximum(freqs, 30.0) / 30.0)  # pink-ish
    spectrum *= 1.0 / (1.0 + (freqs / 2500.0) ** 2)  # muffled top
    noise = np.fft.irfft(spectrum, n).astype(np.float32)
    t = np.arange(n) / RATE
    blade = 23.0  # Hz, 3 blades at ~460 rpm
    hum = sum(
        np.sin(2 * np.pi * blade * k * t + rng.uniform(0, 6.28)) / k
        for k in range(1, 6)
    )
    wobble = 1.0 + 0.25 * np.sin(2 * np.pi * 0.08 * t)
    out = (noise / (np.std(noise) + 1e-9) + 0.3 * hum) * wobble
    return (out / (np.std(out) + 1e-9)).astype(np.float32)


def television(language: str, seconds: float, rng: np.random.Generator) -> np.ndarray:
    """A television across the room: a presenter talking over a music bed,
    through a small loudspeaker, in a living room."""
    lines = CHATTER_LINES[language]
    voice = TV_VOICES[language]
    speech = []
    total = 0
    while total < seconds * RATE:
        line = lines[int(rng.integers(0, len(lines)))]
        clip = tts(line, voice, language)
        speech.append(clip)
        speech.append(np.zeros(int(rng.uniform(0.15, 0.4) * RATE), dtype=np.float32))
        total += len(clip)
    speech = np.concatenate(speech)[: int(seconds * RATE)]
    n = len(speech)
    t = np.arange(n) / RATE
    chords = [(220.0, 277.2, 329.6), (196.0, 246.9, 293.7), (174.6, 220.0, 261.6)]
    music = np.zeros(n, dtype=np.float32)
    for i, chord in enumerate(chords * int(seconds // 6 + 1)):
        start, end = int(i * 2.0 * RATE), int((i + 1) * 2.0 * RATE)
        if start >= n:
            break
        seg = t[start:end]
        music[start:end] = sum(np.sin(2 * np.pi * f * seg) for f in chord) / 3
    tv = speech / (np.std(speech) + 1e-9) + 0.35 * music / (np.std(music) + 1e-9)
    # Small loudspeaker: nothing below ~200 Hz, rolled off above ~6 kHz.
    spectrum = np.fft.rfft(tv)
    freqs = np.fft.rfftfreq(n, 1 / RATE)
    spectrum *= (freqs / 200.0) ** 2 / (1 + (freqs / 200.0) ** 2)
    spectrum *= 1 / (1 + (freqs / 6000.0) ** 4)
    tv = np.fft.irfft(spectrum, n).astype(np.float32)
    bed = demand("DLIVING", seconds, rng)[:n]
    tv = tv / (np.std(tv) + 1e-9) + 0.3 * bed / (np.std(bed) + 1e-9)
    return (tv / (np.std(tv) + 1e-9)).astype(np.float32)
