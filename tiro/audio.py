"""Microphone capture (PortAudio/WASAPI via sounddevice) delivering 16 kHz mono float32."""

from __future__ import annotations

import logging
import queue
import threading

import numpy as np

log = logging.getLogger(__name__)

TARGET_SR = 16_000
_pa_lock = threading.RLock()
_open_streams = 0


class MicError(RuntimeError):
    pass


def _sd():
    import sounddevice as sd

    return sd


def _wasapi_index(sd) -> int | None:
    for i, api in enumerate(sd.query_hostapis()):
        if "WASAPI" in api["name"]:
            return i
    return None


def refresh_devices() -> None:
    """Re-scan audio devices (PortAudio only enumerates them at init). Skipped while a stream is open."""
    with _pa_lock:
        if _open_streams:
            return
        sd = _sd()
        try:
            sd._terminate()
            sd._initialize()
        except Exception:
            log.exception("PortAudio re-init failed")


def list_input_devices() -> list[str]:
    with _pa_lock:
        sd = _sd()
        api = _wasapi_index(sd)
        names = []
        for dev in sd.query_devices():
            if dev["max_input_channels"] > 0 and (api is None or dev["hostapi"] == api):
                names.append(dev["name"])
        return names


def default_input_name() -> str | None:
    with _pa_lock:
        sd = _sd()
        api = _wasapi_index(sd)
        idx = sd.query_hostapis(api)["default_input_device"] if api is not None else sd.default.device[0]
        if idx is None or idx < 0:
            return None
        return sd.query_devices(idx)["name"]


def _resolve(sd, name: str | None) -> int:
    api = _wasapi_index(sd)
    if name:
        for i, dev in enumerate(sd.query_devices()):
            if dev["name"] == name and dev["max_input_channels"] > 0 and (api is None or dev["hostapi"] == api):
                return i
        log.warning("Microphone %r not found; using the default input", name)
    idx = sd.query_hostapis(api)["default_input_device"] if api is not None else sd.default.device[0]
    if idx is None or idx < 0:
        raise MicError("No microphone found")
    return idx


class AudioCapture:
    """Opens the microphone, picks the live channel of multi-input interfaces, resamples to 16 kHz."""

    def __init__(self, device_name: str | None = None):
        self.device_name = device_name
        self.level = 0.0  # 0..1, for the overlay meter
        self.peak = 0.0  # loudest sample seen since open(), to detect a dead/muted microphone
        self.opened_name = ""
        self._q: queue.Queue[np.ndarray] = queue.Queue()
        self._stream = None
        self._resampler = None
        self._energy: np.ndarray | None = None
        self._channel = 0

    def open(self) -> None:
        global _open_streams
        import soxr

        with _pa_lock:
            sd = _sd()
            try:
                idx = _resolve(sd, self.device_name)
                info = sd.query_devices(idx)
                sr = int(info["default_samplerate"])
                channels = max(1, min(2, int(info["max_input_channels"])))
                self._energy = np.zeros(channels, dtype=np.float64)
                self._channel = 0
                self._resampler = (
                    soxr.ResampleStream(sr, TARGET_SR, 1, dtype="float32", quality="HQ") if sr != TARGET_SR else None
                )
                self._stream = sd.InputStream(
                    device=idx,
                    samplerate=sr,
                    channels=channels,
                    dtype="float32",
                    blocksize=0,
                    latency="low",
                    callback=self._callback,
                )
                self._stream.start()
                _open_streams += 1
            except MicError:
                raise
            except Exception as exc:
                self._stream = None
                raise MicError(f"Could not open microphone: {exc}") from exc
        self.opened_name = info["name"]
        log.info("Microphone open: %s (%d Hz, %d ch)", info["name"], sr, channels)

    def _callback(self, indata, frames, time_info, status) -> None:  # PortAudio thread
        if indata.shape[1] > 1:
            ch_energy = np.square(indata).mean(axis=0)
            self._energy = 0.97 * self._energy + 0.03 * ch_energy
            best = int(np.argmax(self._energy))
            if best != self._channel and self._energy[best] > 2.0 * self._energy[self._channel]:
                self._channel = best
            mono = indata[:, self._channel].copy()
        else:
            mono = indata[:, 0].copy()
        rms = float(np.sqrt(np.mean(np.square(mono)) + 1e-12))
        db = 20.0 * np.log10(rms)
        target = min(1.0, max(0.0, (db + 58.0) / 42.0))
        self.level = target if target > self.level else self.level * 0.82 + target * 0.18
        self.peak = max(self.peak, float(np.max(np.abs(mono))) if mono.size else 0.0)
        self._q.put(mono)

    def read(self, timeout: float = 0.05) -> np.ndarray | None:
        """Next chunk of 16 kHz mono audio, or None if nothing arrived within the timeout."""
        try:
            chunk = self._q.get(timeout=timeout)
        except queue.Empty:
            return None
        if self._resampler is not None:
            chunk = self._resampler.resample_chunk(chunk)
        return chunk

    def drain(self) -> list[np.ndarray]:
        out = []
        while True:
            chunk = self.read(timeout=0)
            if chunk is None:
                return out
            out.append(chunk)

    def close(self) -> None:
        global _open_streams
        with _pa_lock:
            if self._stream is not None:
                try:
                    self._stream.stop()
                    self._stream.close()
                except Exception:
                    log.exception("closing microphone failed")
                self._stream = None
                _open_streams -= 1
        self.level = 0.0


