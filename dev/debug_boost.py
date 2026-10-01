"""Trace greedy decoding with/without boosting on one clip to see where tokens differ."""

import sys
from pathlib import Path

import numpy as np
import soundfile as sf

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tiro.asr import ParakeetEngine  # noqa: E402
from tiro.vocab import Vocabulary  # noqa: E402

sys.path.insert(0, str(ROOT / "dev"))
from eval_rare import DICTIONARY  # noqa: E402

clip = sys.argv[1] if len(sys.argv) > 1 else "02_hazel"
wav, _ = sf.read(ROOT / "tests" / "data" / "rare" / f"{clip}.wav", dtype="float32")
eng = ParakeetEngine(ROOT / "models" / "parakeet-tdt-0.6b-v2", device="cuda")
eng.load()
asr = eng._asr
feats, flens = asr._preprocessor(wav[None, :], np.array([len(wav)], dtype=np.int64))
enc, lens = asr._encode(feats, flens)


def trace(boost):
    asr.boost = boost
    toks, stamps, _ = next(asr._decoding(enc, lens))
    return [(s, asr._vocab[t]) for t, s in zip(toks, stamps)]


plain = trace(None)
eng.set_vocabulary(Vocabulary.from_lines(DICTIONARY))
boosted = trace(asr.boost)
print("plain  :", " ".join(f"{s}:{p!r}" for s, p in plain))
print("boosted:", " ".join(f"{s}:{p!r}" for s, p in boosted))
