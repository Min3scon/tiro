"""Streaming Silero VAD (ONNX, CPU): one speech probability per 32 ms frame."""

from __future__ import annotations

from pathlib import Path

import numpy as np

FRAME = 512  # samples per VAD frame at 16 kHz (32 ms)
_CONTEXT = 64


class SileroVad:
    def __init__(self, model_path: Path):
        import onnxruntime as ort

        so = ort.SessionOptions()
        so.intra_op_num_threads = 1
        so.inter_op_num_threads = 1
        so.log_severity_level = 3
        so.add_session_config_entry("session.intra_op.allow_spinning", "0")
        self._sess = ort.InferenceSession(str(model_path), sess_options=so, providers=["CPUExecutionProvider"])
        self._sr = np.array([16000], dtype=np.int64)
        self.reset()

    def reset(self) -> None:
        self._state = np.zeros((2, 1, 128), dtype=np.float32)
        self._context = np.zeros(_CONTEXT, dtype=np.float32)

    def __call__(self, frame: np.ndarray) -> float:
        """Speech probability for exactly FRAME samples of 16 kHz audio."""
        x = np.concatenate((self._context, frame.astype(np.float32, copy=False)))[None, :]
        out, self._state = self._sess.run(["output", "stateN"], {"input": x, "state": self._state, "sr": self._sr})
        self._context = x[0, -_CONTEXT:].copy()
        return float(out[0, 0])


class SpeechGate:
    """Hysteresis on top of per-frame probabilities (Silero's recommended 0.5 on / 0.35 off)."""

    def __init__(self, on: float = 0.5, off: float = 0.35, hangover_frames: int = 6):
        self.on = on
        self.off = off
        self.hangover_frames = hangover_frames  # keep "speech" briefly after it drops (~190 ms)
        self.reset()

    def reset(self) -> None:
        self.active = False
        self._below = 0

    def update(self, prob: float) -> bool:
        if self.active:
            if prob < self.off:
                self._below += 1
                if self._below > self.hangover_frames:
                    self.active = False
            else:
                self._below = 0
        elif prob >= self.on:
            self.active = True
            self._below = 0
        return self.active
