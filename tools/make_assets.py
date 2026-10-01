"""Generate Tiro's icon files (.ico + PNG previews) and UI sounds from code.

Usage: python tools/make_assets.py [--preview]
"""

from __future__ import annotations

import io
import math
import struct
import sys
import wave
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, Qt  # noqa: E402
from PySide6.QtGui import QColor, QGuiApplication, QImage, QPainter  # noqa: E402

ASSETS = ROOT / "assets"
ICO_SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)


def png_bytes(img: QImage) -> bytes:
    ba = QByteArray()
    buf = QBuffer(ba)
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    img.save(buf, "PNG")
    return bytes(ba.data())


def write_ico(path: Path, images: list[QImage]) -> None:
    """Multi-resolution .ico with PNG-compressed entries (supported since Windows Vista)."""
    blobs = [png_bytes(img) for img in images]
    header = struct.pack("<HHH", 0, 1, len(images))
    offset = 6 + 16 * len(images)
    entries = b""
    for img, blob in zip(images, blobs, strict=True):
        w = img.width()
        entries += struct.pack("<BBBBHHII", w % 256, w % 256, 0, 0, 1, 32, len(blob), offset)
        offset += len(blob)
    path.write_bytes(header + entries + b"".join(blobs))


def tone(freqs: list[tuple[float, float]], sr: int = 44100, gain_db: float = -20.0) -> np.ndarray:
    """Soft bell-like notes: (frequency Hz, duration s) pairs with overlap."""
    total = sum(d for _, d in freqs) + 0.25
    out = np.zeros(int(total * sr), dtype=np.float64)
    t0 = 0.0
    for f, d in freqs:
        n = int((d + 0.22) * sr)
        t = np.arange(n) / sr
        env = (1 - np.exp(-t / 0.004)) * np.exp(-t / (d * 0.9 + 0.05))
        sig = np.sin(2 * math.pi * f * t) + 0.18 * np.sin(2 * math.pi * 2 * f * t) + 0.05 * np.sin(2 * math.pi * 3 * f * t)
        start = int(t0 * sr)
        out[start : start + n] += sig * env
        t0 += d * 0.72
    out /= np.max(np.abs(out)) + 1e-9
    out *= 10 ** (gain_db / 20)
    fade = int(0.01 * sr)
    out[-fade:] *= np.linspace(1, 0, fade)
    return out


def write_wav(path: Path, samples: np.ndarray, sr: int = 44100) -> None:
    pcm = (np.clip(samples, -1, 1) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm.tobytes())


def write_icns(path: Path, render) -> None:
    """macOS .icns with PNG entries (16-1024 px, incl. @2x)."""
    kinds = (("icp4", 16), ("icp5", 32), ("ic11", 32), ("ic12", 64), ("ic07", 128), ("ic13", 256), ("ic08", 256),
             ("ic14", 512), ("ic09", 512), ("ic10", 1024))
    body = b""
    for kind, size in kinds:
        blob = png_bytes(render(size))
        body += kind.encode("ascii") + struct.pack(">I", 8 + len(blob)) + blob
    path.write_bytes(b"icns" + struct.pack(">I", 8 + len(body)) + body)


def main() -> None:
    app = QGuiApplication.instance() or QGuiApplication(sys.argv)  # noqa: F841
    from tiro.ui.icons import render_image

    (ASSETS / "icons").mkdir(parents=True, exist_ok=True)
    (ASSETS / "sounds").mkdir(parents=True, exist_ok=True)
    write_ico(ASSETS / "tiro.ico", [render_image(s, "app") for s in ICO_SIZES])
    write_icns(ASSETS / "tiro.icns", lambda size: render_image(size, "app"))
    render_image(512, "app").save(str(ASSETS / "icons" / "tiro-512.png"))
    render_image(256, "app").save(str(ASSETS / "icons" / "tiro-256.png"))

    # preview sheet: app icon at several sizes + tray variants on dark and light taskbars
    sheet = QImage(900, 360, QImage.Format.Format_ARGB32_Premultiplied)
    sheet.fill(QColor("#1F1F1F"))
    p = QPainter(sheet)
    x = 20
    for s in (256, 128, 64, 48, 32, 24, 16):
        p.drawImage(x, 20, render_image(s, "app"))
        x += s + 20
    for row, bg in enumerate(("#101010", "#E9E9E9")):
        y = 290 + row * 34
        p.fillRect(0, y - 4, 900, 32, QColor(bg))
        x = 20
        for variant in ("idle", "live", "busy"):
            for s in (16, 20, 24):
                p.drawImage(x, y, render_image(s, variant))
                x += s + 12
            x += 30
    p.end()
    sheet.save(str(ROOT / "dev" / "icon_preview.png"))

    # menu indicators (drawn at 2x, shown at 14 px)
    from PySide6.QtCore import QPointF
    from PySide6.QtGui import QPainterPath, QPen

    from tiro.ui import theme

    check = QImage(28, 28, QImage.Format.Format_ARGB32_Premultiplied)
    check.fill(Qt.GlobalColor.transparent)
    p = QPainter(check)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(theme.gradient(4, 22, 24, 6), 3.2)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    path = QPainterPath(QPointF(6, 14.5))
    path.lineTo(11.5, 20)
    path.lineTo(22, 8.5)
    p.drawPath(path)
    p.end()
    check.save(str(ASSETS / "icons" / "check.png"))
    dot = QImage(28, 28, QImage.Format.Format_ARGB32_Premultiplied)
    dot.fill(Qt.GlobalColor.transparent)
    p = QPainter(dot)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(theme.gradient(6, 22, 22, 6))
    p.drawEllipse(QPointF(14, 14), 5.5, 5.5)
    p.end()
    dot.save(str(ASSETS / "icons" / "dot.png"))
    chevron = QImage(20, 20, QImage.Format.Format_ARGB32_Premultiplied)
    chevron.fill(Qt.GlobalColor.transparent)
    p = QPainter(chevron)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(QColor(theme.TEXT_2), 2.2)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    path = QPainterPath(QPointF(5, 8))
    path.lineTo(10, 13)
    path.lineTo(15, 8)
    p.drawPath(path)
    p.end()
    chevron.save(str(ASSETS / "icons" / "chevron.png"))

    write_wav(ASSETS / "sounds" / "start.wav", tone([(659.3, 0.07), (987.8, 0.10)], gain_db=-22))
    write_wav(ASSETS / "sounds" / "stop.wav", tone([(987.8, 0.06), (740.0, 0.09)], gain_db=-24))
    write_wav(ASSETS / "sounds" / "error.wav", tone([(311.1, 0.09), (277.2, 0.14)], gain_db=-22))
    print("assets written to", ASSETS)


if __name__ == "__main__":
    main()
