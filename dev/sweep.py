"""Sweep StreamParams on the simulated dictation stream (model loaded once)."""

from __future__ import annotations

import re
import sys
import time
from pathlib import Path

import jiwer
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "dev"))

from sim_stream import build_stream, norm  # noqa: E402

from tiro.asr import ParakeetEngine  # noqa: E402
from tiro.stream import StreamingTranscriber, StreamParams  # noqa: E402
from tiro.textproc import FormatOptions, TextAssembler, hold_back  # noqa: E402
from tiro.vad import FRAME, SileroVad, SpeechGate  # noqa: E402


def run(engine, vad_path, audio, gold, offline, **kw):
    vad, gate = SileroVad(vad_path), SpeechGate()
    times = []

    def tr(a):
        t = time.perf_counter()
        out = engine.transcribe(a)
        times.append(time.perf_counter() - t)
        return out

    opts = FormatOptions()
    st = StreamingTranscriber(tr, StreamParams(**kw), hold_back=lambda ws: hold_back(ws, opts))
    asm = TextAssembler(opts)
    lat = []
    for i in range(0, len(audio) - FRAME + 1, FRAME):
        frame = audio[i : i + FRAME]
        st.push(frame, gate.update(vad(frame)))
        if st.due():
            upd = st.step()
            if upd.committed:
                lat += [st.t / 16000 - w.end for w in upd.committed]
                asm.add([w.text for w in upd.committed])
    upd = st.finalize()
    asm.add([w.text for w in upd.committed])
    wer = jiwer.wer(gold, norm(asm.text))
    fmt = jiwer.wer(" ".join(offline), re.sub(r"\s+", " ", asm.text).strip())
    t = np.array(times)
    print(f"{str(kw):70s} WER={wer * 100:5.2f}% fmt={fmt * 100:5.2f}% lat={np.mean(lat):4.2f}s "
          f"p90={np.percentile(lat, 90):4.2f}s dec={t.mean() * 1000:5.1f}ms p95={np.percentile(t, 95) * 1000:5.1f}ms "
          f"n={len(t)}", flush=True)


def main():
    engine = ParakeetEngine(ROOT / "models" / "parakeet-tdt-0.6b-v2", device="cuda")
    engine.load()
    vad_path = ROOT / "models" / "silero-vad" / "silero_vad.onnx"
    audio, texts, clips = build_stream(73)
    gold = norm(" ".join(texts))
    offline = [" ".join(w.text for w in engine.transcribe(c)) for c in clips]
    configs = [
        {},
        {"trim_after_sec": 5.0},
        {"trim_after_sec": 5.0, "right_speech_sec": 0.6},
        {"trim_after_sec": 5.0, "right_speech_sec": 1.2},
        {"trim_after_sec": 5.0, "left_context_sec": 2.0},
        {"trim_after_sec": 4.0, "step_sec": 0.3},
        {"trim_after_sec": 6.0, "right_speech_sec": 0.75, "left_context_sec": 1.5},
    ]
    for kw in configs:
        run(engine, vad_path, audio, gold, offline, **kw)


if __name__ == "__main__":
    main()
