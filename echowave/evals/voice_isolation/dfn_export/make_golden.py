"""Run the *genuine* official pipeline (DeepFilterLib 0.5.6 Rust extension + deepfilternet 0.5.6)
on the signals in an npz file and save features + offline enhanced outputs.

Must be run with the py3.11 venv that has the real `libdf` installed:
    venv311/bin/python -I make_golden.py in.npz out.npz
"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dfn_official import _stub_df_io, load_official, offline_enhance  # noqa: E402


def main(inp, outp):
    import libdf
    import torch

    _stub_df_io()
    from df.enhance import df_features

    assert "libdf_stub" not in libdf.__file__, libdf.__file__
    model, df_state, p, _ = load_official()
    d = np.load(inp)
    res = {"libdf_file": np.array(libdf.__file__)}
    for k in d.files:
        x = d[k].astype(np.float32)
        res[f"{k}__offline"] = offline_enhance(model, df_state, x)
        xp = torch.nn.functional.pad(torch.from_numpy(x[None]), (0, p.fft_size))
        spec, erb_feat, spec_feat = df_features(xp, df_state, p.nb_df)
        res[f"{k}__spec"] = spec.numpy()
        res[f"{k}__erb"] = erb_feat.numpy()
        res[f"{k}__cplx"] = spec_feat.numpy()
    np.savez(outp, **res)
    print("golden written with", libdf.__file__)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
