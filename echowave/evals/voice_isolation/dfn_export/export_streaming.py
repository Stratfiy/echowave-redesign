#!/usr/bin/env python
"""Export the official DeepFilterNet3 checkpoint as a true single-hop streaming ONNX graph.

The official model classes (deepfilternet 0.5.6, df/deepfilternet3.py) are loaded with the
official checkpoint (DeepFilterNet3/checkpoints/model_120.ckpt.best). `DfNet3Streaming` re-uses
the *same submodule instances* (and therefore weights) but evaluates them for one 10 ms hop,
carrying every piece of temporal context as an explicit state tensor:

  * erb_hist  [1,1,2,32]     last 2 ERB-feature frames      (enc.erb_conv0 time kernel 3)
  * spec_feat_hist [1,2,2,96] last 2 complex-feature frames (enc.df_conv0  time kernel 3)
  * c0_hist   [1,64,4,96]    last 4 outputs of enc.df_conv0 (df_dec.df_convp time kernel 5)
  * spec_hist [1,1,4,481,2]  last 4 raw spectrum frames      (deep filter order 5:
                                                              2 past + current + 2 lookahead)
  * h_enc [1,1,256], h_erb [2,1,256], h_df [2,1,256]         GRU hidden states

Per call the graph consumes the features + raw spectrum of the newest STFT frame k and emits
the enhanced spectrum of frame k-2 (the model's 2-frame lookahead: conv_lookahead =
df_lookahead = 2). All other temporal operators of DFN3 have a time kernel of 1.

Usage:  ./build.sh OUT_DIR   (fetches the checkpoint and package, then runs this)
"""
import hashlib
import json
import os
import sys

import numpy as np
import torch
from torch import Tensor, nn

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
try:  # prefer the genuine libdf if importable, else the numpy stub
    import libdf  # noqa: F401
except ImportError:
    sys.path.insert(0, os.path.join(HERE, "libdf_stub"))
from dfn_official import CKPT, load_official  # noqa: E402

ONNX_PATH = os.environ.get("DFN_ONNX_OUT", os.path.join(HERE, "dfn3_streaming.onnx"))

STATE_SPECS = [  # name, shape
    ("erb_hist", (1, 1, 2, 32)),
    ("spec_feat_hist", (1, 2, 2, 96)),
    ("c0_hist", (1, 64, 4, 96)),
    ("spec_hist", (1, 1, 4, 481, 2)),
    ("h_enc", (1, 1, 256)),
    ("h_erb", (2, 1, 256)),
    ("h_df", (2, 1, 256)),
]


def _strip_time_pad(seq: nn.Sequential) -> nn.Sequential:
    """Conv2dNormAct = [ConstantPad2d(causal time pad), Conv2d, (1x1 Conv2d), BN, act].
    In streaming the explicit history replaces the zero time padding."""
    layers = [m for m in seq if not isinstance(m, nn.ConstantPad2d)]
    return nn.Sequential(*layers)


