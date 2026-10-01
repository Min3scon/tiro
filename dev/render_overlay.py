"""Render the overlay HUD in its different states into dev/overlay_preview.png (no window is shown)."""

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PySide6.QtGui import QColor, QImage, QLinearGradient, QPainter  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication(sys.argv)
from tiro.ui import theme  # noqa: E402

theme.load_fonts()
from tiro.ui import overlay as ov  # noqa: E402

level = {"v": 0.0}
o = ov.Overlay(lambda: level["v"])
o._appear = lambda: None  # don't put a window on screen


def settle(frames=40, lvl=None, advance=0.0):
    o._presence_target = 1.0
    if lvl is not None:
        level["v"] = lvl
    for _ in range(frames):
        o._tick()
        o._presence = 1.0
    for w in o._words:
        w.alpha = 1.0
        w.born -= 1.0


shots = []


def shot(label):
    img = o.grab().toImage()
    shots.append((label, img))


o.state = "listening"
settle(lvl=0.7)
shot("listening (no words yet)")

o.begin()
o.state = "listening"
o.set_text("Hey, can you send me", "the report by")
settle(lvl=0.55)
shot("live words (white = typed, soft = still forming)")

o.set_text("Hey, can you send me the report by Friday? I want to go over the numbers with the team before",
           "our meeting on")
settle(lvl=0.8, frames=80)
shot("long dictation (scrolls, fades at the left edge)")

o.locked = True
settle(lvl=0.3)
shot("hands-free (double-tap) indicator")
o.locked = False

o._words.clear()
o.state = "loading"
settle()
shot("model still loading")

o.begin()
o.set_text("Thanks, see you tomorrow.", "")
o.state = "finishing"
settle(frames=40)
shot("finishing (after key release)")

o.notify("Tiro is ready — hold Right Ctrl and speak", "ok")
o._hide_at = None
settle()
shot("notice")

o.notify("Could not open microphone: device unavailable", "error")
o._hide_at = None
settle()
shot("error notice")

W = ov.CANVAS_W + 40
H = sum(img.height() + 26 for _, img in shots) + 20
sheet = QImage(W, H, QImage.Format.Format_ARGB32_Premultiplied)
p = QPainter(sheet)
bg = QLinearGradient(0, 0, W, H)
bg.setColorAt(0, QColor("#5B6B82"))
bg.setColorAt(1, QColor("#C9CED6"))
p.fillRect(0, 0, W, H, bg)
y = 10
p.setFont(theme.font(12))
for label, img in shots:
    p.setPen(QColor("#FFFFFF"))
    p.drawText(20, y + 14, label)
    p.drawImage(20, y + 18, img)
    y += img.height() + 26
p.end()
out = ROOT / "dev" / "overlay_preview.png"
sheet.save(str(out))
print("saved", out)
