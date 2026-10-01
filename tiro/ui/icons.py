"""The Tiro mark, drawn in code: an ink squircle holding a waveform whose tallest bar ends in a pen nib."""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QImage, QLinearGradient, QPainter, QPainterPath, QPixmap, QRadialGradient

from tiro.ui import theme

# heights of the five waveform bars, as a fraction of the drawable height
_BARS_5 = (0.34, 0.62, 1.0, 0.56, 0.28)
_BARS_3 = (0.52, 1.0, 0.62)


def _squircle(rect: QRectF, radius_frac: float = 0.235) -> QPainterPath:
    path = QPainterPath()
    r = rect.width() * radius_frac
    path.addRoundedRect(rect, r, r)
    return path


def _bar_path(x: float, y_top: float, w: float, h: float, nib: bool) -> QPainterPath:
    path = QPainterPath()
    if not nib:
        path.addRoundedRect(QRectF(x, y_top, w, h), w / 2, w / 2)
        return path
    # rounded top, straight sides, then a tapering pen-nib point at the bottom
    r = w / 2
    cx = x + r
    taper = h * 0.30
    path.moveTo(x, y_top + r)
    path.arcTo(QRectF(x, y_top, w, w), 180, -180)
    path.lineTo(x + w, y_top + h - taper)
    path.quadTo(QPointF(x + w, y_top + h - taper * 0.35), QPointF(cx + w * 0.08, y_top + h - w * 0.08))
    path.quadTo(QPointF(cx, y_top + h + w * 0.02), QPointF(cx - w * 0.08, y_top + h - w * 0.08))
    path.quadTo(QPointF(x, y_top + h - taper * 0.35), QPointF(x, y_top + h - taper))
    path.closeSubpath()
    return path


def paint_mark(p: QPainter, size: float, variant: str = "app") -> None:
    """variant: app | idle | live | busy"""
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    small = size < 40
    pad = size * (0.04 if small else 0.06)
    outer = QRectF(pad, pad, size - 2 * pad, size - 2 * pad)
    shape = _squircle(outer)

    if variant == "live":
        p.fillPath(shape, theme.gradient(outer.left(), outer.bottom(), outer.right(), outer.top()))
    else:
        bg = QLinearGradient(outer.topLeft(), outer.bottomLeft())
        bg.setColorAt(0.0, QColor("#24202F"))
        bg.setColorAt(1.0, QColor("#0D0C13"))
        p.fillPath(shape, bg)
    if not small:
        p.setPen(theme.qcolor("#FFFFFF", 0.07))
        p.drawPath(_squircle(outer.adjusted(0.5, 0.5, -0.5, -0.5)))
        p.setPen(Qt.PenStyle.NoPen)

    heights = _BARS_3 if small else _BARS_5
    n = len(heights)
    inner_h = outer.height() * (0.58 if small else 0.54)
    bar_w = outer.width() * (0.15 if small else 0.085)
    gap = outer.width() * (0.075 if small else 0.062)
    total_w = n * bar_w + (n - 1) * gap
    x0 = outer.center().x() - total_w / 2
    cy = outer.center().y()

    if variant == "app" and not small:
        glow = QRadialGradient(outer.center(), outer.width() * 0.42)
        glow.setColorAt(0.0, theme.qcolor(theme.ROSE, 0.30))
        glow.setColorAt(1.0, theme.qcolor(theme.ROSE, 0.0))
        p.fillPath(shape, glow)

    if variant == "live":
        brush = QColor("#FFFFFF")
    elif variant == "busy":
        brush = theme.qcolor(theme.TEXT_2, 0.75)
    else:
        brush = theme.gradient(x0, cy + inner_h / 2, x0 + total_w, cy - inner_h / 2)
    p.setPen(Qt.PenStyle.NoPen)
    for i, frac in enumerate(heights):
        h = max(bar_w, inner_h * frac)
        x = x0 + i * (bar_w + gap)
        if small:  # snap to whole pixels so 16 px icons stay crisp
            x, bw, h = round(x), max(1.0, round(bar_w)), round(h)
        else:
            bw = bar_w
        nib = not small and i == n // 2
        top = cy - h / 2 - (h * 0.04 if nib else 0)
        p.fillPath(_bar_path(x, top, bw, h, nib), brush)


def render_image(size: int, variant: str = "app") -> QImage:
    img = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    paint_mark(p, size, variant)
    p.end()
    return img


def make_icon(variant: str = "app") -> QIcon:
    icon = QIcon()
    for s in (16, 20, 24, 32, 40, 48, 64, 128, 256):
        icon.addPixmap(QPixmap.fromImage(render_image(s, variant)))
    return icon


def render_template(px: int, variant: str = "idle") -> QImage:
    """Monochrome menu-bar glyph (macOS template image: only the alpha channel matters).

    idle: three waveform bars; live: the bars knocked out of a filled rounded square; busy: faded bars."""
    img = QImage(px, px, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(Qt.PenStyle.NoPen)
    box = QRectF(px * 0.08, px * 0.08, px * 0.84, px * 0.84)
    bar_w, gap = px * 0.14, px * 0.09
    x0 = box.center().x() - (3 * bar_w + 2 * gap) / 2
    bars = QPainterPath()
    for i, frac in enumerate(_BARS_3):
        h = max(bar_w, box.height() * 0.62 * frac)
        bars.addRoundedRect(QRectF(x0 + i * (bar_w + gap), box.center().y() - h / 2, bar_w, h), bar_w / 2, bar_w / 2)
    black = QColor(0, 0, 0)
    if variant == "live":
        square = QPainterPath()
        square.addRoundedRect(box, px * 0.2, px * 0.2)
        p.fillPath(square.subtracted(bars), black)
    else:
        if variant == "busy":
            black.setAlphaF(0.45)
        p.fillPath(bars, black)
    p.end()
    return img


def make_template_icon(variant: str = "idle") -> QIcon:
    icon = QIcon()
    for px in (18, 36):
        icon.addPixmap(QPixmap.fromImage(render_template(px, variant)))
    icon.setIsMask(True)  # macOS draws it as a template image (adapts to light/dark menu bars)
    return icon
