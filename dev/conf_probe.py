"""How well does the decoder's word confidence separate misrecognised words from correct ones?"""

import json
import re
import sys
from pathlib import Path

import numpy as np
import soundfile as sf

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tiro.asr import ParakeetEngine  # noqa: E402

eng = ParakeetEngine(ROOT / "models" / "parakeet-tdt-0.6b-v2", device="cuda")
eng.load()

print("--- rare-word clips (word:conf) ---")
for f in sorted((ROOT / "tests" / "data" / "rare").glob("*.wav"))[:24]:
    wav, _ = sf.read(f, dtype="float32")
    words = eng.transcribe(wav)
    print(f.stem, " ".join(f"{w.text}:{w.conf:.2f}" for w in words))

print("\n--- LibriSpeech: confidence of correct vs wrong words ---")
refs = json.loads((ROOT / "tests" / "data" / "dummy" / "refs.json").read_text())


def norm(s):
    return re.sub(r"[^a-z0-9']", "", s.lower())


good, bad = [], []
import difflib  # noqa: E402

for r in refs:
    wav, _ = sf.read(ROOT / "tests" / "data" / "dummy" / r["file"], dtype="float32")
    words = eng.transcribe(wav)
    hyp = [norm(w.text) for w in words]
    ref = [norm(x) for x in r["text"].split()]
    sm = difflib.SequenceMatcher(a=ref, b=hyp, autojunk=False)
    ok = set()
    for block in sm.get_matching_blocks():
        ok.update(range(block.b, block.b + block.size))
    for i, w in enumerate(words):
        (good if i in ok else bad).append(w.conf)
good, bad = np.array(good), np.array(bad)
print(f"correct words: {len(good)}  wrong words: {len(bad)}")
for thr in (0.5, 0.7, 0.8, 0.9, 0.95):
    print(f"  conf < {thr}: flags {np.mean(good < thr) * 100:5.1f}% of correct words, "
          f"catches {np.mean(bad < thr) * 100:5.1f}% of wrong words")
