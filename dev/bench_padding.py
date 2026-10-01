"""Check that padding encoder features to fixed buckets keeps outputs identical, and measure steady-state cost."""

import json
import re
import time
from pathlib import Path

import jiwer
import numpy as np
import onnxruntime as ort
import soundfile as sf

ort.set_default_logger_severity(3)
ort.preload_dlls(directory="")
import onnx_asr  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
MODEL_DIR = ROOT / "models" / "parakeet-tdt-0.6b-v2"
DATA = ROOT / "tests" / "data" / "dummy"
refs = json.loads((DATA / "refs.json").read_text())
wavs = [sf.read(DATA / r["file"], dtype="float32")[0] for r in refs]
CUDA = ("CUDAExecutionProvider", {"device_id": 0, "cudnn_conv_algo_search": "HEURISTIC"})


def norm(s):
    s = re.sub(r"[^a-z0-9' ]+", " ", s.lower())
    return re.sub(r"\s+", " ", s).strip()


model = onnx_asr.load_model("nemo-parakeet-tdt-0.6b-v2", MODEL_DIR, providers=[CUDA, "CPUExecutionProvider"])
asr = model.asr


def decode(w, pad_to_frames=None):
    feats, flens = asr._preprocessor(w[None, :], np.array([len(w)], dtype=np.int64))
    if pad_to_frames is not None and feats.shape[2] < pad_to_frames:
        feats = np.pad(feats, ((0, 0), (0, 0), (0, pad_to_frames - feats.shape[2])))
    t = time.perf_counter()
    enc, enc_lens = asr._encode(feats, flens)
    te = time.perf_counter() - t
    res = list(map(asr._decode_tokens, *zip(*asr._decoding(enc, enc_lens), strict=False)))[0]
    return res, te


# 1) Equivalence: exact vs padded to 2000 frames (20 s)
same = 0
hyps_exact, hyps_pad, gts = [], [], []
for w, r in zip(wavs, refs):
    if len(w) > 19.5 * 16000:
        continue
    a, _ = decode(w)
    b, _ = decode(w, 2000)
    same += a.text == b.text
    hyps_exact.append(norm(a.text))
    hyps_pad.append(norm(b.text))
    gts.append(norm(r["text"]))
    if a.text != b.text:
        print("DIFF:\n  exact:", a.text, "\n  pad  :", b.text)
print(f"identical text {same}/{len(gts)}; WER exact={jiwer.wer(gts, hyps_exact) * 100:.2f}% pad={jiwer.wer(gts, hyps_pad) * 100:.2f}%")

# 2) Steady-state encoder cost per bucket size (same shape repeated)
w = wavs[2]
for sec in (5, 10, 15, 20, 30):
    frames = sec * 100
    ww = w[: min(len(w), frames * 160 - 1600)]
    for _ in range(3):
        decode(ww, frames)
    ts = [decode(ww, frames)[1] * 1000 for _ in range(5)]
    print(f"bucket {sec:2d}s: encoder steady {np.median(ts):5.1f} ms")
