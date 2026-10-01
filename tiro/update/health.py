"""`Tiro.exe --health OUT.json`: can this build start on this computer? Run on a freshly staged update before
anything is switched over. It loads everything a start needs (Python modules, ONNX Runtime with the bundled voice
detector, Qt, the assets) but touches no settings, hotkeys, microphone or speech model, and opens no window."""
from __future__ import annotations

import json
import traceback

from tiro import __version__

ASSETS = (("vad", "silero_vad.onnx"), ("sounds", "selftest.wav"), ("icons", "chevron.png"))


def run(out: str) -> int:
    result: dict = {"ok": False, "version": __version__}
    try:
        import numpy as np

        from tiro.paths import asset
        from tiro.vad import FRAME, SileroVad

        for parts in ASSETS:
            if not asset(*parts).is_file():
                raise FileNotFoundError("/".join(parts))
        vad = SileroVad(asset("vad", "silero_vad.onnx"))  # ONNX Runtime works and the bundled model runs
        vad(np.zeros(FRAME, dtype=np.float32))
        from PySide6.QtWidgets import QApplication

        import tiro.app  # noqa: F401  (every module the app imports at start)

        qapp = QApplication.instance() or QApplication(["Tiro"])
        qapp.processEvents()
        from tiro.update.feed import TRUSTED_KEYS

        result["keys"] = sorted(TRUSTED_KEYS)  # the release pipeline refuses a build that trusts a test key
        result["ok"] = True
    except Exception as exc:  # noqa: BLE001  (report anything at all)
        result["error"] = f"{type(exc).__name__}: {exc}"
        result["trace"] = traceback.format_exc(limit=6)
    try:
        with open(out, "w", encoding="utf-8") as f:
            json.dump(result, f)
    except OSError:
        return 2
    return 0 if result["ok"] else 1
