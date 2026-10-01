"""What computer is this, and what's the best way to run Tiro on it? Explained in plain English."""

from __future__ import annotations

import ctypes
import logging
import os
import platform
import subprocess
import sys
from dataclasses import dataclass, field

from tiro import gpu

log = logging.getLogger(__name__)
IS_MAC = sys.platform == "darwin"


@dataclass
class Hardware:
    os_name: str
    cpu: str
    cores: int
    ram_gb: float
    gpu: gpu.GpuInfo | None = None  # NVIDIA, via the driver
    apple_chip: str | None = None  # "Apple M2 Pro"
    arm: bool = False

    @property
    def summary(self) -> list[tuple[str, str]]:
        rows = [("System", self.os_name), ("Processor", f"{self.cpu} · {self.cores} cores"),
                ("Memory", f"{self.ram_gb:.0f} GB")]
        if self.apple_chip:
            rows.append(("Graphics", f"{self.apple_chip} GPU and Neural Engine"))
        elif self.gpu is not None:
            rows.append(("Graphics", f"{self.gpu.name} · {self.gpu.memory_gb:.0f} GB"))
        else:
            rows.append(("Graphics", "No NVIDIA graphics card found"))
        return rows


@dataclass
class Plan:
    device: str  # cuda | coreml | cpu  (speech recognition)
    speech_variant: str  # fp32 | int8
    ai_model: str  # small | tiny | off
    headline: str
    reasons: list[str] = field(default_factory=list)
    download_gb: float = 0.0


def _ram_gb() -> float:
    if IS_MAC:
        try:
            return int(subprocess.check_output(["sysctl", "-n", "hw.memsize"], text=True)) / 2**30
        except Exception:
            return 8.0

    class MEMORYSTATUSEX(ctypes.Structure):
        _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]

    st = MEMORYSTATUSEX(dwLength=ctypes.sizeof(MEMORYSTATUSEX))
    ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st))
    return st.ullTotalPhys / 2**30


def _cpu_name() -> str:
    if IS_MAC:
        try:
            return subprocess.check_output(["sysctl", "-n", "machdep.cpu.brand_string"], text=True).strip()
        except Exception:
            return platform.processor() or "Apple Silicon"
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0") as k:
            return " ".join(str(winreg.QueryValueEx(k, "ProcessorNameString")[0]).split())
    except OSError:
        return platform.processor() or "Unknown processor"


def _os_name() -> str:
    if IS_MAC:
        return f"macOS {platform.mac_ver()[0]}"
    ver = sys.getwindowsversion()
    return f"Windows {'11' if ver.build >= 22000 else '10'} (build {ver.build})"


def detect() -> Hardware:
    cpu = _cpu_name()
    hw = Hardware(os_name=_os_name(), cpu=cpu, cores=os.cpu_count() or 4, ram_gb=_ram_gb(),
                  arm=platform.machine().lower() in ("arm64", "aarch64"))
    if IS_MAC:
        hw.apple_chip = cpu if "Apple" in cpu else None
    else:
        hw.gpu = gpu.detect_nvidia_gpu()
    return hw


def recommend(hw: Hardware) -> Plan:
    """Pick the backend and models, and say why."""
    if hw.apple_chip:
        ai = "small" if hw.ram_gb >= 8 else "tiny"
        return Plan("coreml", "fp32", ai, f"Your {hw.apple_chip} will run Tiro on its GPU and Neural Engine.",
                    ["Speech recognition runs through Core ML, on the Mac's own graphics and Neural Engine.",
                     f"The AI check uses the {'1.5B' if ai == 'small' else '0.5B'} model on the GPU with Apple's MLX.",
                     "Everything runs on your Mac, without an internet connection once it's downloaded."], 3.3)
    g = hw.gpu
    if g is not None and g.usable and g.memory_gb >= 3.5:
        ai = "small" if g.memory_gb >= 6 else "tiny"
        return Plan("cuda", "fp32", ai, f"Your {g.name} will do the heavy lifting.",
                    ["Speech recognition runs on the graphics card (CUDA), which is roughly ten times faster than "
                     "the processor and keeps your computer responsive.",
                     f"With {g.memory_gb:.0f} GB of graphics memory there's room for the "
                     f"{'larger, smarter' if ai == 'small' else 'smaller'} AI check alongside it.",
                     "Tiro downloads NVIDIA's GPU libraries (about 1.2 GB) once."], 2.4 + 1.2 + (1.23 if ai == "small" else 0.49))
    reasons = []
    if g is not None and not g.usable:
        reasons.append(f"Your {g.name} can't be used: {g.problem}.")
    elif g is not None:
        reasons.append(f"Your {g.name} doesn't have enough memory for the speech model, so the processor runs it.")
    else:
        reasons.append("There's no NVIDIA graphics card, so the processor runs speech recognition.")
    fast_cpu = hw.cores >= 8
    reasons.append("Tiro uses the compact edition of the speech model: same accuracy, built for processors.")
    reasons.append("The AI check uses the small 0.5B model" + ("." if fast_cpu else
                   "; on this processor it may be too slow, in which case it's switched off."))
    return Plan("cpu", "int8", "tiny", f"Your {hw.cpu.split('@')[0].strip()} will run Tiro.", reasons, 0.63 + 0.79)
