"""NVIDIA GPU detection and CUDA/cuDNN runtime preparation for ONNX Runtime."""

from __future__ import annotations

import ctypes
import logging
import os
from dataclasses import dataclass

from tiro.paths import FROZEN, bundle_dir

log = logging.getLogger(__name__)

# CUDA 13 (which onnxruntime-gpu 1.30 is built against) needs a Turing-or-newer GPU and an R580+ driver.
MIN_COMPUTE_CAPABILITY = (7, 5)
MIN_DRIVER_CUDA_VERSION = 13000


@dataclass(frozen=True)
class GpuInfo:
    name: str
    memory_gb: float
    compute_capability: tuple[int, int]
    driver_cuda_version: int  # e.g. 13000 for CUDA 13.0

    @property
    def usable(self) -> bool:
        return (
            self.compute_capability >= MIN_COMPUTE_CAPABILITY and self.driver_cuda_version >= MIN_DRIVER_CUDA_VERSION
        )

    @property
    def problem(self) -> str | None:
        if self.compute_capability < MIN_COMPUTE_CAPABILITY:
            return f"{self.name} is too old for CUDA 13 (needs an RTX 20-series or newer)"
        if self.driver_cuda_version < MIN_DRIVER_CUDA_VERSION:
            return "NVIDIA driver is too old for CUDA 13 (update to driver 580 or newer)"
        return None


def detect_nvidia_gpu() -> GpuInfo | None:
    """Query the first CUDA device through the driver API (nvcuda.dll). Returns None when there is none."""
    if not hasattr(ctypes, "WinDLL"):
        return None  # macOS: no NVIDIA GPUs
    try:
        cuda = ctypes.WinDLL("nvcuda.dll")
    except OSError:
        return None
    try:
        if cuda.cuInit(0) != 0:
            return None
        count = ctypes.c_int(0)
        if cuda.cuDeviceGetCount(ctypes.byref(count)) != 0 or count.value < 1:
            return None
        dev = ctypes.c_int(0)
        cuda.cuDeviceGet(ctypes.byref(dev), 0)
        name = ctypes.create_string_buffer(256)
        cuda.cuDeviceGetName(name, 256, dev)
        mem = ctypes.c_size_t(0)
        cuda.cuDeviceTotalMem_v2(ctypes.byref(mem), dev)
        major, minor = ctypes.c_int(0), ctypes.c_int(0)
        cuda.cuDeviceGetAttribute(ctypes.byref(major), 75, dev)  # CU_DEVICE_ATTRIBUTE_COMPUTE_CAPABILITY_MAJOR
        cuda.cuDeviceGetAttribute(ctypes.byref(minor), 76, dev)  # CU_DEVICE_ATTRIBUTE_COMPUTE_CAPABILITY_MINOR
        drv = ctypes.c_int(0)
        cuda.cuDriverGetVersion(ctypes.byref(drv))
        return GpuInfo(
            name=name.value.decode(errors="replace"),
            memory_gb=round(mem.value / 2**30, 1),
            compute_capability=(major.value, minor.value),
            driver_cuda_version=drv.value,
        )
    except Exception:  # pragma: no cover - driver quirks
        log.exception("GPU detection failed")
        return None


_prepared: bool | None = None


def prepare_cuda_runtime() -> bool:
    """Make the CUDA/cuDNN DLLs loadable by onnxruntime's CUDA provider. Safe to call repeatedly."""
    global _prepared
    if _prepared is not None:
        return _prepared
    import onnxruntime as ort

    if "CUDAExecutionProvider" not in ort.get_available_providers():
        log.info("onnxruntime build has no CUDA provider")
        _prepared = False
        return False
    try:
        if FROZEN:
            cuda_dir = bundle_dir() / "cuda"
            if not cuda_dir.is_dir():
                log.warning("CUDA runtime not installed (%s)", cuda_dir)
                _prepared = False
                return False
            os.add_dll_directory(str(cuda_dir))
            os.environ["PATH"] = str(cuda_dir) + os.pathsep + os.environ.get("PATH", "")
            ort.preload_dlls(directory=str(cuda_dir))
        else:
            ort.preload_dlls(directory="")  # nvidia-* wheels in site-packages
        _prepared = True
    except Exception:
        log.exception("Could not preload CUDA DLLs")
        _prepared = False
    return _prepared


def cuda_runtime_present() -> bool:
    """Are NVIDIA's runtime libraries installed for this copy of Tiro? (Always true when run from source.)"""
    if not FROZEN:
        return True
    capi = bundle_dir() / "onnxruntime" / "capi" / "onnxruntime_providers_cuda.dll"
    return (bundle_dir() / "cuda").is_dir() and capi.is_file()


def download_cuda_runtime(progress=None, cancel=None) -> None:
    """Fetch the GPU runtime pack for this version from the GitHub release, verify it, and unpack it next to
    Tiro.exe (used when Tiro was installed from the plain zip rather than the installer)."""
    import json
    import urllib.request
    import zipfile

    from tiro import GITHUB_REPO, __version__
    from tiro.models import download_file
    from tiro.paths import app_root, data_dir

    global _prepared
    base = f"https://github.com/{GITHUB_REPO}/releases/download/v{__version__}"
    with urllib.request.urlopen(f"{base}/manifest.json", timeout=30) as r:
        info = json.load(r)["gpu"]
    zpath = data_dir() / "downloads" / info["url"].rsplit("/", 1)[-1]
    total = int(info["size"])
    download_file(info["url"], zpath, total, info.get("sha256") or None,
                  (lambda got: progress(got, total)) if progress else None, cancel)
    with zipfile.ZipFile(zpath) as z:
        z.extractall(app_root())
    zpath.unlink(missing_ok=True)
    _prepared = None  # look again next time a model loads
