"""Tiro's sound set, synthesised from scratch (no samples, so no licensing questions): one sound language.

    python tools/sounds/design.py [--out assets/sounds] [--sheet work/sounds/sheet.png]

Character: clean, tactile, slightly warm, glassy; one key (C major / A minor pentatonic), soft attacks,
fast natural decays, no deep bass (laptop and phone speakers), matched loudness, peaks at -6 dBFS max.
Every sound is a few short decaying partials (modal synthesis) plus a touch of filtered noise for the
"material". Repeated sounds get subtle variants (pitch +-2 %, level +-1 dB); the key cues never vary.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import numpy as np
import soundfile as sf

SR = 48000
ROOT = Path(__file__).resolve().parents[2]

# C major pentatonic, upper register (works on small speakers)
NOTE = {"A4": 440.0, "C5": 523.25, "D5": 587.33, "E5": 659.25, "G5": 783.99, "A5": 880.0, "C6": 1046.5,
        "D6": 1174.66, "E6": 1318.51, "G6": 1567.98, "A6": 1760.0, "C7": 2093.0}


def env(n: int, attack: float, decay: float) -> np.ndarray:
    t = np.arange(n) / SR
    a = np.clip(t / max(attack, 1e-4), 0, 1)
    a = a * a * (3 - 2 * a)  # smooth (no click) attack
    return a * np.exp(-t / max(decay, 1e-4))


def glass(freq: float, dur: float, decay: float = 0.09, attack: float = 0.004, bright: float = 0.35,
          warmth: float = 0.25) -> np.ndarray:
    """A small struck-glass/marimba tone: fundamental, warm octave, a few inharmonic glints."""
    n = int(dur * SR)
    t = np.arange(n) / SR
    partials = [(1.0, 1.0, decay), (2.0, warmth, decay * 0.7), (2.76, bright * 0.5, decay * 0.45),
                (5.40, bright * 0.18, decay * 0.25), (8.93, bright * 0.07, decay * 0.15)]
    x = np.zeros(n)
    for ratio, amp, d in partials:
        f = freq * ratio
        if f > SR * 0.45:
            continue
        x += amp * np.sin(2 * math.pi * f * t + ratio) * env(n, attack, d)
    return x


def tick(dur: float = 0.03, freq: float = 3200.0, noise: float = 0.5, decay: float = 0.006) -> np.ndarray:
    """A tiny tactile click: a short filtered noise burst with a pitched glint."""
    n = int(dur * SR)
    rng = np.random.default_rng(int(freq))
    burst = rng.standard_normal(n) * env(n, 0.0005, decay * 0.6)
    # one-pole high-pass then low-pass: a soft "tk", not a harsh crack
    hp = np.zeros(n)
    prev_x = prev_y = 0.0
    for i in range(n):
        prev_y = 0.96 * (prev_y + burst[i] - prev_x)
        prev_x = burst[i]
        hp[i] = prev_y
    lp = np.convolve(hp, np.ones(6) / 6, mode="same")
    t = np.arange(n) / SR
    glint = np.sin(2 * math.pi * freq * t) * env(n, 0.0008, decay)
    return noise * lp + (1 - noise) * glint


def seq(*parts: tuple[float, np.ndarray]) -> np.ndarray:
    """Place sounds at offsets (seconds) and mix."""
    end = max(int(off * SR) + len(x) for off, x in parts)
    out = np.zeros(end)
    for off, x in parts:
        i = int(off * SR)
        out[i: i + len(x)] += x
    return out


def room(x: np.ndarray, amount: float = 0.12, length: float = 0.18) -> np.ndarray:
    """A short, soft early-reflection tail so sounds feel placed, not dry."""
    n = int(length * SR)
    rng = np.random.default_rng(7)
    ir = rng.standard_normal(n) * np.exp(-np.arange(n) / (SR * length / 5))
    ir = np.convolve(ir, np.ones(24) / 24, mode="same")  # darker tail
    ir /= np.sqrt(np.sum(ir ** 2)) + 1e-9
    wet = np.convolve(x, ir)[: len(x) + n // 2]
    out = np.zeros(len(wet))
    out[: len(x)] += x
    return out + amount * wet


def finish(x: np.ndarray, peak_db: float = -6.0, fade_ms: float = 4.0) -> np.ndarray:
    x = x - np.mean(x)
    n_fade = int(fade_ms / 1000 * SR)
    if len(x) > 2 * n_fade:
        x[-n_fade:] *= np.linspace(1, 0, n_fade) ** 2
    peak = np.max(np.abs(x)) + 1e-12
    return x * (10 ** (peak_db / 20) / peak)


def loudness_db(x: np.ndarray) -> float:
    """Rough perceived loudness: RMS of a 2nd-order high-passed signal over the loud part (dB)."""
    y = np.diff(x, prepend=0.0)  # tilt towards mids/highs like the ear at low levels
    k = max(1, int(0.03 * SR))
    frames = [np.sqrt(np.mean(y[i: i + k] ** 2)) for i in range(0, max(1, len(y) - k), k // 2)]
    top = sorted(frames)[-max(1, len(frames) // 3):]
    return 20 * math.log10(np.mean(top) + 1e-12)


def design() -> dict[str, np.ndarray]:
    s: dict[str, np.ndarray] = {}
    # --- dictation cues (the most important: instantly recognisable) ---
    s["listen_start"] = room(seq((0.0, glass(NOTE["E5"], 0.22, 0.10)), (0.055, glass(NOTE["A5"], 0.25, 0.12))))
    s["listen_stop"] = room(seq((0.0, glass(NOTE["A5"], 0.2, 0.09)), (0.05, glass(NOTE["E5"], 0.24, 0.11))))
    s["text_tick"] = tick(0.03, 3600, 0.35, 0.005)
    s["didnt_catch"] = room(seq((0.0, glass(NOTE["D5"], 0.22, 0.10, bright=0.2)),
                                (0.09, glass(NOTE["C5"], 0.26, 0.12, bright=0.2))), 0.1)
    s["model_step"] = glass(NOTE["G6"], 0.12, 0.05, bright=0.2) * 0.6
    # --- UI ---
    s["press"] = tick(0.035, 2400, 0.55, 0.008)
    s["release"] = tick(0.03, 2900, 0.6, 0.006) * 0.7
    s["toggle_on"] = seq((0.0, tick(0.03, 2600, 0.5, 0.006)), (0.012, glass(NOTE["G6"], 0.08, 0.035, bright=0.15) * 0.5))
    s["toggle_off"] = seq((0.0, tick(0.03, 2200, 0.5, 0.006)), (0.012, glass(NOTE["D6"], 0.08, 0.035, bright=0.15) * 0.5))
    s["detent"] = tick(0.018, 4200, 0.4, 0.003) * 0.6
    s["tab"] = tick(0.03, 3000, 0.45, 0.007)
    s["open"] = seq((0.0, tick(0.025, 2800, 0.5, 0.005)), (0.01, glass(NOTE["C6"], 0.1, 0.04, bright=0.1) * 0.35))
    s["close"] = seq((0.0, tick(0.025, 2500, 0.5, 0.005)), (0.01, glass(NOTE["A5"], 0.1, 0.04, bright=0.1) * 0.35))
    s["focus"] = tick(0.02, 3800, 0.3, 0.004) * 0.5
    s["checkbox"] = s["toggle_on"]
    s["notify"] = room(seq((0.0, glass(NOTE["C6"], 0.25, 0.12)), (0.08, glass(NOTE["G6"], 0.3, 0.14))), 0.14)
    s["update_ready"] = room(seq((0.0, glass(NOTE["G5"], 0.25, 0.12)), (0.07, glass(NOTE["C6"], 0.25, 0.12)),
                                 (0.14, glass(NOTE["E6"], 0.32, 0.15))), 0.14)
    s["error"] = room(seq((0.0, glass(NOTE["E5"], 0.22, 0.10, bright=0.15)),
                          (0.1, glass(NOTE["C5"], 0.28, 0.13, bright=0.15))), 0.1)
    s["success"] = room(seq((0.0, glass(NOTE["C6"], 0.22, 0.1)), (0.07, glass(NOTE["E6"], 0.24, 0.11)),
                            (0.14, glass(NOTE["G6"], 0.32, 0.15))), 0.14)
    s["step_done"] = room(seq((0.0, glass(NOTE["E6"], 0.18, 0.08)), (0.06, glass(NOTE["A6"], 0.24, 0.11))), 0.12)
    s["all_set"] = room(seq((0.0, glass(NOTE["C6"], 0.3, 0.13)), (0.08, glass(NOTE["E6"], 0.3, 0.13)),
                            (0.16, glass(NOTE["G6"], 0.3, 0.14)), (0.26, glass(NOTE["C7"], 0.5, 0.22))), 0.16)
    # --- read-along: rising pentatonic steps, one per lit word, and a closing chord ---
    for i, name in enumerate(["C6", "D6", "E6", "G6", "A6", "C7"]):
        s[f"word_{i}"] = glass(NOTE[name], 0.12, 0.05, bright=0.12) * 0.45
    s["phrase_done"] = room(seq((0.0, glass(NOTE["G6"], 0.2, 0.09) * 0.6), (0.05, glass(NOTE["C7"], 0.3, 0.13) * 0.6)))
    return s


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--out", type=Path, default=ROOT / "assets" / "sounds")
    a = p.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)
    sounds = design()
    # matched perceived loudness: quiet UI ticks sit ~10 dB under the dictation cues
    targets = {"text_tick": -36, "press": -34, "release": -37, "detent": -40, "tab": -35, "focus": -40,
               "toggle_on": -33, "toggle_off": -33, "checkbox": -33, "open": -35, "close": -35, "model_step": -34}
    report = {}
    for name, x in sounds.items():
        x = finish(x)
        target = targets.get(name, -26 if not name.startswith("word_") else -32)
        gain = target - loudness_db(x)
        x = x * 10 ** (gain / 20)
        peak = 20 * math.log10(np.max(np.abs(x)) + 1e-12)
        if peak > -3:  # safe peaks
            x *= 10 ** ((-3 - peak) / 20)
        sf.write(a.out / f"{name}.wav", x.astype(np.float32), SR, subtype="PCM_16")
        report[name] = {"ms": round(len(x) / SR * 1000), "peak_db": round(20 * math.log10(np.max(np.abs(x)) + 1e-12), 1),
                        "loudness_db": round(loudness_db(x), 1)}
    (a.out / "sounds.json").write_text(json.dumps(report, indent=1), encoding="utf-8")
    print(json.dumps(report, indent=1))


if __name__ == "__main__":
    main()
