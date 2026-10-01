"""Quick accuracy/latency benchmark of the Parakeet ONNX model on LibriSpeech clips.

Usage:  python dev/bench_asr.py [cuda] [cuda-exh] [cpu-int8] [cpu-fp32]
"""

import json
import re
import sys
import time
from pathlib import Path

import jiwer
import numpy as np
import onnxruntime as ort
import soundfile as sf

ort.preload_dlls(directory="")
import onnx_asr  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
MODEL_DIR = ROOT / "models" / "parakeet-tdt-0.6b-v2"
DATA = ROOT / "tests" / "data" / "dummy"
refs = json.loads((DATA / "refs.json").read_text())


def norm(s: str) -> str:
    s = s.lower()
    s = re.sub(r"[^a-z0-9' ]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def run(label, providers, quant=None, limit=None):
    t0 = time.perf_counter()
    so = ort.SessionOptions()
    so.log_severity_level = 3
    model = onnx_asr.load_model(
        "nemo-parakeet-tdt-0.6b-v2", MODEL_DIR, quantization=quant, providers=providers, sess_options=so
    )
    t_load = time.perf_counter() - t0
    wav0, _ = sf.read(DATA / refs[0]["file"], dtype="float32")
    t0 = time.perf_counter()
    model.recognize(wav0)
    t_warm = time.perf_counter() - t0
    hyps, gts, times, durs = [], [], [], []
    items = refs[:limit] if limit else refs
    for r in items:
        wav, sr = sf.read(DATA / r["file"], dtype="float32")
        t0 = time.perf_counter()
        text = model.recognize(wav)
        times.append(time.perf_counter() - t0)
        durs.append(len(wav) / sr)
        hyps.append(norm(text))
        gts.append(norm(r["text"]))
    wer = jiwer.wer(gts, hyps)
    tt = np.array(times)
    print(
        f"{label:28s} load={t_load:5.1f}s warm={t_warm:5.2f}s WER={wer * 100:5.2f}%  "
        f"mean={tt.mean() * 1000:6.1f}ms p90={np.percentile(tt, 90) * 1000:6.1f}ms  "
        f"RTFx={sum(durs) / tt.sum():6.1f}  n={len(items)} avg_len={np.mean(durs):.1f}s",
        flush=True,
    )
    return model


if __name__ == "__main__":
    which = sys.argv[1:] or ["cuda", "cpu-int8", "cpu-fp32"]
    cuda_opts = {"device_id": 0, "cudnn_conv_algo_search": "HEURISTIC", "arena_extend_strategy": "kSameAsRequested"}
    if "cuda" in which:
        m = run("cuda fp32 (heuristic)", [("CUDAExecutionProvider", cuda_opts), "CPUExecutionProvider"])
        for r in refs[:3]:
            wav, _ = sf.read(DATA / r["file"], dtype="float32")
            print("  HYP:", m.recognize(wav))
            print("  REF:", r["text"])
        del m
    if "cuda-exh" in which:
        run("cuda fp32 (exhaustive)", [("CUDAExecutionProvider", {"device_id": 0}), "CPUExecutionProvider"])
    if "cpu-int8" in which:
        run("cpu int8", ["CPUExecutionProvider"], quant="int8", limit=30)
    if "cpu-fp32" in which:
        run("cpu fp32", ["CPUExecutionProvider"], limit=30)
