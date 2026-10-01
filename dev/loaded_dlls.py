"""List the NVIDIA/ORT DLLs a CUDA transcription actually loads (to trim the bundle)."""

import ctypes
import os
import sys
from ctypes import wintypes
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tiro.asr import ParakeetEngine  # noqa: E402

eng = ParakeetEngine(ROOT / "models" / "parakeet-tdt-0.6b-v2", device="cuda")
eng.load()
eng.transcribe(np.random.randn(16000 * 12).astype(np.float32) * 0.01)
eng.transcribe(np.random.randn(16000 * 25).astype(np.float32) * 0.01)

psapi = ctypes.WinDLL("psapi")
k32 = ctypes.WinDLL("kernel32")
k32.GetCurrentProcess.restype = wintypes.HANDLE
psapi.EnumProcessModules.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.HMODULE), wintypes.DWORD,
                                     ctypes.POINTER(wintypes.DWORD)]
psapi.GetModuleFileNameExW.argtypes = [wintypes.HANDLE, wintypes.HMODULE, wintypes.LPWSTR, wintypes.DWORD]
mods = (wintypes.HMODULE * 2048)()
needed = wintypes.DWORD()
psapi.EnumProcessModules(k32.GetCurrentProcess(), mods, ctypes.sizeof(mods), ctypes.byref(needed))
names = []
for i in range(needed.value // ctypes.sizeof(wintypes.HMODULE)):
    buf = ctypes.create_unicode_buffer(1024)
    psapi.GetModuleFileNameExW(k32.GetCurrentProcess(), wintypes.HMODULE(mods[i]), buf, 1024)
    names.append(buf.value)
for n in sorted(names):
    low = n.lower()
    if any(k in low for k in ("nvidia", "cuda", "cudnn", "cublas", "cufft", "curand", "nvrtc", "nvjit", "onnxruntime")):
        print(f"{os.path.getsize(n) / 2**20:8.1f} MB  {n}")
