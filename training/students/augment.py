"""Waveform augmentation for training: noise, room reverb, phone/Bluetooth channels, speed and gain.

Everything works on 16 kHz float32 mono numpy arrays and is deterministic given the numpy Generator passed in.
Noise and room responses come from MUSAN (CC-BY-4.0) and RIRS_NOISES (Apache 2.0) when they are downloaded;
without them only the synthetic effects run.
"""
from __future__ import annotations

import glob
import os
from dataclasses import dataclass, field

import numpy as np
import scipy.signal as ss
import soundfile as sf

from training.common import DATA

SR = 16000


@dataclass
class AugmentConfig:
    p_noise: float = 0.35
    snr_db: tuple = (3.0, 30.0)
    p_reverb: float = 0.25
    p_phone: float = 0.12  # narrowband phone line (300-3400 Hz, companding, 8 kHz)
    p_bluetooth: float = 0.06  # wideband headset: 16 kHz, mild compression, dropped packets
    p_speakerphone: float = 0.05  # far mic: reverb + level compression + noise
    p_speed: float = 0.25
    speed: tuple = (0.9, 1.1)
    p_gain: float = 0.5
    gain_db: tuple = (-12.0, 8.0)
    p_lowpass: float = 0.08  # cheap/old microphones
    noise_dirs: list = field(default_factory=lambda: [str(DATA / "noise" / "musan" / "noise"),
                                                      str(DATA / "noise" / "musan" / "music")])
    babble_dir: str = str(DATA / "noise" / "musan" / "speech")
    rir_dirs: list = field(default_factory=lambda: [str(DATA / "noise" / "RIRS_NOISES" / "real_rirs_isotropic_noises"),
                                                    str(DATA / "noise" / "RIRS_NOISES" / "simulated_rirs")])


