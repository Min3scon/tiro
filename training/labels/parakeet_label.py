"""First-pass labels: transcribe every training shard with the Standard model (Parakeet TDT 0.6B v2, CUDA).

Runs in the APP's venv (it uses Tiro's own engine, so the labels have exactly Standard's style):
    .venv\\Scripts\\python.exe -m training.labels.parakeet_label [--sources ami-ihm,librispeech] [--batch-sec 240]

Writes work/labels/parakeet-v2/<source>/<shard>.jsonl: {"id", "hyp", "lp" (mean token log-prob), "lp_min"}.
Resumable: finished shards are skipped; a half-written shard is redone. Pauses while free GPU memory is low.
"""
from __future__ import annotations

import argparse
import io
import json
import os
import sys
import time
from pathlib import Path

import numpy as np
import pyarrow.parquet as pq
import soundfile as sf

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from training.common import LABELS, LOGS, TRAIN, log  # noqa: E402

NAME = "parakeet-v2"


def batches(rows: list[dict], batch_sec: float):
    rows = sorted(rows, key=lambda r: r["duration"])
    cur, total = [], 0.0
    for r in rows:
        # padded cost: the batch is as long as its longest member
        if cur and (len(cur) + 1) * r["duration"] > batch_sec:
            yield cur
            cur = []
        cur.append(r)
    if cur:
        yield cur


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--sources", default="")
    p.add_argument("--batch-sec", type=float, default=90.0)
    p.add_argument("--gpu-mem-gb", type=float, default=2.5)
    p.add_argument("--device", default="cuda")
    p.add_argument("--follow", action="store_true", help="keep picking up new shards while data prep runs")
    a = p.parse_args()

    from tiro.asr import tokens_to_words

    asr = build_asr(REPO / "models" / "parakeet-tdt-0.6b-v2", a.gpu_mem_gb)
    status = LOGS / "label_parakeet.status"
    done_h = 0.0
    t_start = time.time()
    while True:
        new = label_pass(a, asr, tokens_to_words, status, t_start, done_h)
        done_h += new
        if not a.follow or (new == 0 and not producer_alive()):
            break
        if new == 0:
            time.sleep(300)  # wait for the data-preparation job to convert more shards
    log("label_parakeet", "all done")


def build_asr(model_dir: Path, gpu_mem_gb: float):
    """Tiro's own Parakeet decoder (same decisions as the app), with a hard cap on GPU memory so the copy of
    Tiro that's in use on this PC keeps its share of the card."""
    import onnxruntime as ort
    from onnx_asr.loader import Manager
    from onnx_asr.resolver import Resolver

    from tiro import gpu
    from tiro.asr import _fast_tdt_class

    gpu.prepare_cuda_runtime()
    so = ort.SessionOptions()
    so.log_severity_level = 3
    so.add_session_config_entry("session.intra_op.allow_spinning", "0")
    so.inter_op_num_threads = 1
    so.intra_op_num_threads = 2
    providers = [("CUDAExecutionProvider", {
        "device_id": 0, "cudnn_conv_algo_search": "HEURISTIC", "arena_extend_strategy": "kSameAsRequested",
        "gpu_mem_limit": str(int(gpu_mem_gb * 2**30)), "cudnn_conv_use_max_workspace": "0"}), "CPUExecutionProvider"]
    if gpu_mem_gb <= 0:  # CPU only (checks and tests while the GPU is busy)
        providers = ["CPUExecutionProvider"]
    manager = Manager(so, providers)
    cls = _fast_tdt_class()
    files = Resolver(cls, None, model_dir, offline=True).resolve_model(quantization=None)
    asr = cls(files, manager._create_preprocessor, manager.default_onnx_config)
    asr.bucket_frames = ()
    return asr


