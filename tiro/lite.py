"""Tiro Lite: the shared C++ speech engine (core/, the same code the phone and web versions run) inside Tiro.

The engine is a DLL (tiro_core.dll) next to Tiro.exe. It uses the ONNX Runtime that Tiro already ships, so the
process only ever holds one copy of it. Models live in the per-user data folder (never in the app folder, so app
updates don't touch them), one folder per rung of the ladder (see rungs.json).
"""
from __future__ import annotations

import contextlib
import ctypes
import json
import logging
import os
import sys
import threading
from pathlib import Path

import numpy as np

from tiro.paths import asset, bundle_dir, user_models_dir

log = logging.getLogger(__name__)

LIB_NAMES = {"win32": "tiro_core.dll", "darwin": "libtiro_core.dylib"}


def lib_path() -> Path:
    override = os.environ.get("TIRO_CORE_LIB")
    if override:
        return Path(override)
    name = LIB_NAMES.get(sys.platform, "libtiro_core.so")
    for folder in (bundle_dir(), bundle_dir() / "lite", Path(__file__).resolve().parents[1] / "build" / "core-win-x64"):
        for candidate in (folder / name, folder / ("lib" + name)):
            if candidate.is_file():
                return candidate
    return bundle_dir() / name


class _Lib:
    _inst: _Lib | None = None
    _lock = threading.Lock()

    def __init__(self) -> None:
        path = lib_path()
        if sys.platform == "win32":
            # use the ONNX Runtime Tiro already ships (one copy per process), then the DLL's own folder
            try:
                import onnxruntime

                os.add_dll_directory(str(Path(onnxruntime.__file__).parent / "capi"))
            except (ImportError, OSError) as exc:
                log.debug("no shared ONNX Runtime folder: %s", exc)
            os.add_dll_directory(str(path.parent))
        lib = ctypes.CDLL(str(path))
        c = ctypes
        lib.tiro_version.restype = c.c_char_p
        lib.tiro_model_load.restype = c.c_void_p
        lib.tiro_model_load.argtypes = [c.c_char_p, c.c_char_p, c.POINTER(c.c_void_p)]
        lib.tiro_model_free.argtypes = [c.c_void_p]
        lib.tiro_model_transcribe.argtypes = [c.c_void_p, c.POINTER(c.c_float), c.c_size_t, c.POINTER(c.c_void_p)]
        lib.tiro_model_set_vocabulary.argtypes = [c.c_void_p, c.c_char_p]
        lib.tiro_session_begin.restype = c.c_void_p
        lib.tiro_session_begin.argtypes = [c.c_void_p, c.c_char_p]
        lib.tiro_session_feed.argtypes = [c.c_void_p, c.POINTER(c.c_float), c.c_size_t]
        lib.tiro_session_poll.argtypes = [c.c_void_p, c.POINTER(c.c_void_p)]
        lib.tiro_session_finish.argtypes = [c.c_void_p, c.POINTER(c.c_void_p)]
        lib.tiro_session_free.argtypes = [c.c_void_p]
        lib.tiro_free.argtypes = [c.c_void_p]
        lib.tiro_process_peak_rss.restype = c.c_int64
        lib.tiro_process_rss.restype = c.c_int64
        self.lib = lib
        self.version = lib.tiro_version().decode()

    @classmethod
    def get(cls) -> _Lib:
        with cls._lock:
            if cls._inst is None:
                cls._inst = _Lib()
            return cls._inst


def available() -> bool:
    try:
        _Lib.get()
        return True
    except OSError as exc:
        log.info("Lite engine not available: %s", exc)
        return False


def _take(lib, ptr: ctypes.c_void_p) -> str:
    if not ptr.value:
        return ""
    s = ctypes.string_at(ptr.value).decode("utf-8", "replace")
    lib.tiro_free(ptr)
    return s


# ---------------------------------------------------------------------------------------------------- rungs
def rungs() -> list[dict]:
    """The measured ladder (assets/lite/rungs.json): most accurate first."""
    try:
        data = json.loads(asset("lite", "rungs.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return sorted(data.get("rungs", []), key=lambda r: r.get("wer", 99))


def rung_dir(rung_id: str) -> Path:
    return user_models_dir() / "lite" / rung_id


def bundled_rung_dir(rung_id: str) -> Path:
    return bundle_dir() / "models" / "lite" / rung_id


def installed(rung: dict) -> Path | None:
    """Folder holding a complete copy of this rung (downloaded or bundled with the installer), or None."""
    for folder in (rung_dir(rung["id"]), bundled_rung_dir(rung["id"])):
        if all((folder / f["name"]).is_file() for f in rung.get("files", [])):
            return folder
    return None


# ---------------------------------------------------------------------------------------------------- engine
class LiteModel:
    """One loaded rung. Thread-safe for one live session at a time (the app never runs two)."""

    def __init__(self, folder: Path, threads: int = 0, vad: Path | None = None):
        self._l = _Lib.get().lib
        err = ctypes.c_void_p()
        opts = {"threads": int(threads), "vad": str(vad or asset("vad", "silero_vad.onnx"))}
        self._h = self._l.tiro_model_load(str(folder).encode("utf-8"), json.dumps(opts).encode(), ctypes.byref(err))
        if not self._h:
            raise RuntimeError(_take(self._l, err) or f"could not load the Lite model in {folder}")
        self.folder = folder
        self.threads = threads

    def set_vocabulary(self, terms: list[str], rules: list[tuple[str, str]], boost: float = 0.0) -> None:
        payload = {"terms": terms, "rules": [{"heard": h, "written": w} for h, w in rules],
                   "common_words_file": str(asset("words", "common-words-en.txt"))}
        if boost:
            payload["boost"] = boost
        self._l.tiro_model_set_vocabulary(self._h, json.dumps(payload).encode("utf-8"))

    def transcribe(self, audio: np.ndarray) -> dict:
        a = np.ascontiguousarray(audio, dtype=np.float32)
        out = ctypes.c_void_p()
        self._l.tiro_model_transcribe(self._h, a.ctypes.data_as(ctypes.POINTER(ctypes.c_float)), len(a),
                                      ctypes.byref(out))
        return json.loads(_take(self._l, out) or "{}")

    def session(self, **options) -> LiteSession:
        return LiteSession(self, options)

    def close(self) -> None:
        if self._h:
            self._l.tiro_model_free(self._h)
            self._h = None

    def __del__(self):
        with contextlib.suppress(Exception):
            self.close()


class LiteSession:
    def __init__(self, model: LiteModel, options: dict):
        self._l = model._l
        self._h = self._l.tiro_session_begin(model._h, json.dumps(options).encode("utf-8"))

    def feed(self, audio: np.ndarray) -> None:
        a = np.ascontiguousarray(audio, dtype=np.float32)
        self._l.tiro_session_feed(self._h, a.ctypes.data_as(ctypes.POINTER(ctypes.c_float)), len(a))

    def poll(self) -> list[dict]:
        out = ctypes.c_void_p()
        self._l.tiro_session_poll(self._h, ctypes.byref(out))
        return json.loads(_take(self._l, out) or "[]")

    def finish(self) -> dict:
        out = ctypes.c_void_p()
        self._l.tiro_session_finish(self._h, ctypes.byref(out))
        return json.loads(_take(self._l, out) or "{}")

    def close(self) -> None:
        if self._h:
            self._l.tiro_session_free(self._h)
            self._h = None

    def __del__(self):
        with contextlib.suppress(Exception):
            self.close()


def process_rss_mb() -> float:
    return _Lib.get().lib.tiro_process_rss() / 2**20
