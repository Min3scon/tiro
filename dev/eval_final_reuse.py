"""Phase A1: can the last decode made during the pause stand in for the decode after the key is released?

When you stop talking, Tiro decodes once ~0.2 s into the pause. If no speech follows, the decode after the key
comes up sees the same speech plus a little more silence. This script measures how often the two disagree, on
the accuracy sets (synthesized speech, 3 voices), running the real streaming code on the GPU or CPU.

    python dev/eval_final_reuse.py [--device cuda] [--sets noharm,phrases] [--limit 0] [--out work/results/latency/final_reuse.json]
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np
import soundfile as sf

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tiro.asr import ParakeetEngine
from tiro.models import MODELS, find_model
from tiro.paths import asset
from tiro.stream import StreamingTranscriber, StreamParams
from tiro.vad import FRAME, SileroVad, SpeechGate

AUDIO = ROOT / "tests" / "accuracy" / "audio"


def run_clip(engine, vad, audio: np.ndarray, tail_sec: float) -> dict:
    st = StreamingTranscriber(engine.transcribe, StreamParams())
    vad.reset()
    gate = SpeechGate()
    info = {"silence_at_decode": 0}
    orig = st._decode

    def decode():
        info["silence_at_decode"] = st.silence_run
        return orig()

    st._decode = decode
    pad = np.zeros(int(0.6 * 16000), dtype=np.float32)
    audio = np.concatenate([pad, audio, np.zeros(int(tail_sec * 16000), dtype=np.float32)])
    committed: list[str] = []
    for i in range(0, len(audio) - FRAME + 1, FRAME):
        frame = audio[i: i + FRAME]
        st.push(frame, gate.update(vad(frame)))
        if st.due():
            committed += [w.text for w in st.step().committed]
    eligible = st._speech_since_decode == 0 and info["silence_at_decode"] >= 0.2 * st.sr and st.has_speech
    reuse = [w.text for w in st.pending]
    t = time.perf_counter()
    final = [w.text for w in st.finalize().committed]
    ms = (time.perf_counter() - t) * 1000
    return {"eligible": eligible, "same": reuse == final, "reuse": " ".join(committed + reuse),
            "final": " ".join(committed + final), "final_ms": ms}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--device", default="cuda")
    ap.add_argument("--sets", default="noharm,phrases")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--tails", default="0.25,0.45", help="silence after speech when the key comes up (seconds)")
    ap.add_argument("--out", type=Path, default=ROOT / "work" / "results" / "latency" / "final_reuse.json")
    a = ap.parse_args()
    engine = ParakeetEngine(find_model(MODELS["parakeet-tdt-0.6b-v2"]), device=a.device)
    engine.load()
    vad = SileroVad(asset("vad", "silero_vad.onnx"))
    clips = []
    for name in a.sets.split(","):
        clips += sorted((AUDIO / name).glob("*.wav"))
    if a.limit:
        clips = clips[: a.limit]
    report = {"device": engine.device_label, "clips": len(clips), "tails": {}}
    for tail in (float(x) for x in a.tails.split(",")):
        n_elig = n_same = 0
        diffs, final_ms = [], []
        for path in clips:
            audio, sr = sf.read(path, dtype="float32")
            assert sr == 16000
            r = run_clip(engine, vad, audio, tail)
            final_ms.append(r["final_ms"])
            if r["eligible"]:
                n_elig += 1
                n_same += r["same"]
                if not r["same"]:
                    diffs.append({"clip": path.name, "reuse": r["reuse"], "final": r["final"]})
        report["tails"][str(tail)] = {
            "eligible": n_elig, "identical": n_same, "differ": len(diffs),
            "final_decode_ms_median": float(np.median(final_ms)) if final_ms else None,
            "diffs": diffs,
        }
        print(f"tail {tail}s: {n_elig}/{len(clips)} eligible, {n_same} identical, {len(diffs)} differ; "
              f"final decode median {np.median(final_ms):.1f} ms", flush=True)
        for d in diffs[:15]:
            print(f"   {d['clip']}\n      reuse: {d['reuse']}\n      final: {d['final']}")
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(report, indent=1), encoding="utf-8")
    print("wrote", a.out)


if __name__ == "__main__":
    main()
