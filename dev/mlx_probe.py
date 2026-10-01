"""On a Mac: download the small MLX model, load the tier-2 scorer on the Apple GPU and time a few checks."""
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tiro.correct.llm_mlx import MlxLanguageScorer  # noqa: E402
from tiro.models import MLX_LANGUAGE_MODELS, download_model  # noqa: E402

key = sys.argv[1] if len(sys.argv) > 1 else "tiny"
folder = download_model(MLX_LANGUAGE_MODELS[key])
lm = MlxLanguageScorer(folder)
t0 = time.perf_counter()
lm.load()
print(f"loaded {folder.name} in {time.perf_counter() - t0:.1f}s")
cases = [("I've been fighting with", "rust", ["Rust"], "all weekend and",
          ["I'm rewriting the parser in Rust because Python is too slow."]),
         ("We're paying way too much for", "cloud", ["Claude"], "at the moment.", []),
         ("I played", "George asser", ["GeoGuessr"], "last night", [])]
for left, orig, opts, right, ex in cases:
    lm._memo.clear()
    t = time.perf_counter()
    gains = lm.compare(left, orig, opts, right, timeout=10.0, examples=ex)
    print(f"{orig!r:16s} -> {opts[0]!r:12s} gain {gains[0]:+.2f}  ({(time.perf_counter() - t) * 1000:.0f} ms)")
