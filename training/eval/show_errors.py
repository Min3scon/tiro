"""Show the worst utterances of a benchmark result (normalised ref vs hyp)."""
import sys

from training.common import RESULTS, read_jsonl
from training.eval import normalize, wer

tag, name = sys.argv[1], sys.argv[2]
n = int(sys.argv[3]) if len(sys.argv) > 3 else 8
rows = list(read_jsonl(RESULTS / "bench" / tag / f"{name}.jsonl"))
scored = []
for r in rows:
    ref, hyp = normalize.leaderboard(r["ref"]), normalize.leaderboard(r["hyp"])
    e, w = wer.edit_counts(ref, hyp)
    scored.append((e, w, ref, hyp, r))
scored.sort(key=lambda x: -x[0])
for e, w, ref, hyp, r in scored[:n]:
    print(f"--- {r['id']}  errors={e}/{w}  {r['sec']}s")
    print("REF:", ref)
    print("HYP:", hyp)
    print("RAW:", r["hyp"][:300])
