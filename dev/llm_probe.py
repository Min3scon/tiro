"""Speed and sanity of the tier-2 language model on GPU and CPU."""
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tiro import gpu  # noqa: E402
from tiro.correct.llm import LanguageScorer  # noqa: E402

gpu.prepare_cuda_runtime()
CASES = [
    ("I've been fighting with", "rust", ["Rust"], "all weekend and I'm",
     ["I'm rewriting the log parser in Rust because Python is too slow.", "The Rust borrow checker hates me."]),
    ("I've been fighting with", "rust", ["Rust"], "all weekend and I'm",
     ["There's rust on the wheel arches of the van.", "I sanded the rust off the gate."]),
    ("Can you send me the", "Swift", ["SWIFT"], "code when you get",
     ["Our bank charges a flat fee on every outgoing SWIFT transfer.", "The supplier needs our IBAN."]),
    ("I played", "George asser", ["GeoGuessr"], "last night and nailed",
     ["Just played a round of GeoGuessr and got the map wrong."]),
    ("I played", "George asser", ["GeoGuessr"], "last night and nailed", []),
    ("We're paying way too much for", "cloud", ["Claude"], "at the moment.", []),
    ("The weather is great, so let's decide", "whether", ["weather"], "to eat outside.", []),
]
for folder, dev in (("qwen2.5-1.5b-instruct", "cuda"), ("qwen2.5-0.5b-instruct", "cuda"), ("qwen2.5-0.5b-instruct", "cpu")):
    lm = LanguageScorer(ROOT / "models" / folder, device=dev)
    t0 = time.perf_counter()
    lm.load()
    print(f"\n== {folder} on {dev}: loaded in {time.perf_counter() - t0:.1f}s")
    for left, orig, opts, right, ex in CASES:
        times = []
        for _ in range(3):
            lm._memo.clear()
            t = time.perf_counter()
            g = lm.compare(left, orig, opts, right, timeout=5.0, examples=ex)
            times.append((time.perf_counter() - t) * 1000)
        print(f"   {orig!r:16s} -> {opts[0]!r:12s} gain {g[0]:+6.2f}  ({min(times):.0f} ms)  ex={len(ex)}")
