"""Rolling measurements of what correction costs, for the debug panel and for auto-degrading."""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass


@dataclass
class Sample:
    ts: float
    ms: float  # time correction added before the text could be typed
    tier: int
    changes: int
    flagged: int


def _pct(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    return s[min(len(s) - 1, int(q * (len(s) - 1) + 0.5))]


class Metrics:
    def __init__(self, size: int = 400):
        self._samples: deque[Sample] = deque(maxlen=size)
        self._ends: deque[tuple[float, float]] = deque(maxlen=size)  # (end-of-speech -> text ms, correction ms)
        self._lock = threading.Lock()

    def add(self, res) -> None:
        with self._lock:
            self._samples.append(Sample(time.time(), res.ms, res.tier, len(res.changes), res.flagged))

    def add_end(self, total_ms: float, correction_ms: float) -> None:
        """End of speech to the last word typed, and how much of that was the correction pass."""
        with self._lock:
            self._ends.append((total_ms, correction_ms))

    def summary(self, last: int = 100) -> dict:
        with self._lock:
            samples = list(self._samples)[-last:]
            ends = list(self._ends)[-last:]
        ms = [s.ms for s in samples]
        active = [s.ms for s in samples if s.tier > 0]
        return {
            "commits": len(samples),
            "median_ms": _pct(ms, 0.5),
            "p95_ms": _pct(ms, 0.95),
            "median_active_ms": _pct(active, 0.5),
            "p95_active_ms": _pct(active, 0.95),
            "tier0_share": (sum(1 for s in samples if s.tier == 0) / len(samples)) if samples else 1.0,
            "tier2_share": (sum(1 for s in samples if s.tier == 2) / len(samples)) if samples else 0.0,
            "changes": sum(s.changes for s in samples),
            "end_median_ms": _pct([e[0] for e in ends], 0.5),
            "end_without_ms": _pct([e[0] - e[1] for e in ends], 0.5),
            "end_p95_ms": _pct([e[0] for e in ends], 0.95),
            "ends": len(ends),
        }

    def reset(self) -> None:
        with self._lock:
            self._samples.clear()

    def recent(self, n: int = 20) -> list[Sample]:
        with self._lock:
            return list(self._samples)[-n:]