class MicHub:
    """Keeps the microphone open ("instant start") with a short rolling buffer.

    Dictations subscribe to it and get the last moment of audio from *before* the key was pressed, so the
    first syllable is never clipped and there is no stream-opening delay. Nothing is stored beyond the
    rolling buffer.
    """

    RING_SEC = 1.0

    def __init__(self, device_name: str | None):
        self.device_name = device_name
        self.opened_name = ""
        self._capture = AudioCapture(device_name)
        self._ring: list[np.ndarray] = []
        self._ring_len = 0
        self._subs: list[SharedCapture] = []
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    def start(self) -> None:
        self._capture.open()
        self.opened_name = self._capture.opened_name
        self._stop.clear()
        self._thread = threading.Thread(target=self._pump, name="tiro-mic-hub", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(2)
        self._capture.close()

    @property
    def level(self) -> float:
        return self._capture.level

    def _pump(self) -> None:
        while not self._stop.is_set():
            chunk = self._capture.read(timeout=0.1)
            if chunk is None or not chunk.size:
                continue
            with self._lock:
                self._ring.append(chunk)
                self._ring_len += chunk.size
                while self._ring and self._ring_len - self._ring[0].size >= self.RING_SEC * TARGET_SR:
                    self._ring_len -= self._ring.pop(0).size
                for sub in self._subs:
                    sub._feed(chunk)

    def subscribe(self, preroll_sec: float = 0.35) -> SharedCapture:
        sub = SharedCapture(self)
        with self._lock:
            need = int(preroll_sec * TARGET_SR)
            pre: list[np.ndarray] = []
            for chunk in reversed(self._ring):
                if need <= 0:
                    break
                pre.insert(0, chunk[-need:])
                need -= chunk.size
            if pre:
                sub._feed(np.concatenate(pre))
            self._subs.append(sub)
        return sub

    def _unsubscribe(self, sub: SharedCapture) -> None:
        with self._lock:
            if sub in self._subs:
                self._subs.remove(sub)


class SharedCapture(AudioCapture):
    """One dictation's view of a MicHub (same interface as AudioCapture)."""

    def __init__(self, hub: MicHub):
        super().__init__(hub.device_name)
        self.hub = hub
        self.opened_name = hub.opened_name

    def _feed(self, chunk: np.ndarray) -> None:
        if chunk.size:
            self.peak = max(self.peak, float(np.max(np.abs(chunk))))
        self._q.put(chunk)

    def open(self) -> None:  # the hub's stream is already running
        pass

    def read(self, timeout: float = 0.05) -> np.ndarray | None:
        try:
            return self._q.get(timeout=timeout)
        except queue.Empty:
            return None

    @property
    def level(self) -> float:  # type: ignore[override]
        return self.hub.level

    @level.setter
    def level(self, _v: float) -> None:
        pass

    def close(self) -> None:
        self.hub._unsubscribe(self)


class FileCapture(AudioCapture):
    """Test double that plays a WAV file as if it were the microphone (TIRO_TEST_WAV)."""

    def __init__(self, path: str):
        super().__init__(None)
        self.path = path
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    def open(self) -> None:
        import soundfile as sf

        audio, sr = sf.read(self.path, dtype="float32", always_2d=True)
        mono = audio.mean(axis=1)
        if sr != TARGET_SR:
            import soxr

            mono = soxr.resample(mono, sr, TARGET_SR)
        self.opened_name = f"file:{self.path}"
        self._stop.clear()

        def play():
            import time

            block = 480
            t0 = time.perf_counter()
            for i, start in enumerate(range(0, len(mono), block)):
                if self._stop.is_set():
                    return
                chunk = mono[start : start + block].astype(np.float32)
                rms = float(np.sqrt(np.mean(np.square(chunk)) + 1e-12))
                self.level = min(1.0, max(0.0, (20 * np.log10(rms) + 58.0) / 42.0))
                self.peak = max(self.peak, float(np.max(np.abs(chunk))) if chunk.size else 0.0)
                self._q.put(chunk)
                delay = t0 + (i + 1) * block / TARGET_SR - time.perf_counter()
                if delay > 0:
                    time.sleep(delay)
            while not self._stop.is_set():  # then silence, like a real mic
                self._q.put(np.zeros(block, dtype=np.float32))
                self.level = 0.0
                time.sleep(block / TARGET_SR)

        self._thread = threading.Thread(target=play, daemon=True)
        self._thread.start()

    def close(self) -> None:
        self._stop.set()
        self.level = 0.0
