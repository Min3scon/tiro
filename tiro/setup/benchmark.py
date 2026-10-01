"""A quick, real benchmark for the setup wizard: speech recognition speed and accuracy on a known clip, and
how long the correction pass (including the AI check) takes on this computer."""

from __future__ import annotations

import logging
import statistics
import time
from dataclasses import dataclass

from tiro.asr import Word
from tiro.paths import asset
from tiro.selftest import CLIP, EXPECTED, _norm, _read_wav

log = logging.getLogger(__name__)

AI_BUDGET_MS = 150.0  # an AI check slower than this can't keep up with speech: switch it off


@dataclass
class SpeechResult:
    ok: bool
    device: str = ""
    device_label: str = ""
    decode_ms: float = 0.0
    audio_s: float = 0.0
    accuracy: float = 0.0
    text: str = ""
    error: str = ""

    @property
    def speedup(self) -> float:
        return (self.audio_s * 1000 / self.decode_ms) if self.decode_ms else 0.0


@dataclass
class CorrectionResult:
    ok: bool
    ai_ms: float = 0.0  # median AI check
    pass_ms: float = 0.0  # median full correction pass on a doubtful sentence
    ai_name: str = ""
    ai_device: str = ""
    error: str = ""

    @property
    def ai_fast_enough(self) -> bool:
        return self.ok and self.ai_ms <= AI_BUDGET_MS


def speech(engine) -> SpeechResult:
    try:
        audio = _read_wav(asset("sounds", CLIP))
        times, text = [], ""
        for _ in range(4):
            t = time.perf_counter()
            text = " ".join(w.text for w in engine.transcribe(audio))
            times.append((time.perf_counter() - t) * 1000)
        got, want = _norm(text), _norm(EXPECTED)
        acc = sum(1 for w in want if w in got) / len(want)
        return SpeechResult(acc >= 0.85, engine.device, engine.device_label, statistics.median(times[1:]),
                            len(audio) / 16000, acc, text)
    except Exception as exc:
        log.exception("speech benchmark failed")
        return SpeechResult(False, error=str(exc))


def correction(corrector) -> CorrectionResult:
    """Time the AI check on a few realistic questions, and a full correction pass on a doubtful sentence."""
    lm = corrector.language
    if lm is None:
        return CorrectionResult(False, error="no AI model loaded")
    try:
        cases = [
            ("I've been fighting with", "rust", ["Rust"], "all weekend and",
             ["I rewrote the parser in Rust because it was too slow."]),
            ("Can you send me the", "swift", ["SWIFT"], "code when you", ["Our bank charges a fee on SWIFT transfers."]),
            ("We played", "George asser", ["GeoGuessr"], "last night", []),
        ]
        times = []
        for _ in range(2):
            for left, orig, opts, right, ex in cases:
                if hasattr(lm, "_memo"):
                    lm._memo.clear()
                t = time.perf_counter()
                lm.compare(left, orig, opts, right, timeout=5.0, examples=ex)
                times.append((time.perf_counter() - t) * 1000)
        words = [Word(w, i * 0.4, i * 0.4 + 0.3, 0.5 if w in ("George", "asser") else 0.98)
                 for i, w in enumerate("We played George asser last night and nailed the map.".split())]
        pass_times = []
        for _ in range(3):
            if hasattr(lm, "_memo"):
                lm._memo.clear()
            t = time.perf_counter()
            corrector.begin().correct(words, budget_ms=1000.0, record=False)
            pass_times.append((time.perf_counter() - t) * 1000)
        return CorrectionResult(True, statistics.median(times), statistics.median(pass_times),
                                getattr(lm, "name", ""), getattr(lm, "device", ""))
    except Exception as exc:
        log.exception("correction benchmark failed")
        return CorrectionResult(False, error=str(exc))
