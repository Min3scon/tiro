"""Most frequent English words in the training corpora's own transcripts -> assets/words/common-words-en.txt.

The Lite correction pass never turns these into a brand or name unless the user taught it to. The list is
derived from LibriSpeech, AMI, VoxPopuli and People's Speech transcripts (CC-BY-4.0 / CC0), whichever are
converted, so it carries their attribution (see LICENSES.md).

    python -m training.tools.common_words [--top 20000]
"""
from __future__ import annotations

import argparse
import re
from collections import Counter

import pyarrow.parquet as pq

from training.common import REPO, TRAIN

WORD = re.compile(r"[a-z]+(?:'[a-z]+)?")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--top", type=int, default=20000)
    a = p.parse_args()
    counts: Counter[str] = Counter()
    per_source: Counter[str] = Counter()
    for src in sorted(d for d in TRAIN.iterdir() if d.is_dir() and not d.name.startswith("synth")):
        n_src = Counter()
        for shard in sorted(src.glob("*.parquet")):
            for text in pq.read_table(shard, columns=["text"]).column(0).to_pylist():
                n_src.update(WORD.findall((text or "").lower()))
        # each corpus counts equally, so audiobook English doesn't drown out speech from meetings
        total = sum(n_src.values()) or 1
        for w, c in n_src.items():
            counts[w] += c / total
        per_source[src.name] = total
    words = [w for w, _ in counts.most_common(a.top)]
    out = REPO / "assets" / "words" / "common-words-en.txt"
    out.write_text("\n".join(words) + "\n", encoding="utf-8")
    print(f"wrote {len(words)} words to {out} from {dict(per_source)}")


if __name__ == "__main__":
    main()
