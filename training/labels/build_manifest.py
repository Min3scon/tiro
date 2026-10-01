"""Turn teacher labels into a training manifest: filter by agreement with the human transcript, check leakage.

    python -m training.labels.build_manifest --labeller parakeet-v2 --name round1 [--max-wer 0.1]

Rules (distil-whisper style):
  * keep an utterance when the teacher's transcript and the corpus's human transcript differ by at most
    --max-wer (per utterance, after the leaderboard normaliser); the teacher's text (punctuated, cased) is the label;
  * drop empty labels, implausible speaking rates (characters per second outside 2..30) and repetition loops;
  * drop anything whose normalised text equals a test or dev sentence (leakage guard), and report how many.

Writes work/data/manifests/<name>.jsonl (one line per kept utterance: shard, id, text, duration, source) and
<name>.report.json with counts per source and per rule.
"""
from __future__ import annotations

import argparse
import re
from collections import Counter, defaultdict

import pyarrow.parquet as pq

from training.common import DATA, LABELS, TRAIN, log, read_jsonl, write_json, write_jsonl
from training.eval import normalize, sets, wer

MANIFESTS = DATA / "manifests"
LOOP = re.compile(r"\b(\w+(?:\s+\w+){0,3})(?:\s+\1){3,}\b", re.IGNORECASE)


def eval_sentences() -> set[str]:
    """Normalised text of every test and dev utterance (only the text column is read)."""
    out = set()
    for name, s in sets.SETS.items():
        try:
            files = s.files()
        except FileNotFoundError:
            continue
        for f in files:
            pf = pq.ParquetFile(f)
            if s.text_col not in pf.schema_arrow.names:
                continue
            for t in pf.read(columns=[s.text_col]).column(0).to_pylist():
                n = normalize.leaderboard(t or "")
                if len(n.split()) >= 4:  # very short sentences ("thank you") collide by chance, not by leakage
                    out.add(n)
    return out


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--labeller", default="parakeet-v2")
    p.add_argument("--name", required=True)
    p.add_argument("--max-wer", type=float, default=0.10)
    p.add_argument("--sources", default="")
    a = p.parse_args()

    log("manifest", "collecting test/dev sentences for the leakage check")
    held_out = eval_sentences()
    log("manifest", f"{len(held_out)} distinct held-out sentences")
    root = LABELS / a.labeller
    sources = [s for s in a.sources.split(",") if s] or sorted(d.name for d in root.iterdir() if d.is_dir())
    rows, stats = [], defaultdict(Counter)
    hours = Counter()
    for src in sources:
        for lab in sorted((root / src).glob("*.jsonl")):
            shard = TRAIN / src / (lab.stem + ".parquet")
            if not shard.exists():
                continue
            human = {r["id"]: r for r in pq.read_table(shard, columns=["id", "text", "duration"]).to_pylist()}
            for r in read_jsonl(lab):
                h = human.get(r["id"])
                st = stats[src]
                st["seen"] += 1
                if h is None:
                    st["no_audio"] += 1
                    continue
                hyp = (r.get("hyp") or "").strip()
                if not hyp:
                    st["empty"] += 1
                    continue
                nh, nt = normalize.leaderboard(h["text"]), normalize.leaderboard(hyp)
                e, n = wer.edit_counts(nh, nt)
                rate = e / n if n else (0.0 if not e else 1.0)
                if rate > a.max_wer:
                    st["disagree"] += 1
                    continue
                cps = len(hyp) / max(h["duration"], 0.1)
                if not (2.0 <= cps <= 30.0) and len(hyp) > 6:
                    st["rate"] += 1
                    continue
                if LOOP.search(hyp):
                    st["loop"] += 1
                    continue
                if nt in held_out or nh in held_out:
                    st["leak"] += 1
                    continue
                st["kept"] += 1
                hours[src] += h["duration"] / 3600
                rows.append({"shard": f"{src}/{shard.name}", "id": r["id"], "text": hyp,
                             "duration": round(float(h["duration"]), 3), "source": src, "wer_h": round(rate, 4)})
    MANIFESTS.mkdir(parents=True, exist_ok=True)
    n = write_jsonl(MANIFESTS / f"{a.name}.jsonl", rows)
    report = {"labeller": a.labeller, "max_wer": a.max_wer, "kept": n,
              "hours": {k: round(v, 1) for k, v in hours.items()}, "total_hours": round(sum(hours.values()), 1),
              "per_source": {k: dict(v) for k, v in stats.items()}, "held_out_sentences": len(held_out)}
    write_json(MANIFESTS / f"{a.name}.report.json", report)
    log("manifest", f"{a.name}: kept {n} utterances, {report['total_hours']} h; "
        + ", ".join(f"{k}: {v['kept']}/{v['seen']} (leak {v['leak']})" for k, v in report["per_source"].items()))


if __name__ == "__main__":
    main()
