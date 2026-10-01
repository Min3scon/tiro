"""Score dev/eval_final_reuse.py's differences against the reference texts: is reusing the pause decode worse?

    python dev/score_final_reuse.py [work/results/latency/final_reuse.json]
"""
from __future__ import annotations

import json
import random
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ACC = ROOT / "tests" / "accuracy"


def words(s: str) -> list[str]:
    return [w for w in (re.sub(r"[^\w']+", "", t.lower()) for t in s.split()) if w]


def edits(ref: list[str], hyp: list[str]) -> int:
    d = list(range(len(hyp) + 1))
    for i, r in enumerate(ref, 1):
        prev, d[0] = d[0], i
        for j, h in enumerate(hyp, 1):
            prev, d[j] = d[j], min(d[j] + 1, d[j - 1] + 1, prev + (r != h))
    return d[-1]


def references() -> dict[str, str]:
    refs = {}
    for name in ("no_harm.json", "phrases.json"):
        data = json.loads((ACC / name).read_text(encoding="utf-8"))
        items = data if isinstance(data, list) else data.get("items", data.get("cases", []))
        for it in items:
            refs[it["id"]] = it.get("expect") or it.get("text") or it.get("say")
    return refs


def main() -> None:
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "work" / "results" / "latency" / "final_reuse.json"
    report = json.loads(path.read_text(encoding="utf-8"))
    refs = references()
    for tail, r in report["tails"].items():
        diffs = []
        for d in r["diffs"]:
            cid = d["clip"].rsplit("_", 1)[0]
            ref = words(refs[cid])
            diffs.append((edits(ref, words(d["reuse"])), edits(ref, words(d["final"]))))
        er, ef = sum(a for a, _ in diffs), sum(b for _, b in diffs)
        # paired bootstrap over all eligible clips (identical ones contribute 0 difference)
        n = r["eligible"]
        delta = [a - b for a, b in diffs] + [0] * (n - len(diffs))
        rng = random.Random(0)
        boots = sorted(sum(rng.choice(delta) for _ in range(n)) for _ in range(2000))
        lo, hi = boots[50], boots[1949]
        better = sum(a < b for a, b in diffs)
        worse = sum(a > b for a, b in diffs)
        print(f"tail {tail}s: {n} eligible, {len(diffs)} differ: reuse better on {better}, worse on {worse}, "
              f"same errors on {len(diffs) - better - worse}; word errors reuse {er} vs fresh decode {ef} "
              f"(difference {er - ef:+d}, 95% CI {lo:+d} to {hi:+d})")


if __name__ == "__main__":
    main()
