"""Render the settings window and tray menu offscreen into dev/ui_*.png for visual review."""

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
from tiro.config import Settings  # noqa: E402


class StubApp:
    def __init__(self):
        self.settings = Settings()
        self.last_text = "Hello there."

    def status_line(self):
        return "Ready · hold Right Ctrl"

    def engine_description(self):
        return "Parakeet TDT 0.6B v2 running on NVIDIA GeForce RTX 3070 (CUDA)."

    def autostart_enabled(self):
        return True

    def set_autostart(self, on):
        pass

    def update_setting(self, k, v):
        setattr(self.settings, k, v)

    def capture_hotkey(self, cb):
        pass

    def list_mics(self):
        return ["USB Audio Interface (Inputs 1+2)", "Headset Microphone"]

    def default_mic_name(self):
        return "USB Audio Interface (Inputs 1+2)"

    def open_logs(self):
        pass

    def restart_as_admin(self):
        pass

    def copy_last(self):
        pass

    def show_settings(self):
        pass

    def quit(self):
        pass


from tiro.ui.settings import SettingsWindow  # noqa: E402

stub = StubApp()
w = SettingsWindow(stub)
w.resize(580, 1180)
w.show()
qapp.processEvents()
w.grab().save(str(ROOT / "dev" / "ui_settings.png"))

# tray menu: reuse the real Tray menu construction without a system tray
from PySide6.QtGui import QAction, QActionGroup  # noqa: E402
from PySide6.QtWidgets import QWidgetAction  # noqa: E402

from tiro.ui.tray import _Header, styled_menu  # noqa: E402

menu = styled_menu()
header = _Header()
header.status.setText(stub.status_line())
wa = QWidgetAction(menu)
wa.setDefaultWidget(header)
menu.addAction(wa)
menu.addSeparator()
menu.addAction("Copy last dictation")
menu.addSeparator()
g = QActionGroup(menu)
for label, checked in (("Hold to talk", True), ("Press to toggle", False)):
    a = QAction(label, menu, checkable=True)
    a.setChecked(checked)
    g.addAction(a)
    menu.addAction(a)
sub = styled_menu(menu, "Microphone")
menu.addMenu(sub)
a = QAction("Start with Windows", menu, checkable=True)
a.setChecked(True)
menu.addAction(a)
menu.addAction("Settings…")
menu.addSeparator()
menu.addAction("Quit Tiro")
menu.popup(qapp.primaryScreen().geometry().center())
qapp.processEvents()
menu.grab().save(str(ROOT / "dev" / "ui_menu.png"))
print("saved")