def batched_greedy(asr, waves: np.ndarray, lens: np.ndarray):
    """Greedy TDT decoding of a whole batch at once: one decoder/joint call per step for every utterance that
    is still decoding, instead of one call per utterance per step. Same decisions as onnx-asr's loop.
    Returns [(token strings, mean log-prob, min log-prob)] in batch order."""
    feats, feat_lens = asr._preprocessor(waves, lens)
    enc, enc_lens = asr._encode(feats, feat_lens)  # [B, T, D]
    dj = asr._decoder_joint
    b_count = enc.shape[0]
    vocab, blank, max_sym = asr._vocab_size, asr._blank_idx, asr._max_tokens_per_step
    s1, s2 = asr._create_state()
    s1 = np.repeat(s1, b_count, axis=1)
    s2 = np.repeat(s2, b_count, axis=1)
    t = np.zeros(b_count, dtype=np.int64)
    emitted = np.zeros(b_count, dtype=np.int64)
    prev = np.full(b_count, blank, dtype=np.int32)
    toks = [[] for _ in range(b_count)]
    lps = [[] for _ in range(b_count)]
    enc_lens = enc_lens.astype(np.int64)
    active = np.nonzero(t < enc_lens)[0]
    while len(active):
        n = len(active)
        frames = enc[active, t[active]][:, :, None].astype(np.float32)
        out, _, n1, n2 = dj.run(None, {"encoder_outputs": frames, "targets": prev[active][:, None],
                                       "target_length": np.ones(n, dtype=np.int32),
                                       "input_states_1": s1[:, active], "input_states_2": s2[:, active]})
        out = out.reshape(n, -1)
        logits = out[:, :vocab]
        steps = out[:, vocab:].argmax(axis=1)
        tok = logits.argmax(axis=1)
        top = logits.max(axis=1)
        lse = top + np.log(np.exp(logits - top[:, None]).sum(axis=1))
        for j, b in enumerate(active):
            k = int(tok[j])
            if k != blank:
                toks[b].append(k)
                lps[b].append(float(logits[j, k] - lse[j]))
                s1[:, b] = n1[:, j]
                s2[:, b] = n2[:, j]
                prev[b] = k
                emitted[b] += 1
            step = int(steps[j])
            if step > 0:
                t[b] += step
                emitted[b] = 0
            elif k == blank or emitted[b] == max_sym:
                t[b] += 1
                emitted[b] = 0
        active = np.nonzero(t < enc_lens)[0]
    return [([asr._vocab[i] for i in toks[b]], lps[b]) for b in range(b_count)]


def producer_alive() -> bool:
    import psutil

    pid_file = LOGS / "prepare_all.pid"
    try:
        return psutil.pid_exists(int(pid_file.read_text().strip()))
    except Exception:
        return False


def label_pass(a, asr, tokens_to_words, status, t_start, done_h) -> float:
    added = 0.0
    sources = [s for s in a.sources.split(",") if s] or sorted(d.name for d in TRAIN.iterdir() if d.is_dir())
    for src in sources:
        shards = sorted((TRAIN / src).glob("*.parquet"))
        out_dir = LABELS / NAME / src
        out_dir.mkdir(parents=True, exist_ok=True)
        for si, shard in enumerate(shards, 1):
            out = out_dir / (shard.stem + ".jsonl")
            if out.exists():
                continue
            table = pq.read_table(shard, columns=["id", "audio", "duration"])
            rows = table.to_pylist()
            tmp = out.with_suffix(".tmp")
            t0 = time.time()
            with open(tmp, "w", encoding="utf-8") as f:
                for batch in batches(rows, a.batch_sec):
                    wavs = [sf.read(io.BytesIO(r["audio"]), dtype="float32")[0] for r in batch]
                    n = max(len(w) for w in wavs)
                    arr = np.zeros((len(wavs), n), dtype=np.float32)
                    for i, w in enumerate(wavs):
                        arr[i, : len(w)] = w
                    lens = np.array([len(w) for w in wavs], dtype=np.int64)
                    for r, (tokens, lps) in zip(batch, batched_greedy(asr, arr, lens)):
                        words = tokens_to_words(tokens, [0.0] * len(tokens), lps)
                        f.write(json.dumps({
                            "id": r["id"], "hyp": " ".join(w.text for w in words).strip(),
                            "lp": float(np.mean(lps)) if lps else 0.0, "lp_min": float(min(lps)) if lps else 0.0,
                        }, ensure_ascii=False) + "\n")
            os.replace(tmp, out)
            h = sum(r["duration"] for r in rows) / 3600
            added += h
            total = done_h + added
            rate = total / max((time.time() - t_start) / 3600, 1e-6)
            log("label_parakeet", f"{src} [{si}/{len(shards)}] {shard.name}: {len(rows)} utts {h:.1f} h in "
                f"{time.time() - t0:.0f}s (RTFx {h * 3600 / max(time.time() - t0, 1e-3):.0f})")
            status.write_text(f"{total:.0f} h labelled this run at {rate:.0f} h/hour; now {src} {si}/{len(shards)}",
                              encoding="utf-8")
    return added


if __name__ == "__main__":
    main()
