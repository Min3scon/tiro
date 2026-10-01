"""Build the README / website images into docs/images/ from the real UI code (offscreen).

  hero.png       a dictation in progress: an editor with text arriving and the Tiro overlay
  overlay.gif    the overlay during a dictation (animated)
  plus curated screenshots copied from the dev renders (settings, setup wizard, installer).
"""

import math
import os
import shutil
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "images"
OUT.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(ROOT))

from PIL import Image  # noqa: E402
from PySide6.QtCore import QRectF, Qt  # noqa: E402
from PySide6.QtGui import QColor, QImage, QLinearGradient, QPainter, QPainterPath, QRadialGradient  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication(sys.argv)
from tiro.ui import theme  # noqa: E402

theme.load_fonts()
from tiro.ui import overlay as ov  # noqa: E402

level = {"v": 0.0}
o = ov.Overlay(lambda: level["v"])
o._appear = lambda: None


def to_pil(img: QImage) -> Image.Image:
    img = img.convertToFormat(QImage.Format.Format_RGBA8888)
    return Image.frombuffer("RGBA", (img.width(), img.height()), bytes(img.constBits()), "raw", "RGBA", 0, 1).copy()


def backdrop(w: int, h: int) -> QImage:
    img = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    g = QLinearGradient(0, 0, w, h)
    g.setColorAt(0, QColor("#1B1830"))
    g.setColorAt(1, QColor("#0E0D16"))
    p.fillRect(0, 0, w, h, g)
    for cx, cy, r, col in ((w * 0.85, h * 0.1, w * 0.5, "#40E6508E"), (w * 0.1, h * 0.95, w * 0.45, "#338B5CF6")):
        rg = QRadialGradient(cx, cy, r)
        rg.setColorAt(0, QColor(col))
        rg.setColorAt(1, QColor(0, 0, 0, 0))
        p.fillRect(0, 0, w, h, rg)
    p.end()
    return img


def editor(p: QPainter, rect: QRectF, text: str) -> None:
    path = QPainterPath()
    path.addRoundedRect(rect, 14, 14)
    p.fillPath(path, QColor("#F7F5F0"))
    bar = QPainterPath()
    bar.addRoundedRect(QRectF(rect.left(), rect.top(), rect.width(), 40), 14, 14)
    p.fillPath(bar, QColor("#E9E6DF"))
    p.fillRect(QRectF(rect.left(), rect.top() + 26, rect.width(), 14), QColor("#E9E6DF"))
    for i, c in enumerate(("#FF5F57", "#FEBC2E", "#28C840")):
        p.setBrush(QColor(c))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(QRectF(rect.left() + 18 + i * 20, rect.top() + 14, 12, 12))
    p.setPen(QColor("#2A2733"))
    f = theme.font(22)
    p.setFont(f)
    p.drawText(QRectF(rect.left() + 44, rect.top() + 72, rect.width() - 88, rect.height() - 100),
               int(Qt.TextFlag.TextWordWrap), text)


def pill(words_done: str, words_pending: str, lvl: float, frames: int = 30) -> QImage:
    o.state = "listening"
    o.set_text(words_done, words_pending)
    level["v"] = lvl
    o._presence_target = 1.0
    for _ in range(frames):
        o._tick()
        o._presence = 1.0
    return o.grab().toImage()


# ---------------------------------------------------------------- hero
W, H = 1600, 900
hero = backdrop(W, H)
p = QPainter(hero)
p.setRenderHint(QPainter.RenderHint.Antialiasing)
p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
typed = "Quick update for the team: the Kubernetes migration is done, and Siobhan is sharing the"
editor(p, QRectF(250, 150, 1100, 360), "")
p.setFont(theme.font(28))
p.setPen(QColor("#2A2733"))
box = QRectF(300, 230, 1000, 240)
p.drawText(box, int(Qt.TextFlag.TextWordWrap), typed)
# caret right after the typed text
fm = p.fontMetrics()
lines, line = [], ""
for word in typed.split():
    trial = (line + " " + word).strip()
    if fm.horizontalAdvance(trial) > box.width():
        lines.append(line)
        line = word
    else:
        line = trial
lines.append(line)
cx = box.left() + fm.horizontalAdvance(lines[-1]) + 4
cy = box.top() + (len(lines) - 1) * fm.lineSpacing()
p.fillRect(QRectF(cx, cy + 4, 2.5, fm.height() - 6), QColor(theme.ROSE))
o.begin()
img = pill("the Kubernetes migration is done, and Siobhan is sharing the", "GeoGuessr tournament", 0.75, 60)
p.save()
scale = 1.6
p.translate(W / 2, 640)
p.scale(scale, scale)
p.drawImage(int(-img.width() / 2), 0, img)
p.restore()
p.end()
hero.save(str(OUT / "hero.png"))

# ---------------------------------------------------------------- overlay animation
script = "Hey, can you send me the report by Friday? I want to go over the numbers with Siobhan before our meeting.".split()
frames = []
o.begin()
total = 70
BW, BH = ov.CANVAS_W + 80, ov.CANVAS_H + 60
bg = backdrop(BW, BH)
for i in range(total):
    n = min(len(script), max(0, int(i * len(script) / (total - 14))))
    done = " ".join(script[: max(0, n - 3)])
    pending = " ".join(script[max(0, n - 3): n])
    lvl = 0.25 + 0.55 * abs(math.sin(i * 0.55)) * (1.0 if i < total - 10 else 0.2)
    if i >= total - 8:
        done, pending = " ".join(script), ""
    o.state = "listening"
    o.set_text(done, pending)
    level["v"] = lvl
    for _ in range(3):
        o._tick()
    o._presence = 1.0
    frame = QImage(bg)
    fp = QPainter(frame)
    fp.drawImage(40, 30, o.grab().toImage())
    fp.end()
    frames.append(to_pil(frame).convert("RGB").quantize(colors=255, method=Image.Quantize.MEDIANCUT))
frames[0].save(OUT / "overlay.gif", save_all=True, append_images=frames[1:], duration=70, loop=0, optimize=True)

# ---------------------------------------------------------------- curated screenshots
picks = {
    "dev/ui_settings_accuracy.png": "settings-accuracy.png",
    "dev/ui_settings_dictionary.png": "settings-dictionary.png",
    "dev/ui_settings_privacy.png": "settings-privacy.png",
    "dev/ui_settings_general.png": "settings-general.png",
    "dev/flow_01_computer.png": "setup-computer.png",
    "dev/flow_03_bench.png": "setup-speed-check.png",
    "dev/flow_08_test.png": "setup-try-it.png",
    "dev/ui_fixlast.png": "fix-last.png",
    "dev/installer_shots/setup-1-welcome.png": "installer-welcome.png",
    "dev/installer_shots/setup-2-choose.png": "installer-choose.png",
    "dev/installer_shots/setup-3-install.png": "installer-install.png",
    "dev/installer_shots/setup-4-done.png": "installer-done.png",
}
for src, dst in picks.items():
    if (ROOT / src).is_file():
        shutil.copy(ROOT / src, OUT / dst)
print("images in", OUT, sorted(x.name for x in OUT.iterdir()))
