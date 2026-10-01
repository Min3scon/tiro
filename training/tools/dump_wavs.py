"""Write utterances of an eval set as 16-bit WAV files plus refs.jsonl (for testing native tools).

    python -m training.tools.dump_wavs dev-ls-clean 20 work/tmp/wavs
"""
import json
import sys
from pathlib import Path

import numpy as np
import soundfile as sf

from training.eval import sets

name, n, out = sys.argv[1], int(sys.argv[2]), Path(sys.argv[3])
every = int(sys.argv[4]) if len(sys.argv) > 4 else 1
out.mkdir(parents=True, exist_ok=True)
with open(out / "refs.jsonl", "w", encoding="utf-8") as f:
    for i, u in enumerate(sets.utterances(name, limit=n, every=every)):
        path = out / f"{i:04d}.wav"
        sf.write(path, np.clip(u.audio, -1, 1), 16000, subtype="PCM_16")
        f.write(json.dumps({"file": str(path), "id": u.id, "ref": u.text, "sec": u.duration}) + "\n")
print("wrote", n, "files to", out)
