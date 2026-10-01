"""Show what the corrector considered for some clips: flagged words, candidates, evidence, audio penalty.

Usage: python dev/debug_correct.py [cuda|cpu] [--boost] 00_hazel 02_zira ...   (or --libri N)
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import soundfile as sf

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "dev"))

from eval_correct import stream_text  # noqa: E402
from eval_rare import DICTIONARY  # noqa: E402

from tiro.asr import ParakeetEngine  # noqa: E402
from tiro.correct import knowledge  # noqa: E402
from tiro.correct.acoustic import AcousticScorer  # noqa: E402
from tiro.correct.corrector import Corrector  # noqa: E402
from tiro.vad import SileroVad  # noqa: E402
from tiro.vocab import Vocabulary  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("device", nargs="?", default="cuda")
    ap.add_argument("clips", nargs="*")
    ap.add_argument("--boost", action="store_true")
    ap.add_argument("--libri", type=int, default=0)
    ap.add_argument("--all", action="store_true", help="print every candidate, not only the best per span")
    args = ap.parse_args()
    engine = ParakeetEngine(ROOT / "models" / "parakeet-tdt-0.6b-v2", device=args.device)
    engine.load()
    if args.boost:
        engine.set_vocabulary(Vocabulary.from_lines(DICTIONARY))
    vad = SileroVad(ROOT / "models" / "silero-vad" / "silero_vad.onnx")
    k = knowledge.build(DICTIONARY, [], None, knowledge.starter_lexicon())
    corrector = Corrector(k, scorer=AcousticScorer(engine))
    items = []
    for name in args.clips:
        items.append((name, sf.read(ROOT / "tests" / "data" / "rare" / f"{name}.wav", dtype="float32")[0]))
    if args.libri:
        refs = json.loads((ROOT / "tests" / "data" / "dummy" / "refs.json").read_text())[: args.libri]
        for r in refs:
            items.append((r["file"], sf.read(ROOT / "tests" / "data" / "dummy" / r["file"], dtype="float32")[0]))
    for name, wav in items:
        run = corrector.begin()
        run.trace = []
        text, _, raw = stream_text(engine, vad, wav, run)
        changed = " ".join(raw) != text
        if args.libri and not args.clips and not changed and not args.all:
            continue
        print(f"\n[{name}] raw: {' '.join(raw)}\n        out: {text}")
        seen = {}
        for row in run.trace:
            heard, written = row[0], row[1]
            key = (heard, written)
            seen[key] = row  # keep the last (commit-time) evaluation
        rows = sorted(seen.values(), key=lambda r: -r[6])
        for heard, written, source, sim, prior, delta, total, guarded, support, confs in rows[: (50 if args.all else 8)]:
            d = f"{delta:5.1f}" if delta is not None else "  n/a"
            print(f"   {heard!r:28s} -> {written!r:22s} {source:10s} sim {sim:.2f} prior {prior:5.1f} "
                  f"audio {d} total {total:5.1f}{' GUARDED' if guarded else ''} ctx {support:.1f} conf {confs}")


if __name__ == "__main__":
    main()
