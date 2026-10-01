"""Join a few test sentences (with short natural pauses) into one longer dictation for the latency tests.

    python dev/latency/make_long_wav.py [--count 4] [--voice zira] [--gap 0.45] [--out work/results/latency/long.wav]
"""
from __future__ import annotations

import argparse
import json
import wave
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
AUDIO = ROOT / "tests" / "accuracy" / "audio" / "noharm"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--count", type=int, default=4)
    ap.add_argument("--voice", default="zira")
    ap.add_argument("--gap", type=float, default=0.45)
    ap.add_argument("--first", type=int, default=1)
    ap.add_argument("--out", type=Path, default=ROOT / "work" / "results" / "latency" / "long.wav")
    a = ap.parse_args()
    cases = {c["id"]: c for c in json.loads((ROOT / "tests" / "accuracy" / "no_harm.json").read_text(encoding="utf-8"))}
    parts, texts = [], []
    for i in range(a.first, a.first + a.count):
        path = AUDIO / f"nh-{i:03d}_{a.voice}.wav"
        with wave.open(str(path)) as w:
            assert w.getframerate() == 16000 and w.getnchannels() == 1
            parts.append(np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16))
        parts.append(np.zeros(int(a.gap * 16000), dtype=np.int16))
        case = cases.get(f"nh-{i:03d}")
        if case:
            texts.append(case["expect"])
    audio = np.concatenate(parts[:-1])
    a.out.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(a.out), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(audio.tobytes())
    print(f"{a.out}: {len(audio) / 16000:.1f} s")
    if texts:
        print(" ".join(texts))


if __name__ == "__main__":
    main()
