# PyInstaller spec for Tiro on macOS (Apple Silicon): Tiro.app, a menu-bar app (no Dock icon).
# Built by tools/build_mac.sh (on GitHub Actions, macos-14 or newer, arm64).

from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_data_files, copy_metadata

ROOT = Path(SPECPATH)
from tiro import __version__  # noqa: E402

datas = [(str(ROOT / "assets"), "assets")]
datas += collect_data_files("onnx_asr")
datas += copy_metadata("onnx-asr") + copy_metadata("onnxruntime")
binaries, hidden = [], []
for pkg in ("mlx", "mlx_lm"):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hidden += h

a = Analysis(
    [str(ROOT / "run_tiro.pyw")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hidden + [
        "PySide6.QtNetwork", "tokenizers", "rapidfuzz.process", "rapidfuzz.distance.Levenshtein",
        "objc", "Quartz", "AppKit", "Foundation", "ApplicationServices", "AVFoundation", "ServiceManagement",
        "tiro.platform.mac.winutil", "tiro.platform.mac.injector", "tiro.platform.mac.secure",
        "tiro.platform.mac.autostart", "tiro.platform.mac.sounds", "tiro.platform.mac.hook",
        "tiro.platform.mac.permissions", "tiro.correct.llm_mlx",
    ],
    excludes=[
        "tkinter", "pytest", "jiwer", "pyarrow", "tiro.platform.windows", "IPython", "matplotlib", "pandas",
        "scipy", "PIL", "sympy", "torch", "PySide6.QtQml", "PySide6.QtQuick", "PySide6.QtPdf", "PySide6.QtSql",
        "PySide6.QtTest", "PySide6.QtXml", "PySide6.QtConcurrent", "PySide6.QtHelp", "PySide6.QtDesigner",
    ],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="Tiro", console=False, upx=False,
          target_arch="arm64", codesign_identity=None)
coll = COLLECT(exe, a.binaries, a.datas, name="Tiro", upx=False)
app = BUNDLE(
    coll,
    name="Tiro.app",
    icon=str(ROOT / "assets" / "tiro.icns"),
    bundle_identifier="app.tiro.dictation",
    version=__version__,
    info_plist={
        "CFBundleName": "Tiro",
        "CFBundleDisplayName": "Tiro",
        "CFBundleShortVersionString": __version__,
        "CFBundleVersion": __version__,
        "LSUIElement": True,  # menu-bar app: no Dock icon, no app switcher entry
        "LSMinimumSystemVersion": "13.0",
        "LSArchitecturePriority": ["arm64"],
        "NSHighResolutionCapable": True,
        "NSMicrophoneUsageDescription": "Tiro listens while you hold your dictation key, and turns your speech "
                                        "into text on this Mac. Audio never leaves your computer.",
        "NSHumanReadableCopyright": "Tiro is open source (MIT).",
    },
)
