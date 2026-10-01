"""Investigate CUDA encoder latency: steady state vs. new input shapes, and provider options."""

import sys
import time
from pathlib import Path

import numpy as np
import onnxruntime as ort

ort.set_default_logger_severity(3)
ort.preload_dlls(directory="")

ROOT = Path(__file__).resolve().parents[1]
ENC = ROOT / "models" / "parakeet-tdt-0.6b-v2" / "encoder-model.onnx"


def make(opts, verbose=False):
    so = ort.SessionOptions()
    so.log_severity_level = 0 if verbose else 3
    so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
    t = time.perf_counter()
    s = ort.InferenceSession(str(ENC), sess_options=so, providers=[("CUDAExecutionProvider", opts), "CPUExecutionProvider"])
    print(f"  session created in {time.perf_counter() - t:.1f}s", flush=True)
    return s


def run(s, sec):
    T = int(sec * 100)
    feats = np.random.randn(1, 128, T).astype(np.float32)
    lens = np.array([T], dtype=np.int64)
    t = time.perf_counter()
    s.run(["outputs", "encoded_lengths"], {"audio_signal": feats, "length": lens})
    return (time.perf_counter() - t) * 1000


def probe(label, opts):
    print(label, opts, flush=True)
    s = make(opts)
    first = [run(s, sec) for sec in (3.0, 6.0, 9.0, 12.0)]
    steady = [run(s, 6.0) for _ in range(5)]
    again = [run(s, sec) for sec in (3.0, 9.0, 12.0)]
    newshapes = [run(s, sec) for sec in (3.3, 6.7, 9.1, 12.9, 4.4)]
    print(f"  first-time shapes 3/6/9/12s: {[round(x) for x in first]} ms")
    print(f"  steady 6s x5: {[round(x) for x in steady]} ms")
    print(f"  repeat shapes 3/9/12s: {[round(x) for x in again]} ms")
    print(f"  more new shapes: {[round(x) for x in newshapes]} ms", flush=True)
    del s


if __name__ == "__main__":
    which = sys.argv[1:] or ["heur", "exh", "default"]
    if "heur" in which:
        probe("HEURISTIC", {"cudnn_conv_algo_search": "HEURISTIC"})
    if "exh" in which:
        probe("EXHAUSTIVE", {"cudnn_conv_algo_search": "EXHAUSTIVE"})
    if "default" in which:
        probe("DEFAULT", {"cudnn_conv_algo_search": "DEFAULT"})
    if "maxws" in which:
        probe("HEURISTIC+maxws", {"cudnn_conv_algo_search": "HEURISTIC", "cudnn_conv_use_max_workspace": "1"})
    if "verbose" in which:
        so = ort.SessionOptions()
        so.log_severity_level = 1
        ort.set_default_logger_severity(1)
        s = ort.InferenceSession(str(ENC), sess_options=so, providers=["CUDAExecutionProvider", "CPUExecutionProvider"])
