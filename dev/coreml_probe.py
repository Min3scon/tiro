"""On an Apple Silicon Mac: which ONNX Runtime Core ML settings run the Parakeet encoder, and how fast?

Tries a few provider configurations on the fp32 and int8 encoders and times a 6 s and a 12 s input, so the
app can pick the fastest working one (or stay on the CPU).
"""
import json
import sys
import time
import traceback
from pathlib import Path

import numpy as np
import onnxruntime as ort

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tiro.paths import data_dir  # noqa: E402

model_dir = data_dir() / "models" / "parakeet-tdt-0.6b-v2"
print("providers:", ort.get_available_providers())
configs = {
    "cpu": [],
    "mlprogram_all": [("CoreMLExecutionProvider", {"ModelFormat": "MLProgram", "MLComputeUnits": "ALL"})],
    "mlprogram_gpu": [("CoreMLExecutionProvider", {"ModelFormat": "MLProgram", "MLComputeUnits": "CPUAndGPU"})],
    "mlprogram_static": [("CoreMLExecutionProvider", {"ModelFormat": "MLProgram", "MLComputeUnits": "ALL",
                                                      "RequireStaticInputShapes": "1"})],
    "neuralnetwork": [("CoreMLExecutionProvider", {"ModelFormat": "NeuralNetwork", "MLComputeUnits": "ALL"})],
}
results = {}
for enc in ("encoder-model.onnx", "encoder-model.int8.onnx"):
    path = model_dir / enc
    if not path.exists():
        continue
    for name, prov in configs.items():
        key = f"{enc}:{name}"
        try:
            so = ort.SessionOptions()
            so.log_severity_level = 3
            t0 = time.perf_counter()
            sess = ort.InferenceSession(str(path), sess_options=so, providers=prov + ["CPUExecutionProvider"])
            load = time.perf_counter() - t0
            times = {}
            for frames in (600, 1200, 600):
                feats = np.random.randn(1, 128, frames).astype(np.float32)
                length = np.array([frames], dtype=np.int64)
                t = time.perf_counter()
                sess.run(None, {"audio_signal": feats, "length": length})
                times.setdefault(frames, []).append(round((time.perf_counter() - t) * 1000))
            results[key] = {"ok": True, "load_s": round(load, 1), "ms": times}
        except Exception as exc:
            results[key] = {"ok": False, "error": f"{type(exc).__name__}: {str(exc)[:300]}"}
            traceback.print_exc(limit=1)
        print(key, json.dumps(results[key]), flush=True)
print(json.dumps(results, indent=1))
