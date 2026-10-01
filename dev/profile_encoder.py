"""Profile which encoder ops dominate after an input-shape change on CUDA."""

import collections
import json
import sys
import time
from pathlib import Path

import numpy as np
import onnxruntime as ort

ort.set_default_logger_severity(3)
ort.preload_dlls(directory="")

ROOT = Path(__file__).resolve().parents[1]
ENC = ROOT / "models" / "parakeet-tdt-0.6b-v2" / "encoder-model.onnx"


def feats(sec):
    T = int(sec * 100)
    return {"audio_signal": np.random.randn(1, 128, T).astype(np.float32), "length": np.array([T], dtype=np.int64)}


def timed(s, sec):
    t = time.perf_counter()
    s.run(["outputs", "encoded_lengths"], feats(sec))
    return round((time.perf_counter() - t) * 1000)


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "profile"
    so = ort.SessionOptions()
    so.log_severity_level = 3
    opts = {"cudnn_conv_algo_search": "HEURISTIC"}
    if mode == "nomempattern":
        so.enable_mem_pattern = False
    if mode == "basic":
        so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_BASIC
    if mode == "pad1d":
        opts["cudnn_conv1d_pad_to_nc1d"] = "1"
    if mode == "profile":
        so.enable_profiling = True
        so.profile_file_prefix = str(ROOT / "dev" / "enc_profile")
    s = ort.InferenceSession(str(ENC), sess_options=so, providers=[("CUDAExecutionProvider", opts), "CPUExecutionProvider"])
    seq = [6.0, 6.0, 6.0, 7.0, 7.0, 8.0]
    print(mode, [timed(s, x) for x in seq], flush=True)
    if mode == "profile":
        path = s.end_profiling()
        events = json.loads(Path(path).read_text())
        # group node events by run index: find 'model_run' events to split
        runs = [e for e in events if e.get("name") == "model_run"]
        print("runs:", [round(r["dur"] / 1000) for r in runs])
        # Take the 4th run (shape change 6->7) and summarize op types by total duration
        target = runs[3]
        t0, t1 = target["ts"], target["ts"] + target["dur"]
        agg = collections.Counter()
        cnt = collections.Counter()
        for e in events:
            if e.get("cat") == "Node" and t0 <= e["ts"] <= t1 and e["name"].endswith("_kernel_time"):
                op = e.get("args", {}).get("op_name", "?")
                prov = e.get("args", {}).get("provider", "?")
                agg[(op, prov)] += e["dur"]
                cnt[(op, prov)] += 1
        print("top ops for shape-change run (us):")
        for (op, prov), d in agg.most_common(12):
            print(f"  {op:28s} {prov:24s} {d:9d} us  n={cnt[(op, prov)]}")
        steady = runs[2]
        t0, t1 = steady["ts"], steady["ts"] + steady["dur"]
        agg2 = collections.Counter()
        for e in events:
            if e.get("cat") == "Node" and t0 <= e["ts"] <= t1 and e["name"].endswith("_kernel_time"):
                agg2[(e.get("args", {}).get("op_name", "?"), e.get("args", {}).get("provider", "?"))] += e["dur"]
        print("top ops for steady run (us):")
        for (op, prov), d in agg2.most_common(8):
            print(f"  {op:28s} {prov:24s} {d:9d} us")
        Path(path).unlink(missing_ok=True)