class DfNet3Streaming(nn.Module):
    def __init__(self, model):
        super().__init__()
        self.m = model
        enc = model.enc
        self.erb_conv0 = _strip_time_pad(enc.erb_conv0)
        self.df_conv0 = _strip_time_pad(enc.df_conv0)
        self.df_convp = _strip_time_pad(model.df_dec.df_convp)
        self.nb_df = model.nb_df
        self.df_order = model.df_order
        assert model.df_lookahead == 2 and self.df_order == 5

    def forward(self, feat_erb: Tensor, feat_spec: Tensor, spec: Tensor,
                erb_hist: Tensor, spec_feat_hist: Tensor, c0_hist: Tensor, spec_hist: Tensor,
                h_enc: Tensor, h_erb: Tensor, h_df: Tensor):
        m = self.m
        enc = m.enc
        # ---------------- Encoder (Encoder.forward, one time step) ----------------
        erb_win = torch.cat([erb_hist, feat_erb], dim=2)            # [1,1,3,32]
        cpl_win = torch.cat([spec_feat_hist, feat_spec], dim=2)     # [1,2,3,96]
        e0 = self.erb_conv0(erb_win)                                # [1,64,1,32]
        e1 = enc.erb_conv1(e0)
        e2 = enc.erb_conv2(e1)
        e3 = enc.erb_conv3(e2)                                      # [1,64,1,8]
        c0 = self.df_conv0(cpl_win)                                 # [1,64,1,96]
        c1 = enc.df_conv1(c0)
        cemb = c1.permute(0, 2, 3, 1).flatten(2)
        cemb = enc.df_fc_emb(cemb)
        emb = e3.permute(0, 2, 3, 1).flatten(2)
        emb = enc.combine(emb, cemb)
        emb, h_enc_n = enc.emb_gru(emb, h_enc)
        lsnr = enc.lsnr_fc(emb) * enc.lsnr_scale + enc.lsnr_offset
        # ---------------- ERB decoder (ErbDecoder.forward) ----------------
        dec = m.erb_dec
        b, _, t, f8 = e3.shape
        embd, h_erb_n = dec.emb_gru(emb, h_erb)
        embd = embd.view(b, t, f8, -1).permute(0, 3, 1, 2)
        d3 = dec.convt3(dec.conv3p(e3) + embd)
        d2 = dec.convt2(dec.conv2p(e2) + d3)
        d1 = dec.convt1(dec.conv1p(e1) + d2)
        mask = dec.conv0_out(dec.conv0p(e0) + d1)                   # [1,1,1,32]
        # ---------------- DF decoder (DfDecoder.forward) ----------------
        dd = m.df_dec
        c, h_df_n = dd.df_gru(emb, h_df)
        c = c + dd.df_skip(emb)
        c0_win = torch.cat([c0_hist, c0], dim=2)                    # [1,64,5,96]
        cp = self.df_convp(c0_win).permute(0, 2, 3, 1)              # [1,1,96,10]
        c = dd.df_out(c)
        coefs = c.view(1, 1, self.nb_df, dd.df_out_ch) + cp        # [1,1,96,10]
        coefs = m.df_out_transform(coefs)                           # [1,5,1,96,2]
        # ---------------- ERB mask on frame k-2 (Mask.forward) ----------------
        spec_win = torch.cat([spec_hist, spec], dim=2)              # [1,1,5,481,2] frames k-4..k
        spec_c = spec_win[:, :, 2:3]                                # frame k-2 (=tau)
        spec_m = m.mask(spec_c, mask)                               # [1,1,1,481,2]
        # ---------------- Deep filter (multiframe.DF, order 5, lookahead 2) ----------------
        sf = spec_win[:, 0, :, : self.nb_df]                        # [1,5,96,2]
        cf = coefs[:, :, 0]                                         # [1,5,96,2]
        re = (sf[..., 0] * cf[..., 0] - sf[..., 1] * cf[..., 1]).sum(1)
        im = (sf[..., 0] * cf[..., 1] + sf[..., 1] * cf[..., 0]).sum(1)
        low = torch.stack([re, im], -1).view(1, 1, 1, self.nb_df, 2)
        spec_e = torch.cat([low, spec_m[:, :, :, self.nb_df:]], dim=3)
        return (spec_e, lsnr,
                erb_win[:, :, 1:], cpl_win[:, :, 1:], c0_win[:, :, 1:], spec_win[:, :, 1:],
                h_enc_n, h_erb_n, h_df_n)


def zero_states():
    return [torch.zeros(s) for _, s in STATE_SPECS]


@torch.no_grad()
def torch_stream_run(sm, spec, feat_erb, feat_spec):
    """Reference loop of the torch streaming wrapper reproducing offline DfNet.forward."""
    T = spec.shape[2]
    st = zero_states()
    out = torch.zeros_like(spec)
    fs = feat_spec.squeeze(1).permute(0, 3, 1, 2)  # [1,2,T,96]
    for k in range(T + 2):
        if k < T:
            fe, fc, sp = feat_erb[:, :, k:k + 1], fs[:, :, k:k + 1], spec[:, :, k:k + 1]
        else:  # offline pads features and spectrum with zeros after the end
            fe, fc, sp = torch.zeros(1, 1, 1, 32), torch.zeros(1, 2, 1, 96), torch.zeros(1, 1, 1, 481, 2)
        if k < 2:  # offline drops the first 2 feature frames (pad_feat); only the spectrum enters
            st[3] = torch.cat([st[3][:, :, 1:], sp], 2)
            continue
        r = sm(fe, fc, sp, *st)
        out[:, :, k - 2] = r[0][:, :, 0]
        st = list(r[2:])
    return out


