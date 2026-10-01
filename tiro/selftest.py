"""`Tiro.exe --selftest [out.json]`: load the speech model and transcribe a known clip.

The installer runs this right after installing, to prove the setup works and to show real numbers
("recognised in 0.05 s on your RTX 3070"). Exit code 0 means the clip was transcribed correctly.
"""

from __future__ import annotations

import json
import re
import statistics
import time
import wave
from pathlib import Path

import numpy as np

from tiro.asr import ParakeetEngine
from tiro.config import Settings
from tiro.models import MODELS, find_model
from tiro.paths import asset

CLIP = "selftest.wav"  # LibriSpeech dev-clean 1272-128104-0000 (CC BY 4.0), 5.9 s
EXPECTED = "mister quilter is the apostle of the middle classes and we are glad to welcome his gospel"


def _norm(text: str) -> list[str]:
    words = re.sub(r"[^a-z' ]+", " ", text.lower()).split()
    return ["mister" if w == "mr" else w for w in words]


def _read_wav(path: Path) -> np.ndarray:
    with wave.open(str(path)) as w:
        if w.getframerate() != 16000 or w.getnchannels() != 1 or w.getsampwidth() != 2:
            raise ValueError("selftest clip must be 16 kHz mono PCM16")
        return np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype(np.float32) / 32768.0


def run(out_path: str | None = None, device: str | None = None) -> int:
    settings = Settings.load()
    spec = MODELS[settings.model]
    result: dict = {"ok": False, "model": spec.title, "requested_device": device or settings.device}
    try:
        folder = find_model(spec)
        if folder is None:
            raise FileNotFoundError(f"{spec.title} is not installed")
        t0 = time.perf_counter()
        engine = ParakeetEngine(folder, device=device or settings.device)
        engine.load()
        result["load_s"] = round(time.perf_counter() - t0, 2)
        audio = _read_wav(asset("sounds", CLIP))
        times, text = [], ""
        for _ in range(3):
            t = time.perf_counter()
            text = " ".join(w.text for w in engine.transcribe(audio))
            times.append(time.perf_counter() - t)
        got, want = _norm(text), _norm(EXPECTED)
        matched = sum(1 for w in want if w in got)
        result.update(
            device=engine.device,
            device_label=engine.device_label,
            variant=engine.variant,
            fallback_reason=engine.fallback_reason,
            text=text,
            audio_s=round(len(audio) / 16000, 2),
            decode_ms=round(statistics.median(times) * 1000, 1),
            accuracy=round(matched / len(want), 3),
            ok=matched / len(want) >= 0.85,
        )
    except Exception as exc:  # report, don't crash: the installer shows this to the user
        result["error"] = f"{type(exc).__name__}: {exc}"
    payload = json.dumps(result, indent=2)
    if out_path:
        Path(out_path).write_text(payload, encoding="utf-8")
    else:
        print(payload)
    return 0 if result["ok"] else 1


def apply_settings(assignments: list[str]) -> int:
    """`Tiro.exe --set key=value ...` (used by the installer). 'autostart' toggles the Run key."""
    from dataclasses import fields

    from tiro import autostart

    settings = Settings.load()
    kinds = {f.name: type(getattr(settings, f.name)) for f in fields(Settings)}
    for item in assignments:
        key, _, value = item.partition("=")
        key, value = key.strip(), value.strip()
        if key == "autostart":
            autostart.set_enabled(value.lower() in ("1", "true", "yes", "on"))
            continue
        if key not in kinds:
            continue
        kind = kinds[key]
        if kind is bool:
            setattr(settings, key, value.lower() in ("1", "true", "yes", "on"))
        elif kind is int:
            setattr(settings, key, int(value))
        elif kind is float:
            setattr(settings, key, float(value))
        elif kind is list:
            setattr(settings, key, [v for v in value.split("|") if v])
        else:
            setattr(settings, key, value or None)
    settings.save()
    return 0
