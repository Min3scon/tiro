"""Check that batched greedy TDT decoding gives exactly the per-utterance decoder's transcripts (app venv)."""
import io
import sys
import time
from pathlib import Path

import numpy as np
import pyarrow.parquet as pq
import soundfile as sf

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from training.common import TRAIN  # noqa: E402
from training.labels.parakeet_label import batched_greedy, build_asr  # noqa: E402
from tiro.asr import tokens_to_words  # noqa: E402

asr = build_asr(REPO / "models" / "parakeet-tdt-0.6b-v2", 0)
shard = sorted((TRAIN / "librispeech").glob("*.parquet"))[0]
rows = pq.read_table(shard, columns=["id", "audio", "duration"]).to_pylist()[:24]
wavs = [sf.read(io.BytesIO(r["audio"]), dtype="float32")[0] for r in rows]
same = 0
t_single = t_batch = 0.0
for start in range(0, len(wavs), 8):
    chunk = wavs[start:start + 8]
    n = max(len(w) for w in chunk)
    arr = np.zeros((len(chunk), n), dtype=np.float32)
    for i, w in enumerate(chunk):
        arr[i, :len(w)] = w
    lens = np.array([len(w) for w in chunk], dtype=np.int64)
    t0 = time.time()
    ref = [" ".join(w.text for w in tokens_to_words(r.tokens, r.timestamps, r.logprobs))
           for r in asr.recognize_batch(arr, lens)]
    t_single += time.time() - t0
    t0 = time.time()
    hyp = [" ".join(w.text for w in tokens_to_words(tk, [0.0] * len(tk), lp)) for tk, lp in batched_greedy(asr, arr, lens)]
    t_batch += time.time() - t0
    for a, b in zip(ref, hyp):
        same += a == b
        if a != b:
            print("DIFF\n  ", a, "\n  ", b)
print(f"identical {same}/{len(wavs)}; per-utterance loop {t_single:.1f}s, batched {t_batch:.1f}s")