class Augmenter:
    def __init__(self, cfg: AugmentConfig | None = None):
        self.cfg = cfg or AugmentConfig()
        self.noise_files = [f for d in self.cfg.noise_dirs for f in glob.glob(os.path.join(d, "**", "*.wav"),
                                                                              recursive=True)]
        self.babble_files = glob.glob(os.path.join(self.cfg.babble_dir, "**", "*.wav"), recursive=True)
        self.rir_files = [f for d in self.cfg.rir_dirs for f in glob.glob(os.path.join(d, "**", "*.wav"),
                                                                          recursive=True)]
        self._cache: dict[str, np.ndarray] = {}

    def _load(self, path: str, max_sec: float | None = None, rng=None) -> np.ndarray:
        if path in self._cache:
            x = self._cache[path]
        else:
            x, sr = sf.read(path, dtype="float32", always_2d=False)
            if x.ndim > 1:
                x = x[:, 0]
            if sr != SR:
                import soxr

                x = soxr.resample(x, sr, SR).astype(np.float32)
            if len(self._cache) < 512 and len(x) < SR * 30:
                self._cache[path] = x
        if max_sec and len(x) > max_sec * SR and rng is not None:
            start = int(rng.integers(0, len(x) - int(max_sec * SR)))
            x = x[start: start + int(max_sec * SR)]
        return x

    # ---------------------------------------------------------------- effects
    def add_noise(self, x, rng, snr_db=None, babble=False):
        pool = self.babble_files if babble and self.babble_files else self.noise_files
        if pool:
            n = self._load(pool[int(rng.integers(len(pool)))], max_sec=len(x) / SR + 1, rng=rng)
            if len(n) < len(x):
                n = np.tile(n, int(np.ceil(len(x) / max(len(n), 1))))
            n = n[: len(x)]
        else:  # synthetic coloured noise
            n = rng.standard_normal(len(x)).astype(np.float32)
            n = ss.lfilter([1.0], [1.0, -float(rng.uniform(0.0, 0.95))], n).astype(np.float32)
        snr = float(rng.uniform(*self.cfg.snr_db)) if snr_db is None else snr_db
        ps = float(np.mean(x ** 2)) + 1e-10
        pn = float(np.mean(n ** 2)) + 1e-10
        return x + n * np.sqrt(ps / (pn * 10 ** (snr / 10)))

    def reverb(self, x, rng):
        if not self.rir_files:
            return x
        h = self._load(self.rir_files[int(rng.integers(len(self.rir_files)))])
        h = h[: SR]  # first second is plenty
        peak = int(np.argmax(np.abs(h)))
        h = h[max(0, peak - 16):]
        h = h / (np.sqrt(np.sum(h ** 2)) + 1e-8)
        y = ss.fftconvolve(x, h)[: len(x)]
        return y.astype(np.float32) * (np.std(x) / (np.std(y) + 1e-8))

    @staticmethod
    def _mulaw(x, mu=255.0):
        y = np.sign(x) * np.log1p(mu * np.abs(x)) / np.log1p(mu)
        y = np.round(y * 127) / 127  # 8-bit companded samples
        return np.sign(y) * ((1 + mu) ** np.abs(y) - 1) / mu

    def phone(self, x, rng):
        sos = ss.butter(6, [300, 3400], btype="bandpass", fs=SR, output="sos")
        y = ss.sosfilt(sos, x)
        y = y[::2]  # 8 kHz
        y = self._mulaw(np.clip(y * float(rng.uniform(1, 3)), -1, 1)) / 2
        y = np.repeat(y, 2)[: len(x)]
        y = ss.sosfilt(ss.butter(8, 3600, fs=SR, output="sos"), y)
        return y.astype(np.float32)

    def bluetooth(self, x, rng):
        sos = ss.butter(4, [80, 7200], btype="bandpass", fs=SR, output="sos")
        y = ss.sosfilt(sos, x)
        y = np.tanh(y * 2.0) / 2.0  # headset AGC/compression
        # dropped packets: 7.5 ms frames (mSBC) zeroed with concealment-like fade
        frame = int(0.0075 * SR)
        for _ in range(int(rng.integers(0, max(2, len(y) // (SR // 2))))):
            i = int(rng.integers(0, max(1, len(y) - frame)))
            y[i: i + frame] *= np.linspace(1, 0, frame) * 0.2
        return y.astype(np.float32)

    def speakerphone(self, x, rng):
        y = self.reverb(x, rng)
        y = np.tanh(y * 4) / 4
        return self.add_noise(y, rng, snr_db=float(rng.uniform(5, 20)))

    @staticmethod
    def speed(x, factor):
        import soxr

        return soxr.resample(x, SR * factor, SR).astype(np.float32)

    # ---------------------------------------------------------------- pipeline
    def __call__(self, x: np.ndarray, rng: np.random.Generator) -> np.ndarray:
        c = self.cfg
        y = x.astype(np.float32, copy=True)
        if rng.random() < c.p_speed:
            y = self.speed(y, float(rng.uniform(*c.speed)))
        channel = rng.random()
        if channel < c.p_phone:
            y = self.phone(y if rng.random() < 0.5 else self.add_noise(y, rng, float(rng.uniform(10, 30))), rng)
        elif channel < c.p_phone + c.p_bluetooth:
            y = self.bluetooth(self.add_noise(y, rng, float(rng.uniform(8, 30))), rng)
        elif channel < c.p_phone + c.p_bluetooth + c.p_speakerphone:
            y = self.speakerphone(y, rng)
        else:
            if rng.random() < c.p_reverb:
                y = self.reverb(y, rng)
            if rng.random() < c.p_noise:
                y = self.add_noise(y, rng, babble=rng.random() < 0.2)
            if rng.random() < c.p_lowpass:
                cutoff = float(rng.uniform(3500, 7000))
                y = ss.sosfilt(ss.butter(6, cutoff, fs=SR, output="sos"), y).astype(np.float32)
        if rng.random() < c.p_gain:
            y = y * 10 ** (float(rng.uniform(*c.gain_db)) / 20)
        peak = float(np.max(np.abs(y))) if len(y) else 0.0
        if peak > 0.99:  # clip like a real ADC would, but only occasionally
            y = np.clip(y, -1, 1) if rng.random() < 0.3 else y * (0.98 / peak)
        return y.astype(np.float32)
