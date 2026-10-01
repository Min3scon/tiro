"""ctypes binding to the native Tiro engine (core/), so evaluations score exactly the code the apps run."""
from __future__ import annotations

import ctypes
import json
import os
import sys
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[2]


def _lib_path() -> Path:
    env = os.environ.get("TIRO_CORE_LIB")
    if env:
        return Path(env)
    if sys.platform == "win32":
        return REPO / "build" / "core-win-x64" / "libtiro_core.dll"
    if sys.platform == "darwin":
        return REPO / "build" / "core-mac" / "libtiro_core.dylib"
    return REPO / "build" / "core-linux" / "libtiro_core.so"


class _Lib:
    _inst = None

    def __init__(self):
        path = _lib_path()
        if sys.platform == "win32":
            os.add_dll_directory(str(path.parent))
        lib = ctypes.CDLL(str(path))
        c = ctypes
        lib.tiro_model_load.restype = c.c_void_p
        lib.tiro_model_load.argtypes = [c.c_char_p, c.c_char_p, c.POINTER(c.c_void_p)]
        lib.tiro_model_free.argtypes = [c.c_void_p]
        lib.tiro_model_transcribe.argtypes = [c.c_void_p, c.POINTER(c.c_float), c.c_size_t, c.POINTER(c.c_void_p)]
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

    @classmethod
    def get(cls) -> "_Lib":
        if cls._inst is None:
            cls._inst = _Lib()
        return cls._inst


def _take_string(lib, ptr: ctypes.c_void_p) -> str:
    if not ptr.value:
        return ""
    s = ctypes.string_at(ptr.value).decode("utf-8", "replace")
    lib.tiro_free(ptr)
    return s


class Model:
    def __init__(self, model_dir: str | Path, **options):
        self._l = _Lib.get().lib
        err = ctypes.c_void_p()
        self._h = self._l.tiro_model_load(str(model_dir).encode(), json.dumps(options).encode(), ctypes.byref(err))
        if not self._h:
            raise RuntimeError(_take_string(self._l, err) or "tiro_model_load failed")

    def transcribe(self, audio: np.ndarray) -> dict:
        a = np.ascontiguousarray(audio, dtype=np.float32)
        out = ctypes.c_void_p()
        self._l.tiro_model_transcribe(self._h, a.ctypes.data_as(ctypes.POINTER(ctypes.c_float)), len(a),
                                      ctypes.byref(out))
        return json.loads(_take_string(self._l, out) or "{}")

    def session(self, **options) -> "Session":
        return Session(self, options)

    def close(self):
        if self._h:
            self._l.tiro_model_free(self._h)
            self._h = None

    def __del__(self):
        self.close()


class Session:
    def __init__(self, model: Model, options: dict):
        self._m = model
        self._l = model._l
        self._h = self._l.tiro_session_begin(model._h, json.dumps(options).encode())

    def feed(self, audio: np.ndarray) -> None:
        a = np.ascontiguousarray(audio, dtype=np.float32)
        self._l.tiro_session_feed(self._h, a.ctypes.data_as(ctypes.POINTER(ctypes.c_float)), len(a))

    def poll(self) -> list[dict]:
        out = ctypes.c_void_p()
        self._l.tiro_session_poll(self._h, ctypes.byref(out))
        return json.loads(_take_string(self._l, out) or "[]")

    def finish(self) -> dict:
        out = ctypes.c_void_p()
        self._l.tiro_session_finish(self._h, ctypes.byref(out))
        return json.loads(_take_string(self._l, out) or "{}")

    def close(self):
        if self._h:
            self._l.tiro_session_free(self._h)
            self._h = None

    def __del__(self):
        self.close()


def peak_rss() -> int:
    return int(_Lib.get().lib.tiro_process_peak_rss())


def rss() -> int:
    return int(_Lib.get().lib.tiro_process_rss())
