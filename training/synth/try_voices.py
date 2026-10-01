"""Synthesise a few sentences with each voice set (quick listening/speed check before the big job)."""
import time

import numpy as np
import soundfile as sf

from training.common import WORK, read_jsonl
from training.synth import tts

out = WORK / "tmp" / "voices"
out.mkdir(parents=True, exist_ok=True)
rows = list(read_jsonl(WORK / "data" / "synth" / "sentences.jsonl"))[:6]
for name, _, n_spk in tts.VOICE_SETS:
    eng = tts._engine(name)
    t0 = time.time()
    total = 0.0
    for i, r in enumerate(rows):
        a = eng.generate(r["say"], sid=(i * 37) % n_spk, speed=1.0)
        x = np.asarray(a.samples, dtype=np.float32)
        total += len(x) / a.sample_rate
        sf.write(out / f"{name}-{i}.wav", x, a.sample_rate)
    el = time.time() - t0
    print(f"{name:11s} {total:6.1f}s of speech in {el:5.1f}s (RTF {el / total:.3f})")
