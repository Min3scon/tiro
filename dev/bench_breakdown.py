"""Break down Parakeet latency into preprocess / encode / decode for different provider layouts."""

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
from onnx_asr.models.nemo import NemoConformerTdt  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
MODEL_DIR = ROOT / "models" / "parakeet-tdt-0.6b-v2"
DATA = ROOT / "tests" / "data" / "dummy"
refs = json.loads((DATA / "refs.json").read_text())
wavs = [sf.read(DATA / r["file"], dtype="float32")[0] for r in refs]


def norm(s: str) -> str:
    s = s.lower()
    s = re.sub(r"[^a-z0-9' ]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


CUDA = ("CUDAExecutionProvider", {"device_id": 0, "cudnn_conv_algo_search": "HEURISTIC"})


def build(enc_providers, dec_providers, quant=None, numpy_pre=None):
    so = ort.SessionOptions()
    so.log_severity_level = 3
    pre_cfg = {} if numpy_pre is None else {"use_numpy_preprocessors": numpy_pre}
    model = onnx_asr.load_model(
        "nemo-parakeet-tdt-0.6b-v2", MODEL_DIR, quantization=quant, providers=enc_providers, sess_options=so,
        preprocessor_config=pre_cfg or None,
    )
    asr = model.asr
    assert isinstance(asr, NemoConformerTdt)
    if dec_providers != enc_providers:
        dso = ort.SessionOptions()
        dso.intra_op_num_threads = 1
        dso.inter_op_num_threads = 1
        suffix = f".{quant}" if quant else ""
        asr._decoder_joint = ort.InferenceSession(
            str(MODEL_DIR / f"decoder_joint-model{suffix}.onnx"), sess_options=dso, providers=dec_providers
        )
    print("  encoder providers:", asr._encoder.get_providers()[:1],
          "decoder providers:", asr._decoder_joint.get_providers()[:1],
          "preprocessor:", type(asr._preprocessor).__name__)
    return model


def bench(label, model, n=None):
    asr = model.asr
    items = list(range(len(wavs)))[: n or len(wavs)]
    model.recognize(wavs[0])  # warm
    tp = te = td = 0.0
    hyps, gts, total_audio = [], [], 0.0
    for i in items:
        w = wavs[i]
        total_audio += len(w) / 16000
        wf, wl = w[None, :], np.array([len(w)], dtype=np.int64)
        t0 = time.perf_counter()
        feats, flens = asr._preprocessor(wf, wl)
        t1 = time.perf_counter()
        enc, enc_lens = asr._encode(feats, flens)
        t2 = time.perf_counter()
        res = list(map(asr._decode_tokens, *zip(*asr._decoding(enc, enc_lens), strict=False)))
        t3 = time.perf_counter()
        tp += t1 - t0
        te += t2 - t1
        td += t3 - t2
        hyps.append(norm(res[0].text))
        gts.append(norm(refs[i]["text"]))
    k = len(items)
    tot = tp + te + td
    print(f"{label:34s} WER={jiwer.wer(gts, hyps) * 100:5.2f}%  per-clip: pre={tp / k * 1000:5.1f} "
          f"enc={te / k * 1000:6.1f} dec={td / k * 1000:6.1f} total={tot / k * 1000:6.1f}ms  RTFx={total_audio / tot:6.1f}",
          flush=True)


if __name__ == "__main__":
    import sys

    modes = sys.argv[1:] or ["cuda-all", "cuda-enc-cpu-dec", "cpu-int8", "cpu-fp32"]
    if "cuda-all" in modes:
        m = build([CUDA, "CPUExecutionProvider"], [CUDA, "CPUExecutionProvider"])
        bench("cuda all", m)
        del m
    if "cuda-enc-cpu-dec" in modes:
        m = build([CUDA, "CPUExecutionProvider"], ["CPUExecutionProvider"])
        bench("cuda enc + cpu dec", m)
        del m
    if "cuda-enc-cpu-dec-numpypre" in modes:
        m = build([CUDA, "CPUExecutionProvider"], ["CPUExecutionProvider"], numpy_pre=True)
        bench("cuda enc + cpu dec + numpy pre", m)
        del m
    if "cpu-int8" in modes:
        m = build(["CPUExecutionProvider"], ["CPUExecutionProvider"], quant="int8")
        bench("cpu int8", m)
        del m
    if "cpu-fp32" in modes:
        m = build(["CPUExecutionProvider"], ["CPUExecutionProvider"])
        bench("cpu fp32", m)
        del m
