"""Benchmark ONE model on a list of eval sets, in its own process (so peak RAM is the model's own).

    python -m training.eval.bench_one --tag moonshine-small --model "moonshine:...?arch=small" \
        --sets dev-ls-clean,dev-voxpopuli --every 4 --threads 4

Writes work/results/bench/<tag>/<set>.jsonl (one line per utterance: id, ref, hyp, seconds, ms) and
work/results/bench/<tag>/summary.json. Re-running resumes: utterances already in the jsonl are skipped.
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import time

import psutil

from training.common import RESULTS, append_jsonl, read_jsonl, write_json
from training.eval import backends, sets, wer


def rss_mb() -> tuple[float, float]:
    mi = psutil.Process().memory_info()
    peak = getattr(mi, "peak_wset", None) or getattr(mi, "peak_rss", None) or mi.rss
    return mi.rss / 2**20, peak / 2**20


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--tag", required=True)
    p.add_argument("--model", required=True)
    p.add_argument("--sets", required=True)
    p.add_argument("--every", type=int, default=1)
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--threads", type=int, default=4)
    a = p.parse_args()

    out = RESULTS / "bench" / a.tag
    out.mkdir(parents=True, exist_ok=True)
    # touch the data path first so library/import memory is in the baseline, not charged to the model
    plan = sets.expand(a.sets)
    first = plan[0][0]
    next(iter(sets.utterances(first, limit=1)))
    base_rss, _ = rss_mb()
    t0 = time.perf_counter()
    rec = backends.make(a.model, a.threads)
    load_s = time.perf_counter() - t0
    load_rss, _ = rss_mb()

    summary = {"tag": a.tag, "model": a.model, "threads": a.threads, "every": a.every,
               "model_mb": backends.model_bytes(a.model) / 1e6, "load_s": load_s,
               "rss_base_mb": base_rss, "rss_loaded_mb": load_rss,
               "cpu": platform.processor(), "machine": platform.machine(), "sets": {}}
    # warm-up so first-call graph optimisation isn't counted as decode time
    rec(next(iter(sets.utterances(first, limit=1))).audio)

    for name, every in plan:
        every = every * a.every
        path = out / f"{name}.jsonl"
        done = {r["id"]: r for r in read_jsonl(path)} if path.exists() else {}
        for u in sets.utterances(name, limit=a.limit or None, every=every):
            if u.id in done:
                continue
            t = time.perf_counter()
            hyp = rec(u.audio)
            ms = (time.perf_counter() - t) * 1000
            row = {"id": u.id, "ref": u.text, "hyp": hyp, "sec": round(u.duration, 3), "ms": round(ms, 2)}
            append_jsonl(path, row)
            done[u.id] = row
        rows = list(done.values())
        s = wer.score([r["id"] for r in rows], [r["ref"] for r in rows], [r["hyp"] for r in rows])
        lo, hi = wer.bootstrap_ci(s, b=1000)
        audio = sum(r["sec"] for r in rows)
        proc = sum(r["ms"] for r in rows) / 1000
        lat = sorted(r["ms"] for r in rows)
        summary["sets"][name] = {
            "every": every, "n": len(rows), "hours": audio / 3600, "wer": s.wer, "ci": [lo, hi],
            "rtf": proc / audio if audio else 0.0,
            "ms_p50": lat[len(lat) // 2] if lat else 0, "ms_p95": lat[int(len(lat) * 0.95)] if lat else 0,
        }
        print(f"{a.tag:28s} {name:16s} n={len(rows):5d} WER={s.wer * 100:6.2f}% "
              f"[{lo * 100:.2f}-{hi * 100:.2f}] RTF={summary['sets'][name]['rtf']:.4f}", flush=True)

    _, peak = rss_mb()
    summary["rss_peak_mb"] = peak
    summary["rss_model_peak_mb"] = peak - base_rss
    write_json(out / "summary.json", summary)
    print(json.dumps({k: v for k, v in summary.items() if k != "sets"}, indent=1), flush=True)


if __name__ == "__main__":
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    main()
