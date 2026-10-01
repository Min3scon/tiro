"""How long do the correction model's first calls take (new input shapes) compared with warm ones?

    python dev/llm_coldstart.py [--device cuda]
"""
from __future__ import annotations

import argparse
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tiro.correct.llm import LanguageScorer  # noqa: E402
from tiro.paths import user_models_dir  # noqa: E402

CASES = [
    ("I wrote it in", "Python", ["Pythons"], "last night"),
    ("Can you send the report to", "Shaun", ["Sean", "Shawn"], "before lunch"),
    ("We played", "George asser", ["GeoGuessr"], "all evening and it was"),
    ("The meeting with the new team from the marketing department about the launch of the product on",
     "Tuesday", ["Thursday", "Tuesdays", "to stay"], "was moved"),
    ("Please book a table for", "for", ["four", "fore", "far"], "people at the"),
    ("He said that the" + " very" * 20 + " long sentence", "end", ["and"], ""),
]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--device", default="cuda")
    ap.add_argument("--model", default="")
    a = ap.parse_args()
    folders = [p for p in (user_models_dir()).glob("qwen2.5-*instruct*") if p.is_dir()]
    folders += [p for p in (ROOT / "models").glob("qwen2.5-*instruct*") if p.is_dir()]
    if a.model:
        folders = [Path(a.model)]
    if not folders:
        sys.exit("no Qwen model folder found")
    folder = sorted(folders)[-1]
    lm = LanguageScorer(folder, device=a.device, gpu_lock=threading.Lock() if a.device == "cuda" else None)
    t = time.perf_counter()
    lm.load()
    print(f"{folder.name} on {a.device}: load {time.perf_counter() - t:.1f}s (includes one warm-up call)")
    for rnd in (1, 2):
        lm._memo.clear()
        for left, orig, opts, right in CASES:
            t = time.perf_counter()
            lm.compare(left, orig, opts, right, timeout=30.0, examples=["I love GeoGuessr", "Shaun is here"])
            print(f"  round {rnd}: {len(left.split()):3d} words left, {len(opts)} options: "
                  f"{(time.perf_counter() - t) * 1000:7.1f} ms")


if __name__ == "__main__":
    main()
