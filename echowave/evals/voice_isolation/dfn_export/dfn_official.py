"""Load the official DeepFilterNet3 checkpoint into the official `df` (deepfilternet 0.5.6) classes.

Works with the genuine `libdf` (DeepFilterLib wheel, py<=3.11) or with the numpy stub
`libdf_stub/libdf.py` (py3.13), depending on which `libdf` is first on sys.path.
`df.io` is replaced by an empty module because it imports torchaudio's removed
`torchaudio.backend` API; it is only used for file I/O, never for inference.
"""

import os
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
MODEL_DIR = os.environ.get(
    "DFN_MODEL_DIR", os.path.join(HERE, "work", "DeepFilterNet3")
)
CKPT = os.path.join(MODEL_DIR, "checkpoints", "model_120.ckpt.best")


def _stub_df_io():
    if "df.io" not in sys.modules:
        m = types.ModuleType("df.io")
        m.load_audio = m.resample = m.save_audio = None
        sys.modules["df.io"] = m


def load_official(model_dir: str = MODEL_DIR):
    _stub_df_io()
    from loguru import logger

    logger.remove()
    import torch
    from df.checkpoint import load_model
    from df.config import config
    from df.model import ModelParams
    from libdf import DF

    config.load(
        os.path.join(model_dir, "config.ini"),
        config_must_exist=True,
        allow_defaults=True,
        allow_reload=True,
    )
    p = ModelParams()
    df_state = DF(
        sr=p.sr,
        fft_size=p.fft_size,
        hop_size=p.hop_size,
        nb_bands=p.nb_erb,
        min_nb_erb_freqs=p.min_nb_freqs,
    )
    model, epoch = load_model(
        os.path.join(model_dir, "checkpoints"), df_state, epoch="best"
    )
    model.eval()
    torch.set_grad_enabled(False)
    return model, df_state, p, epoch


def offline_enhance(model, df_state, audio_48k):
    """Official offline path: df.enhance.enhance (pads 960 samples, removes 480-sample delay)."""
    import numpy as np
    import torch
    from df.enhance import enhance

    x = torch.from_numpy(np.asarray(audio_48k, np.float32)[None])
    return enhance(model, df_state, x, pad=True).numpy()[0]
