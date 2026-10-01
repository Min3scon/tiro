"""Simulate live dictation: stream concatenated LibriSpeech through VAD + StreamingTranscriber.

Reports WER of the text that would have been typed, compared with offline per-clip decoding, plus
casing/punctuation drift against offline output and commit latency.

Usage: python dev/sim_stream.py [cuda|cpu] [n_clips] [--param value ...]
"""

from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

import jiwer
import numpy as np
import soundfile as sf

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tiro.asr import ParakeetEngine  # noqa: E402
from tiro.stream import StreamingTranscriber, StreamParams  # noqa: E402
from tiro.textproc import FormatOptions, TextAssembler, hold_back  # noqa: E402
from tiro.vad import FRAME, SileroVad, SpeechGate  # noqa: E402

DATA = ROOT / "tests" / "data" / "dummy"


_ABBREV = {"mr": "mister", "mrs": "missus", "dr": "doctor", "st": "saint"}


def norm(s: str) -> str:
    s = re.sub(r"[^a-z0-9' ]+", " ", s.lower())
    return " ".join(_ABBREV.get(w, w) for w in s.split())


def build_stream(n: int, seed: int = 7):
    refs = json.loads((DATA / "refs.json").read_text())[:n]
    rng = np.random.default_rng(seed)
    parts, texts = [], []
    for r in refs:
        wav, _ = sf.read(DATA / r["file"], dtype="float32")
        parts.append(wav)
        texts.append(r["text"])
        gap = rng.uniform(0.25, 1.4) if rng.random() < 0.85 else rng.uniform(2.0, 4.0)
        parts.append(np.zeros(int(gap * 16000), dtype=np.float32))
    audio = np.concatenate(parts)
    audio += (rng.standard_normal(audio.size) * 10 ** (-55 / 20)).astype(np.float32)
    return audio, texts, [p for p in parts[::2]]


def main() -> None:
    args = sys.argv[1:]
    device = args[0] if args and args[0] in ("cuda", "cpu", "auto") else "auto"
    n = int(args[1]) if len(args) > 1 and args[1].isdigit() else 73
    overrides = {}
    for i, a in enumerate(args):
        if a.startswith("--") and a != "--dict":
            overrides[a[2:]] = float(args[i + 1])
    cpu_decoder = overrides.pop("cpu_decoder", 0)
    use_dict = "--dict" in args
    params = StreamParams(**overrides)

    engine = ParakeetEngine(ROOT / "models" / "parakeet-tdt-0.6b-v2", device=device)
    engine.load()
    if use_dict:
        sys.path.insert(0, str(ROOT / "dev"))
        from eval_rare import DICTIONARY

        from tiro.vocab import Vocabulary

        n_paths = engine.set_vocabulary(Vocabulary.from_lines(DICTIONARY))
        print(f"dictionary active: {len(DICTIONARY)} entries, {n_paths} token spellings")
    if cpu_decoder:
        import onnxruntime as ort

        dso = ort.SessionOptions()
        dso.intra_op_num_threads = 1
        dso.inter_op_num_threads = 1
        engine._asr._decoder_joint = ort.InferenceSession(
            str(ROOT / "models" / "parakeet-tdt-0.6b-v2" / "decoder_joint-model.int8.onnx"),
            sess_options=dso, providers=["CPUExecutionProvider"])
        print("using CPU int8 decoder")
    vad = SileroVad(ROOT / "models" / "silero-vad" / "silero_vad.onnx")
    gate = SpeechGate()

    audio, ref_texts, clips = build_stream(n)
    print(f"stream: {len(audio) / 16000:.1f}s, {n} utterances, device={engine.device_label}, params={overrides}")

    t0 = time.perf_counter()
    offline = [" ".join(w.text for w in engine.transcribe(c)) for c in clips]
    t_off = time.perf_counter() - t0

    decode_times = []

    def timed_transcribe(a):
        t = time.perf_counter()
        out = engine.transcribe(a)
        decode_times.append((time.perf_counter() - t, len(a) / 16000))
        return out

    opts = FormatOptions()
    st = StreamingTranscriber(timed_transcribe, params, hold_back=lambda ws: hold_back(ws, opts))
    asm = TextAssembler(opts)
    latencies = []
    typed_chunks = []
    wall_clock = 0.0  # simulated real time (uncompressed)
    for i in range(0, len(audio) - FRAME + 1, FRAME):
        frame = audio[i : i + FRAME]
        wall_clock += FRAME / 16000
        speech = gate.update(vad(frame))
        st.push(frame, speech)
        if st.due():
            upd = st.step()
            if upd.committed:
                now = st.t / 16000
                latencies += [now - w.end for w in upd.committed]
                typed_chunks.append(asm.add([w.text for w in upd.committed]))
    upd = st.finalize()
    if upd.committed:
        typed_chunks.append(asm.add([w.text for w in upd.committed]))

    typed = asm.text
    gold = norm(" ".join(ref_texts))
    wer_stream = jiwer.wer(gold, norm(typed))
    wer_off = jiwer.wer(gold, norm(" ".join(offline)))
    # casing + punctuation sensitive comparison against offline output
    fmt = jiwer.wer(" ".join(offline), re.sub(r"\s+", " ", typed).strip())
    dt = np.array([d for d, _ in decode_times])
    win = np.array([w for _, w in decode_times])
    lat = np.array(latencies) if latencies else np.zeros(1)
    print(f"offline per-clip WER: {wer_off * 100:.2f}%   (decode {t_off:.1f}s)")
    print(f"streamed typed   WER: {wer_stream * 100:.2f}%")
    print(f"format drift vs offline (case+punct token WER): {fmt * 100:.2f}%")
    print(f"decodes: {len(dt)}  mean {dt.mean() * 1000:.1f}ms  p95 {np.percentile(dt, 95) * 1000:.1f}ms  "
          f"max {dt.max() * 1000:.1f}ms  window mean {win.mean():.1f}s max {win.max():.1f}s")
    print(f"commits: {len(typed_chunks)}  commit latency after word end: mean {lat.mean():.2f}s  "
          f"p90 {np.percentile(lat, 90):.2f}s  (non-final commits only)")
    print("\n--- typed (first 700 chars) ---\n" + typed[:700])
    print("\n--- offline (first 700 chars) ---\n" + " ".join(offline)[:700])
    out = ROOT / "dev" / "sim_output.txt"
    out.write_text("TYPED:\n" + typed + "\n\nOFFLINE:\n" + "\n".join(offline) + "\n\nCHUNKS:\n" + "\n".join(
        repr(c) for c in typed_chunks), encoding="utf-8")


if __name__ == "__main__":
    main()
