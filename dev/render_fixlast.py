"""Render the 'Fix last transcription' window offscreen into dev/ui_fixlast.png."""
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from PySide6.QtWidgets import QApplication  # noqa: E402

qapp = QApplication(sys.argv)
from tiro.ui import theme  # noqa: E402

theme.load_fonts()


class _Ctx:
    events = 5


class _Sess:
    text = " I played George asser last night and nailed the map."
    inject_hwnd = 0
    last_inject_events = 5
    app_name = "notepad"


class _App:
    context = _Ctx()


from tiro.ui.fixlast import FixLastWindow  # noqa: E402

w = FixLastWindow(_App(), _Sess())
w.resize(560, 300)
w.show()
qapp.processEvents()
w.grab().save(str(ROOT / "dev" / "ui_fixlast.png"))
print("ok")
