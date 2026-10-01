"""Personal-vocabulary accuracy of the native engine: tests/accuracy/phrases.json (72 sentences x 3 voices).

    python -m training.eval.vocab_eval MODEL_DIR [--limit N] [--threads 2] [--no-vocab] [--tag name]

Scores word error rate against the expected written sentence (leaderboard normaliser, so only words count) and
the share of vocabulary terms that come out exactly right (case and spelling as the user writes them).
The audio was synthesised with Windows voices (Zira, Hazel, George) that are never used for training.
"""
from __future__ import annotations

import argparse
import json
import re
import time
from pathlib import Path

import numpy as np
import soundfile as sf

from training.common import REPO, RESULTS, write_json
from training.eval import tiro_core, wer

SETS = {"phrases": (REPO / "tests" / "accuracy" / "phrases.json", REPO / "tests" / "accuracy" / "audio" / "phrases"),
        "noharm": (REPO / "tests" / "accuracy" / "no_harm.json", REPO / "tests" / "accuracy" / "audio" / "noharm")}
DICT = REPO / "assets" / "words" / "starter-dictionary.txt"
COMMON = REPO / "assets" / "words" / "common-words-en.txt"
VAD = REPO / "assets" / "vad" / "silero_vad.onnx"


def dictionary_terms() -> list[str]:
    return [ln.strip() for ln in DICT.read_text(encoding="utf-8").splitlines() if ln.strip() and not ln.startswith("#")]


def term_hit(term: str, text: str) -> bool:
    return re.search(r"(?<![\w])" + re.escape(term) + r"(?![\w])", text) is not None


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("model", type=Path)
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--threads", type=int, default=2)
    p.add_argument("--no-vocab", action="store_true")
    p.add_argument("--tag", default="")
    p.add_argument("--set", default="phrases", choices=sorted(SETS))
    a = p.parse_args()
    spec, audio_dir = SETS[a.set]
    phrases = json.loads(spec.read_text(encoding="utf-8"))
    model = tiro_core.Model(a.model, threads=a.threads, vad=str(VAD))
    if not a.no_vocab:
        tiro_core_vocab(model, dictionary_terms())
    rows = []
    for ph in phrases:
        for voice in ("zira", "hazel", "george"):
            path = audio_dir / f"{ph['id']}_{voice}.wav"
            if not path.exists():
                continue
            rows.append((ph, voice, path))
    if a.limit:
        rows = rows[: a.limit]
    ids, refs, hyps, hits, total_terms = [], [], [], 0, 0
    t0 = time.time()
    for ph, voice, path in rows:
        audio, sr = sf.read(path, dtype="float32")
        if audio.ndim > 1:
            audio = audio.mean(axis=1)
        if sr != 16000:
            import soxr

            audio = soxr.resample(audio, sr, 16000).astype(np.float32)
        s = model.session(partial_ms=0)
        for i in range(0, len(audio), 1280):
            s.feed(audio[i: i + 1280])
        s.feed(np.zeros(16000, dtype=np.float32))
        text = s.finish().get("text", "").strip()
        s.close()
        ids.append(f"{ph['id']}-{voice}")
        refs.append(ph["expect"])
        hyps.append(text)
        for term in ph.get("terms", []):
            total_terms += 1
            hits += term_hit(term, text)
    sc = wer.score(ids, refs, hyps)
    lo, hi = wer.bootstrap_ci(sc, b=1000)
    res = {"model": str(a.model), "vocab": not a.no_vocab, "n": len(ids), "wer": sc.wer, "ci": [lo, hi],
           "terms_right": hits, "terms_total": total_terms, "seconds": time.time() - t0,
           "examples": [{"id": i, "ref": r, "hyp": h} for i, r, h in zip(ids, refs, hyps)]}
    tag = a.tag or f"{a.set}-{a.model.name}-{'vocab' if not a.no_vocab else 'novocab'}"
    write_json(RESULTS / "vocab" / f"{tag}.json", res)
    print(f"{tag}: n={len(ids)} WER {sc.wer:.2%} [{lo:.2%}-{hi:.2%}] terms right {hits}/{total_terms}")


def tiro_core_vocab(model: tiro_core.Model, terms: list[str]) -> None:
    import ctypes

    payload = json.dumps({"terms": terms, "common_words_file": str(COMMON)}).encode()
    model._l.tiro_model_set_vocabulary.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
    model._l.tiro_model_set_vocabulary(model._h, payload)


if __name__ == "__main__":
    main()
