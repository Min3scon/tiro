"""Grab only the overlay's screen region a few seconds into a dictation (run alongside e2e_notepad.py)."""

import sys
import time
from pathlib import Path

from PySide6.QtWidgets import QApplication

ROOT = Path(__file__).resolve().parents[1]
delays = [float(x) for x in sys.argv[1:]] or [5.0]
app = QApplication(sys.argv[:1])
screen = app.primaryScreen()
geo = screen.availableGeometry()
w, h = 620, 110
x = geo.center().x() - w // 2
y = geo.bottom() - h - 34
t0 = time.monotonic()
for i, d in enumerate(delays):
    time.sleep(max(0.0, t0 + d - time.monotonic()))
    screen.grabWindow(0, x, y, w, h).save(str(ROOT / "dev" / f"overlay_live_{i}.png"))
print("captured", len(delays))
