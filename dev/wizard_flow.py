"""Drive the real setup wizard end to end, offscreen: real app, real models, real benchmark.

Steps through every page, waits where the wizard waits (downloads, speed check), saves a screenshot of each
page to dev/flow_<n>_<page>.png and prints what the speed check concluded.
"""
import os
import sys
import time
from pathlib import Path

os.environ["QT_QPA_PLATFORM"] = "offscreen"
ROOT = Path(__file__).resolve().parents[1]
profile = ROOT / "dev" / "wizard_profile"
profile.mkdir(exist_ok=True)
(profile / "settings.json").unlink(missing_ok=True)
os.environ["TIRO_CONFIG_DIR"] = str(profile)
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

qapp = QApplication(sys.argv)
qapp.setQuitOnLastWindowClosed(False)
from tiro.ui import theme  # noqa: E402

theme.load_fonts()
from tiro.app import TiroApp  # noqa: E402

app = TiroApp(qapp, setup=True)
app.update_setting("hotkey", "f24")
app.show_setup()
w = app.setup_window
shots = []
t_start = time.monotonic()


def snap():
    key = w.key
    path = ROOT / "dev" / f"flow_{w.index:02d}_{key}.png"
    w.grab().save(str(path))
    shots.append(key)


def tick():
    if time.monotonic() - t_start > 420:
        print("TIMEOUT on page", w.key)
        qapp.quit()
        return
    if not w.isVisible():
        print("wizard closed; setup_done =", app.settings.setup_done)
        qapp.quit()
        return
    if w.key not in shots:
        snap()
    if w.next.isEnabled():
        if w.key == "bench":
            print("speed check:", {k: v.text() for k, v in w.bench_rows.items()}, "|", w.bench_note.text())
            snap()
        if w.key == "test":
            w.try_box.setPlainText("Tiro is set up and ready to go.")
            qapp.processEvents()
            print("test page note:", w.test_note.text())
        w._next()
    QTimer.singleShot(700, tick)


QTimer.singleShot(1500, tick)
qapp.exec()
print("pages:", shots, f"({time.monotonic() - t_start:.0f} s)")
app.quit()
os._exit(0)
