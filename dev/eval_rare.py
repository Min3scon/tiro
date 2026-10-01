"""Rare-word recognition test: how often does the expected word come out right, with/without a dictionary?

Usage: python dev/eval_rare.py [--dict] [--boost-start X --boost-cont Y] [--no-fuzzy]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import soundfile as sf

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tiro.asr import ParakeetEngine  # noqa: E402

DICTIONARY = ["GeoGuessr", "Kubernetes", "Siobhan", "PyTorch", "Zettelkasten", "Tiro", "Supabase", "Jira",
              "Niamh", "Nuitka", "Nando's", "Figma", "Obsidian", "Confluence", "Shoreditch", "PyInstaller"]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dict", action="store_true")
    ap.add_argument("--boost-start", type=float, default=None)
    ap.add_argument("--boost-cont", type=float, default=None)
    ap.add_argument("--no-fuzzy", action="store_true")
    ap.add_argument("--device", default="cuda")
    args = ap.parse_args()

    cases = json.loads((ROOT / "dev" / "rare_words.json").read_text())
    eng = ParakeetEngine(ROOT / "models" / "parakeet-tdt-0.6b-v2", device=args.device)
    eng.load()
    vocab = None
    if args.dict:
        from tiro.vocab import Vocabulary

        vocab = Vocabulary.from_lines(DICTIONARY)
        kw = {}
        if args.boost_start is not None:
            kw["start_bonus"] = args.boost_start
        if args.boost_cont is not None:
            kw["cont_bonus"] = args.boost_cont
        eng.set_vocabulary(vocab, **kw)
    hits = 0
    total = 0
    for f in sorted((ROOT / "tests" / "data" / "rare").glob("*.wav")):
        idx = int(f.stem.split("_")[0])
        expect = cases[idx]["expect"]
        wav, _ = sf.read(f, dtype="float32")
        words = [w.text for w in eng.transcribe(wav)]
        if vocab is not None and not args.no_fuzzy:
            words = vocab.correct(words)
        text = " ".join(words)
        ok = expect.lower() in text.lower().replace(" ", " ")
        hits += ok
        total += 1
        print(f"{'OK ' if ok else '-- '} {f.stem:12s} [{expect}] {text}")
    print(f"\n{hits}/{total} expected words recognised")


if __name__ == "__main__":
    main()
