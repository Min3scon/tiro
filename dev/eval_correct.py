"""Streams test audio through the real pipeline (VAD -> streaming ASR -> correction) and scores it.

  rare words:   does the expected name come out right?            (dev/rare_words.json clips)
  do no harm:   on LibriSpeech, how often does correction change a word that was right?

Usage: python dev/eval_correct.py [cuda|cpu] [--configs base,boost,correct,both] [--libri N]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

import numpy as np
import soundfile as sf

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "dev"))

from eval_rare import DICTIONARY  # noqa: E402

from tiro.asr import ParakeetEngine  # noqa: E402
from tiro.correct import knowledge  # noqa: E402
from tiro.correct.acoustic import AcousticScorer  # noqa: E402
from tiro.correct.corrector import Corrector  # noqa: E402
from tiro.stream import StreamingTranscriber, StreamParams  # noqa: E402
from tiro.textproc import FormatOptions, TextAssembler, hold_back  # noqa: E402
from tiro.vad import FRAME, SileroVad, SpeechGate  # noqa: E402
from tiro.vocab import Vocabulary  # noqa: E402


def stream_text(engine, vad, audio: np.ndarray, run=None) -> tuple[str, list[float], list[str]]:
    """Text as it would be typed; correction times at each commit; raw (uncorrected) words."""
    opts = FormatOptions()
    def hold(committable, rest):
        n = hold_back([w.text for w in committable], opts)
        return max(n, run.hold_back(committable, rest)) if run is not None else n

    st = StreamingTranscriber(engine.transcribe, StreamParams(), hold_back=hold)
    asm = TextAssembler(opts)
    asm_raw = TextAssembler(opts)  # the same words without correction
    vad.reset()
    gate = SpeechGate()
    times: list[float] = []
    raw: list[str] = []
    pad = np.zeros(int(0.6 * 16000), dtype=np.float32)
    audio = np.concatenate([pad, audio, pad])

    def apply(upd):
        words = upd.committed
        if words:
            raw.extend(w.text for w in words)
            asm_raw.add([w.text for w in words])
            if run is not None:
                t = time.perf_counter()
                res = run.correct(words, after=[w.text for w in upd.pending])
                times.append((time.perf_counter() - t) * 1000)
                words = res.words
            asm.add([w.text for w in words])
        if run is not None and upd.pending and not upd.final:
            run.prefetch(upd.pending)  # in the app this runs on a worker thread between decodes

    for i in range(0, len(audio) - FRAME + 1, FRAME):
        frame = audio[i : i + FRAME]
        st.push(frame, gate.update(vad(frame)))
        if st.due():
            apply(st.step())
    apply(st.finalize())
    return asm.text.strip(), times, asm_raw.text.strip().split()


def words_exact(s: str) -> list[str]:
    """Words with edge punctuation stripped, case kept (a casing change counts as an edit)."""
    return [w for w in (re.sub(r"^[^\w]+|[^\w]+$", "", t) for t in s.split()) if w]


def norm(s: str) -> list[str]:
    return re.sub(r"[^a-z0-9' ]+", " ", s.lower()).split()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("device", nargs="?", default="cuda")
    ap.add_argument("--configs", default="base,boost,correct,both")
    ap.add_argument("--libri", type=int, default=40)
    ap.add_argument("--mode", default="balanced")
    args = ap.parse_args()

    engine = ParakeetEngine(ROOT / "models" / "parakeet-tdt-0.6b-v2", device=args.device)
    engine.load()
    vad = SileroVad(ROOT / "assets" / "vad" / "silero_vad.onnx")
    starter = knowledge.starter_lexicon()
    k = knowledge.build(DICTIONARY, [], None, starter)
    corrector = Corrector(k, scorer=AcousticScorer(engine), mode=args.mode)
    vocab = Vocabulary.from_lines(DICTIONARY)
    print(f"device {engine.device_label}; dictionary {len(DICTIONARY)} terms; starter {len(starter)} terms")

    cases = json.loads((ROOT / "dev" / "rare_words.json").read_text())
    rare = []
    for i, c in enumerate(cases):
        for voice in ("hazel", "zira"):
            wav, _ = sf.read(ROOT / "tests" / "data" / "rare" / f"{i:02d}_{voice}.wav", dtype="float32")
            rare.append((c["expect"], wav, f"{i:02d}_{voice}"))
    refs = json.loads((ROOT / "tests" / "data" / "dummy" / "refs.json").read_text())[: args.libri]
    libri = [(r["text"], sf.read(ROOT / "tests" / "data" / "dummy" / r["file"], dtype="float32")[0]) for r in refs]

    for cfg in args.configs.split(","):
        engine.set_vocabulary(vocab if cfg in ("boost", "both") else None)
        use = cfg in ("correct", "both")
        hits, times, outs = 0, [], []
        for expect, wav, name in rare:
            run = corrector.begin() if use else None
            text, t, _ = stream_text(engine, vad, wav, run)
            times += t
            ok = expect in text
            hits += ok
            outs.append(f"   {'OK ' if ok else '-- '} {name}: {text}")
        # do no harm: words that were right before correction and changed after
        harmed = fixed = changed = total = 0
        for ref, wav in libri:
            run = corrector.begin() if use else None
            text, t, raw = stream_text(engine, vad, wav, run)
            times += t
            if not use:
                continue
            before, after = words_exact(" ".join(raw)), words_exact(text)
            ref_words = set(norm(ref)) | {w.lower() for w in norm(ref)}
            total += len(before)
            if before != after:
                import difflib

                sm = difflib.SequenceMatcher(a=before, b=after, autojunk=False)
                for op, i1, i2, j1, j2 in sm.get_opcodes():
                    if op == "equal":
                        continue
                    changed += 1
                    print(f"      edit: {' '.join(before[i1:i2])!r} -> {' '.join(after[j1:j2])!r}")
                    if all(w.lower() in ref_words for w in before[i1:i2]):
                        harmed += 1
                    elif all(w.lower() in ref_words for w in after[j1:j2]):
                        fixed += 1
        times.sort()
        med = times[len(times) // 2] if times else 0.0
        p95 = times[int(0.95 * (len(times) - 1))] if times else 0.0
        print(f"\n== {cfg}: rare words {hits}/{len(rare)}"
              + (f"; LibriSpeech: {changed} edits ({fixed} fixes, {harmed} harmful) over {total} words;"
                 f" correction at commit median {med:.1f} ms, p95 {p95:.1f} ms" if use else ""))
        for o in outs:
            print(o)


if __name__ == "__main__":
    main()
