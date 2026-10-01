"""Speech-recognition backends used by the benchmarks, all behind one tiny interface:

    rec = make(spec, threads)      # loads the model
    text = rec(audio_float32_16k)  # transcribes one utterance (offline, whole utterance)

Specs:
    sherpa:<model dir>                    sherpa-onnx; model type detected from the files
    sherpa:<model dir>?beam=4             modified beam search (transducers)
    moonshine:<model dir>?arch=small      Moonshine Voice runtime (Moonshine Streaming models, .ort)
    hf-moonshine:<hf repo or dir>         PyTorch Moonshine Streaming via transformers (GPU if available)
"""
from __future__ import annotations

import glob
import os
import urllib.parse
from pathlib import Path
from typing import Callable

import numpy as np

Recognizer = Callable[[np.ndarray], str]


def _parse(spec: str) -> tuple[str, str, dict]:
    kind, _, rest = spec.partition(":")
    path, _, query = rest.partition("?")
    opts = dict(urllib.parse.parse_qsl(query))
    return kind, path, opts


def _one(folder: Path, pattern: str) -> str:
    hits = sorted(glob.glob(str(folder / pattern)))
    if not hits:
        raise FileNotFoundError(f"{pattern} not found in {folder}")
    # prefer int8 when both exist
    hits.sort(key=lambda p: (".int8." not in p, len(p)))
    return hits[0]


def _sherpa(folder: Path, threads: int, opts: dict) -> Recognizer:
    import sherpa_onnx as so

    files = {p.name for p in folder.iterdir()}
    beam = int(opts.get("beam", 0))
    if "decoder_model_merged.ort" in files:
        rec = so.OfflineRecognizer.from_moonshine_v2(
            encoder=str(folder / "encoder_model.ort"), decoder=str(folder / "decoder_model_merged.ort"),
            tokens=str(folder / "tokens.txt"), num_threads=threads)
    elif any(f.startswith("joiner") for f in files):
        kw = {}
        if beam:
            kw = dict(decoding_method="modified_beam_search", max_active_paths=beam)
        if opts.get("hotwords"):
            kw.update(hotwords_file=opts["hotwords"], hotwords_score=float(opts.get("hotwords_score", 1.5)),
                      modeling_unit="bpe", bpe_vocab=opts.get("bpe_vocab", ""))
        rec = so.OfflineRecognizer.from_transducer(
            encoder=_one(folder, "encoder*.onnx"), decoder=_one(folder, "decoder*.onnx"),
            joiner=_one(folder, "joiner*.onnx"), tokens=str(folder / "tokens.txt"), num_threads=threads,
            # 80 mel bins unless the encoder's metadata says otherwise (sherpa-onnx reads feat_dim from it)
            model_type="nemo_transducer", feature_dim=int(opts.get("feature_dim", 80)), **kw)
    elif any("-encoder" in f for f in files):
        rec = so.OfflineRecognizer.from_whisper(
            encoder=_one(folder, "*-encoder*.onnx"), decoder=_one(folder, "*-decoder*.onnx"),
            tokens=_one(folder, "*-tokens.txt"), num_threads=threads, language="en", task="transcribe")
    else:
        raise ValueError(f"can't tell the model type of {folder}")

    def run(audio: np.ndarray) -> str:
        s = rec.create_stream()
        s.accept_waveform(16000, audio)
        rec.decode_stream(s)
        return s.result.text.strip()

    return run


def _moonshine(folder: Path, threads: int, opts: dict) -> Recognizer:
    from moonshine_voice.moonshine_api import ModelArch
    from moonshine_voice.transcriber import Transcriber

    arch = {"tiny": ModelArch.TINY_STREAMING, "small": ModelArch.SMALL_STREAMING,
            "medium": ModelArch.MEDIUM_STREAMING, "base": ModelArch.BASE_STREAMING,
            "tiny-v1": ModelArch.TINY, "base-v1": ModelArch.BASE}[opts.get("arch", "small")]
    # The Moonshine Voice runtime has no thread-count option: ONNX Runtime picks one thread per physical core.
    tr = Transcriber(str(folder), model_arch=arch, options={"vad_threshold": "0"} if opts.get("novad") else None)

    def run(audio: np.ndarray) -> str:
        t = tr.transcribe_without_streaming(audio.tolist(), sample_rate=16000)
        return " ".join(line.text.strip() for line in t.lines if line.text.strip())

    return run


def _hf_moonshine(repo: str, threads: int, opts: dict) -> Recognizer:
    import torch
    from transformers import AutoModelForSpeechSeq2Seq, AutoProcessor

    dev = "cuda" if torch.cuda.is_available() and opts.get("device", "cuda") == "cuda" else "cpu"
    dtype = torch.float16 if dev == "cuda" else torch.float32
    proc = AutoProcessor.from_pretrained(repo)
    model = AutoModelForSpeechSeq2Seq.from_pretrained(repo, torch_dtype=dtype).to(dev).eval()
    torch.set_num_threads(max(threads, 1))

    @torch.inference_mode()
    def run(audio: np.ndarray) -> str:
        inputs = proc(audio, sampling_rate=16000, return_tensors="pt").to(dev)
        inputs = {k: (v.to(dtype) if v.is_floating_point() else v) for k, v in inputs.items()}
        # Moonshine emits ~6.5 tokens per second of audio at most; cap generation like the model card does
        max_new = int(len(audio) / 16000 * 6.5) + 10
        out = model.generate(**inputs, max_new_tokens=max_new)
        return proc.batch_decode(out, skip_special_tokens=True)[0].strip()

    return run


