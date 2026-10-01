# PyInstaller spec for Tiro: one-folder, windowed (no console), custom icon + version resource.
# Build with tools\build.ps1 (it also copies the speech models next to Tiro.exe).

import os
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, copy_metadata

ROOT = Path(SPECPATH)
NO_CUDA = os.environ.get("TIRO_BUILD_NO_CUDA") == "1"  # small CPU-only builds for the update tests
SITE = ROOT / ".venv" / "Lib" / "site-packages"
CU13 = SITE / "nvidia" / "cu13" / "bin" / "x86_64"
CUDNN = SITE / "nvidia" / "cudnn" / "bin"

# CUDA 13 runtime pieces that onnxruntime's CUDA provider actually loads (checked with dev/loaded_dlls.py),
# plus NVRTC for cuDNN's runtime-compiled kernels. All go flat into _internal/cuda.
cuda = [(str(CU13 / name), "cuda") for name in (
    "cudart64_13.dll", "cublas64_13.dll", "cublasLt64_13.dll", "cufft64_12.dll",
    "nvrtc64_130_0.dll", "nvrtc-builtins64_134.dll",
)]
cuda += [(str(p), "cuda") for p in sorted(CUDNN.glob("cudnn*64_9.dll"))]
if NO_CUDA:
    cuda = []

native = ROOT / "build" / "native" / "tiro_hook.dll"  # tools\build_native.ps1 (global hotkey on a native thread)
if not native.is_file():
    raise SystemExit("build the native hook first: tools\\build_native.ps1")
native_bins = [(str(native), ".")]

datas = [(str(ROOT / "assets"), "assets"), (str(ROOT / "CHANGELOG.md"), "assets")]
datas += collect_data_files("onnx_asr")  # preprocessor graphs + filterbanks
datas += copy_metadata("onnx-asr") + copy_metadata("onnxruntime-gpu")

a = Analysis(
    [str(ROOT / "run_tiro.pyw")],
    pathex=[str(ROOT)],
    binaries=cuda + native_bins,
    datas=datas,
    hiddenimports=["PySide6.QtNetwork", "tokenizers", "rapidfuzz.process", "rapidfuzz.distance.Levenshtein",
                   "tiro.platform.windows.winutil", "tiro.platform.windows.injector", "tiro.platform.windows.secure",
                   "tiro.platform.windows.autostart", "tiro.platform.windows.sounds", "tiro.platform.windows.hook",
                   "tiro.platform.windows.native_hook"],
    excludes=[
        "tkinter", "pytest", "jiwer", "pyarrow", "huggingface_hub", "hf_xet", "httpx2", "nvidia", "tiro.platform.mac",
        "mlx", "mlx_lm", "transformers", "torch", "objc", "Quartz", "AppKit",
        "IPython", "matplotlib", "pandas", "scipy", "PIL", "sympy",
        "PySide6.QtQml", "PySide6.QtQuick", "PySide6.QtPdf", "PySide6.QtOpenGL", "PySide6.QtSql",
        "PySide6.QtTest", "PySide6.QtXml", "PySide6.QtDBus", "PySide6.QtConcurrent", "PySide6.QtHelp",
        "PySide6.QtPrintSupport", "PySide6.QtOpenGLWidgets", "PySide6.QtSvg", "PySide6.QtDesigner",
    ],
    noarchive=False,
)
# PyInstaller's dependency scan also finds the CUDA DLLs in site-packages\nvidia and in any CUDA toolkit on PATH.
# Keep only the copies in cuda\ (the ones tiro.gpu preloads) and drop Qt's unused software OpenGL renderer.
_cuda_names = {Path(src).name.lower() for src, _dest in cuda}


def _keep(entry):
    dest = entry[0].replace("\\", "/").lower()
    name = dest.rsplit("/", 1)[-1]
    if dest.startswith("nvidia/") or name == "opengl32sw.dll":
        return False
    if NO_CUDA and name.startswith(("onnxruntime_providers_cuda", "onnxruntime_providers_tensorrt")):
        return False
    return not (name in _cuda_names and not dest.startswith("cuda/"))


a.binaries = [b for b in a.binaries if _keep(b)]
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Tiro",
    icon=str(ROOT / "assets" / "tiro.ico"),
    version=str(ROOT / "tools" / "version_info.txt"),
    console=False,
    upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name="Tiro", upx=False)