def main():
    model, df_state, p, epoch = load_official()
    import libdf
    print("libdf used:", libdf.__file__)
    sha = hashlib.sha256(open(CKPT, "rb").read()).hexdigest()
    print("checkpoint", CKPT, "epoch", epoch, "sha256", sha)

    sm = DfNet3Streaming(model).eval()

    # ---- torch-level parity: streaming wrapper vs. official full-sequence forward ----
    from df.enhance import df_features
    rng = np.random.default_rng(1)
    n = 48000 * 2
    tt = np.arange(n) / 48000
    x = (0.05 * rng.standard_normal(n) + 0.2 * np.sin(2 * np.pi * 440 * tt) * (tt % 0.5 < 0.25)).astype(np.float32)
    spec, erb_feat, spec_feat = df_features(torch.from_numpy(x[None]), df_state, p.nb_df)
    off = model(spec.clone(), erb_feat, spec_feat)[0]
    strm = torch_stream_run(sm, spec, erb_feat, spec_feat)
    d = (off - strm).abs().max().item()
    print(f"torch streaming wrapper vs offline DfNet.forward: max|diff| = {d:.3e} (max |off| {off.abs().max():.3e})")
    assert d < 1e-4, d

    # ---- ONNX export ----
    in_names = ["feat_erb", "feat_spec", "spec"] + [n for n, _ in STATE_SPECS]
    out_names = ["spec_enh", "lsnr"] + [n + "_out" for n, _ in STATE_SPECS]
    dummy = (torch.zeros(1, 1, 1, 32), torch.zeros(1, 2, 1, 96), torch.zeros(1, 1, 1, 481, 2), *zero_states())
    kw = dict(input_names=in_names, output_names=out_names, opset_version=17, do_constant_folding=True)
    try:
        torch.onnx.export(sm, dummy, ONNX_PATH, dynamo=False, **kw)
    except TypeError:
        torch.onnx.export(sm, dummy, ONNX_PATH, **kw)
    import onnx
    om = onnx.load(ONNX_PATH)
    onnx.checker.check_model(om)
    meta = {"checkpoint_sha256": sha, "checkpoint": "DeepFilterNet3/checkpoints/model_120.ckpt.best (epoch %s)" % epoch,
            "source": "https://raw.githubusercontent.com/Rikorose/DeepFilterNet/v0.5.6/models/DeepFilterNet3.zip",
            "states": json.dumps({n: list(s) for n, s in STATE_SPECS}), "hop": "480", "sr": "48000",
            "lookahead_frames": "2"}
    for k, v in meta.items():
        e = om.metadata_props.add()
        e.key, e.value = k, v
    onnx.save(om, ONNX_PATH)
    print("wrote", ONNX_PATH, os.path.getsize(ONNX_PATH), "bytes; opset", om.opset_import[0].version)
    print("ops:", sorted({nd.op_type for nd in om.graph.node}))

    # ---- ORT vs torch wrapper parity, one hop at a time ----
    import onnxruntime as ort
    so = ort.SessionOptions()
    so.intra_op_num_threads = 1
    so.inter_op_num_threads = 1
    sess = ort.InferenceSession(ONNX_PATH, so, providers=["CPUExecutionProvider"])
    st_t = zero_states()
    st_o = [s.numpy() for s in st_t]
    fs = spec_feat.squeeze(1).permute(0, 3, 1, 2)
    md = 0.0
    for k in range(2, spec.shape[2]):
        fe, fc, sp = erb_feat[:, :, k:k + 1], fs[:, :, k:k + 1].contiguous(), spec[:, :, k:k + 1]
        rt = sm(fe, fc, sp, *st_t)
        ro = sess.run(None, dict(zip(in_names, [fe.numpy(), fc.numpy(), sp.numpy(), *st_o])))
        md = max(md, float(np.abs(rt[0].numpy() - ro[0]).max()))
        st_t, st_o = list(rt[2:]), ro[2:]
    print(f"ORT vs torch wrapper per-hop spec max|diff| = {md:.3e}")
    with open(os.path.join(os.path.dirname(ONNX_PATH), "export_info.json"), "w") as f:
        json.dump({"checkpoint_sha256": sha, "epoch": epoch, "torch_stream_vs_offline_maxdiff": d,
                   "ort_vs_torch_maxdiff": md, "onnx_bytes": os.path.getsize(ONNX_PATH),
                   "torch": torch.__version__, "onnx": onnx.__version__, "onnxruntime": ort.__version__}, f, indent=1)


if __name__ == "__main__":
    main()