def split_points(audio: np.ndarray, max_sec: float, sr: int = 16000) -> list[tuple[int, int]]:
    """Cut long audio into pieces of at most max_sec, each cut placed at the quietest 200 ms stretch in the
    last 40% of the window (a pause, if there is one). Nothing is dropped: the pieces tile the input."""
    n = len(audio)
    limit = int(max_sec * sr)
    if n <= limit:
        return [(0, n)]
    hop = sr // 100  # 10 ms
    frames = n // hop
    energy = np.square(audio[: frames * hop].reshape(frames, hop)).mean(axis=1)
    smooth = np.convolve(energy, np.ones(20) / 20, mode="same")  # 200 ms
    pieces, start = [], 0
    while n - start > limit:
        lo = (start + int(limit * 0.6)) // hop
        hi = (start + limit) // hop
        cut = (lo + int(np.argmin(smooth[lo:hi]))) * hop
        pieces.append((start, cut))
        start = cut
    pieces.append((start, n))
    return pieces


def _chunked(rec: Recognizer, max_sec: float) -> Recognizer:
    def run(audio: np.ndarray) -> str:
        parts = [rec(audio[a:b]) for a, b in split_points(audio, max_sec)]
        return " ".join(p for p in parts if p)

    return run


def make(spec: str, threads: int = 1) -> Recognizer:
    """Build a recogniser. `?max=SECONDS` (default 12) cuts longer audio at pauses first, the same way the
    app's voice-activity detector hands the engine one phrase at a time; `?max=0` feeds whole utterances."""
    kind, path, opts = _parse(spec)
    if kind == "sherpa":
        rec = _sherpa(Path(path), threads, opts)
    elif kind == "moonshine":
        rec = _moonshine(Path(path), threads, opts)
    elif kind == "hf-moonshine":
        rec = _hf_moonshine(path, threads, opts)
    elif kind == "tiro":
        rec = _tiro(path, threads, opts)
    elif kind in ("core", "core-live"):
        return _core(Path(path), threads, opts, live=kind == "core-live")
    else:
        raise ValueError(f"unknown backend {kind!r}")
    max_sec = float(opts.get("max", 12))
    return _chunked(rec, max_sec) if max_sec > 0 else rec


def _core(folder: Path, threads: int, opts: dict, live: bool) -> Recognizer:
    """The native Tiro engine. core: whole utterance at once (chunked at pauses like the other backends).
    core-live: the dictation pipeline itself (VAD gating, phrases committed at pauses), fed in 80 ms chunks."""
    from training.eval import tiro_core

    repo = Path(__file__).resolve().parents[2]
    vad = str(repo / "assets" / "vad" / "silero_vad.onnx")
    model = tiro_core.Model(folder, threads=threads, vad=vad if live else "",
                            prepack=opts.get("prepack", "0") == "1")
    if not live:
        rec = lambda audio: model.transcribe(audio).get("text", "").strip()  # noqa: E731
        max_sec = float(opts.get("max", 12))
        return _chunked(rec, max_sec) if max_sec > 0 else rec

    def run(audio: np.ndarray) -> str:
        s = model.session(partial_ms=int(opts.get("partial_ms", 0)))
        step = 1280
        for i in range(0, len(audio), step):
            s.feed(audio[i: i + step])
        s.feed(np.zeros(int(16000 * 0.8), dtype=np.float32))  # the speaker stops; a pause ends the phrase
        text = s.finish().get("text", "")
        s.close()
        return text.strip()

    run.model = model  # type: ignore[attr-defined]
    return run


def _tiro(device: str, threads: int, opts: dict) -> Recognizer:
    """The shipped Standard engine (tiro.asr.ParakeetEngine): only importable from the app's own venv.
    device: cuda | cpu; ?dir=<model folder> picks e.g. the int8-only folder a CPU-only install has."""
    import sys

    repo = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(repo))
    from tiro.asr import ParakeetEngine

    folder = Path(opts.get("dir") or repo / "models" / "parakeet-tdt-0.6b-v2")
    eng = ParakeetEngine(folder, device=device or "cpu")
    eng.load()
    if device == "cuda" and eng.device != "cuda":
        raise RuntimeError(f"CUDA unavailable: {eng.fallback_reason}")

    def run(audio: np.ndarray) -> str:
        return " ".join(w.text for w in eng.transcribe(audio)).strip()

    run.engine = eng  # type: ignore[attr-defined]
    return run


def model_bytes(spec: str) -> int:
    """Total size of the model files a spec loads (what a user would download)."""
    kind, path, _ = _parse(spec)
    p = Path(path)
    if not p.exists():
        return 0
    exts = (".onnx", ".ort", ".bin", ".txt", ".json", ".safetensors")
    total = 0
    for f in p.rglob("*"):
        if f.is_file() and f.suffix in exts and "LICENSE" not in f.name:
            # sherpa folders often ship fp32 and int8 side by side; count only what's loaded
            if kind == "sherpa" and f.suffix == ".onnx" and ".int8." not in f.name and \
                    (f.with_name(f.name.replace(".onnx", ".int8.onnx"))).exists():
                continue
            total += f.stat().st_size
    return total


os.environ.setdefault("OMP_NUM_THREADS", "1")
