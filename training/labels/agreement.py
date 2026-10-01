"""How well do a labeller's transcripts agree with a corpus's own human transcripts?

    python -m training.labels.agreement parakeet-v2 [source ...]
Prints pooled WER per source and the share of utterances under several per-utterance WER thresholds
(the distil-whisper style filter keeps an utterance when its teacher/human WER is under a threshold).
"""
from __future__ import annotations

import sys

import numpy as np
import pyarrow.parquet as pq

from training.common import LABELS, TRAIN, read_jsonl
from training.eval import normalize, wer


def main() -> None:
    labeller, *sources = sys.argv[1:]
    root = LABELS / labeller
    sources = sources or sorted(d.name for d in root.iterdir() if d.is_dir())
    for src in sources:
        errs, words, rates, hours = [], [], [], 0.0
        for lab in sorted((root / src).glob("*.jsonl")):
            shard = TRAIN / src / (lab.stem + ".parquet")
            if not shard.exists():
                continue
            ref = {r["id"]: (r["text"], r["duration"]) for r in
                   pq.read_table(shard, columns=["id", "text", "duration"]).to_pylist()}
            for row in read_jsonl(lab):
                if row["id"] not in ref:
                    continue
                text, dur = ref[row["id"]]
                e, n = wer.edit_counts(normalize.leaderboard(text), normalize.leaderboard(row["hyp"]))
                errs.append(e)
                words.append(n)
                rates.append(e / n if n else (0.0 if e == 0 else 1.0))
                hours += dur / 3600
        if not words:
            continue
        rates = np.asarray(rates)
        print(f"{src:16s} utts={len(rates):7d} hours={hours:7.1f} WER vs human={sum(errs) / max(sum(words), 1):.2%} "
              + " ".join(f"<={t:.0%}:{(rates <= t).mean():.1%}" for t in (0.0, 0.05, 0.1, 0.2)))


if __name__ == "__main__":
    main()
