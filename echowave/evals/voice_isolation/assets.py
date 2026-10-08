"""Everything the evaluation downloads, where from, and under what licence.

Nothing here is committed: models and corpora land in ``VOICE_ISOLATION_CACHE``
(default ``~/.cache/decibyl/voice_isolation``) and are fetched once by
``python -m evals.voice_isolation.assets``. Every item is free for commercial
use; the evaluation audio itself is generated, never redistributed.
"""

from __future__ import annotations

import hashlib
import os
import sys
import tarfile
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path

CACHE = Path(
    os.getenv("VOICE_ISOLATION_CACHE", "~/.cache/decibyl/voice_isolation")
).expanduser()

HF = "https://huggingface.co"


@dataclass(frozen=True)
class Asset:
    name: str
    url: str
    dest: str
    licence: str
    role: str
    unpack: bool = False


ASSETS: tuple[Asset, ...] = (
    # Speaker verification, the two candidates measured.
    Asset(
        "wespeaker-resnet34-lm",
        f"{HF}/Wespeaker/wespeaker-voxceleb-resnet34-LM/resolve/main/voxceleb_resnet34_LM.onnx",
        "models/wespeaker_resnet34_lm.onnx",
        "WeSpeaker code Apache-2.0; weights CC BY 4.0 (trained on VoxCeleb2)",
        "caller voice lock (speaker embedding)",
    ),
    Asset(
        "wespeaker-ecapa512-lm",
        f"{HF}/Wespeaker/wespeaker-ecapa-tdnn512-LM/resolve/main/voxceleb_ECAPA512_LM.onnx",
        "models/wespeaker_ecapa512_lm.onnx",
        "WeSpeaker code Apache-2.0; weights CC BY 4.0 (trained on VoxCeleb2)",
        "caller voice lock (speaker embedding, faster candidate)",
    ),
    # Transcriber stand-in: streaming partial results give the word counts
    # MinWords reads from a real transcriber's interim results.
    Asset(
        "vosk-small-en-in",
        "https://alphacephei.com/vosk/models/vosk-model-small-en-in-0.4.zip",
        "vosk/en-in.zip",
        "Apache-2.0",
        "interim transcripts, English",
        unpack=True,
    ),
    Asset(
        "vosk-small-hi",
        "https://alphacephei.com/vosk/models/vosk-model-small-hi-0.22.zip",
        "vosk/hi.zip",
        "Apache-2.0",
        "interim transcripts, Hindi and Hinglish",
        unpack=True,
    ),
    # Speech: Kokoro TTS for Hindi, Hinglish and English voices.
    Asset(
        "kokoro-82m",
        f"{HF}/onnx-community/Kokoro-82M-v1.0-ONNX/resolve/main/onnx/model.onnx",
        "kokoro/model.onnx",
        "Apache-2.0 (Kokoro-82M weights); kokoro-onnx runtime MIT; espeak-ng GPL-3.0 (eval only, not shipped)",
        "synthesised callers and background talkers",
    ),
    # Speech: real people, English.
    Asset(
        "librispeech-test-clean",
        "https://www.openslr.org/resources/12/test-clean.tar.gz",
        "librispeech/test-clean.tar.gz",
        "CC BY 4.0",
        "real English callers and background talkers",
        unpack=True,
    ),
    # Speech: real people, Hinglish. MUCS 2021 code-switched spoken tutorials.
    Asset(
        "mucs-hinglish-test",
        "https://www.openslr.org/resources/104/Hindi-English_test.tar.gz",
        "mucs/Hindi-English_test.tar.gz",
        "CC BY-SA 4.0 (evaluation only; nothing derived is redistributed)",
        "real Hinglish callers and background talkers",
        unpack=True,
    ),
    # Noise beds: DEMAND, recorded multichannel environments.
    Asset(
        "demand-straffic",
        "https://zenodo.org/api/records/1227121/files/STRAFFIC_16k.zip/content",
        "demand/STRAFFIC_16k.zip",
        "CC BY 4.0",
        "traffic",
        unpack=True,
    ),
    Asset(
        "demand-pcafeter",
        "https://zenodo.org/api/records/1227121/files/PCAFETER_16k.zip/content",
        "demand/PCAFETER_16k.zip",
        "CC BY 4.0",
        "cafeteria babble",
        unpack=True,
    ),
    Asset(
        "demand-dliving",
        "https://zenodo.org/api/records/1227121/files/DLIVING_16k.zip/content",
        "demand/DLIVING_16k.zip",
        "CC BY 4.0",
        "living-room bed under the television",
        unpack=True,
    ),
)

KOKORO_VOICES = (
    "hf_alpha hf_beta hm_omega hm_psi af_heart af_bella af_nicole af_sarah "
    "am_adam am_michael am_eric bf_emma bm_george bm_lewis"
).split()


def path(rel: str) -> Path:
    return CACHE / rel


def _fetch(url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    with urllib.request.urlopen(url) as response, open(tmp, "wb") as out:
        while chunk := response.read(1 << 20):
            out.write(chunk)
    tmp.rename(dest)


def fetch_all() -> None:
    for asset in ASSETS:
        dest = path(asset.dest)
        if not dest.exists():
            print(f"fetching {asset.name}", file=sys.stderr)
            _fetch(asset.url, dest)
        if asset.unpack and not (dest.parent / (dest.name + ".done")).exists():
            if dest.name.endswith(".zip"):
                with zipfile.ZipFile(dest) as archive:
                    archive.extractall(dest.parent)
            else:
                with tarfile.open(dest) as archive:
                    archive.extractall(dest.parent, filter="data")
            (dest.parent / (dest.name + ".done")).touch()
    voices = path("kokoro/voices.npz")
    if not voices.exists():
        import numpy as np

        arrays = {}
        for name in KOKORO_VOICES:
            raw = path(f"kokoro/{name}.bin")
            if not raw.exists():
                _fetch(
                    f"{HF}/onnx-community/Kokoro-82M-v1.0-ONNX/resolve/main/voices/{name}.bin",
                    raw,
                )
            arrays[name] = np.fromfile(raw, dtype=np.float32).reshape(-1, 1, 256)
        np.savez(voices, **arrays)


def sha256(file: Path) -> str:
    digest = hashlib.sha256()
    with open(file, "rb") as handle:
        while chunk := handle.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


def licence_table() -> str:
    rows = ["| Asset | Licence | Used for |", "| --- | --- | --- |"]
    rows += [f"| {a.name} | {a.licence} | {a.role} |" for a in ASSETS]
    return "\n".join(rows)


if __name__ == "__main__":
    fetch_all()
    print(licence_table())
