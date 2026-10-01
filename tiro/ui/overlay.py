"""The floating HUD: a small dark pill with a live waveform and the last few words being transcribed.

It never takes focus and is click-through, so typing always lands in the app underneath.
"""

from __future__ import annotations

import math
import time
from collections.abc import Callable

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QGuiApplication, QLinearGradient, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget

from tiro import winutil
from tiro.ui import theme

CANVAS_W, CANVAS_H = 620, 110
PILL_H = 46
MIN_W = 92  # waveform only
MAX_W = 520
PAD_X = 18
WAVE_W = 26
TEXT_GAP = 14
BARS = 5


def _ease_out(x: float) -> float:
    return 1 - (1 - x) ** 3


class _WordAnim:
    __slots__ = ("text", "born", "final", "alpha", "width")

    def __init__(self, text: str, final: bool, born: float, width: float):
        self.text = text
        self.final = final
        self.born = born
        self.alpha = 0.0
        self.width = width


class Overlay(QWidget):
    def __init__(self, level_source: Callable[[], float]):
        super().__init__(None)
        flags = (
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowTransparentForInput
            | Qt.WindowType.WindowDoesNotAcceptFocus
            | Qt.WindowType.NoDropShadowWindowHint
        )
        self.setWindowFlags(flags)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setFixedSize(CANVAS_W, CANVAS_H)
        self.level_source = level_source
        self.position = "bottom"
        self.show_text = True

        self._font = theme.font(15, QFont.Weight.Medium)
        self._fm = QFontMetricsF(self._font)
        self._small = theme.font(12, QFont.Weight.Medium)
        self._space = self._fm.horizontalAdvance(" ")

        self.state = "hidden"  # hidden | listening | loading | finishing | notice
        self.locked = False
        self._presence = 0.0
        self._presence_target = 0.0
        self._width = MIN_W
        self._words: list[_WordAnim] = []
        self._scroll = 0.0
        self._bars = [0.0] * BARS
        self._level = 0.0
        self._t0 = time.monotonic()
        self._notice = ""
        self._notice_kind = "info"
        self._hide_at: float | None = None

        self._timer = QTimer(self)
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self._tick)

    # ------------------------------------------------------------------ public API (main thread)
    def begin(self, loading: bool = False) -> None:
        self._words.clear()
        self._scroll = 0.0
        self._notice = ""
        self.locked = False
        self._hide_at = None
        self.state = "loading" if loading else "listening"
        self._appear()

    def set_state(self, state: str) -> None:
        if state in ("listening", "loading", "finishing"):
            self.state = state
            self._hide_at = None
            self._appear()
        elif state in ("done", "cancelled", "error"):
            linger = 0.9 if (state == "done" and self._words) else 0.15
            if self.state == "notice":
                return
            self.state = "finishing" if state == "done" else self.state
            self._hide_at = time.monotonic() + linger

    def set_locked(self, locked: bool) -> None:
        self.locked = locked
        self.update()

    def set_text(self, committed: str, pending: str) -> None:
        if not self.show_text:
            return
        now = time.monotonic()
        tokens = [(w, True) for w in committed.replace("\n", " ").split()] + [(w, False) for w in pending.split()]
        tokens = tokens[-40:]
        old = self._words
        # keep animation state for the unchanged prefix (matched from the end of what is shown)
        new: list[_WordAnim] = []
        offset = max(0, len(old) - len(tokens)) if len(old) > len(tokens) else 0
        for i, (text, final) in enumerate(tokens):
            j = i + offset
            prev = old[j] if j < len(old) else None
            if prev is not None and prev.text == text:
                prev.final = final
                new.append(prev)
            else:
                w = _WordAnim(text, final, now, self._fm.horizontalAdvance(text))
                if prev is not None and prev.text.lower().strip(".,?!") == text.lower().strip(".,?!"):
                    w.alpha = prev.alpha  # punctuation/casing revision: no re-fade
                new.append(w)
        self._words = new
        self._appear()

    def notify(self, message: str, kind: str = "info", seconds: float = 3.2) -> None:
        self._notice = message
        self._notice_kind = kind
        self.state = "notice"
        self._words.clear()
        self._hide_at = time.monotonic() + seconds
        self._appear()

    # ------------------------------------------------------------------ animation
    def _appear(self) -> None:
        self._presence_target = 1.0
        if not self.isVisible():
            self._place()
            self._presence = 0.0
            self.show()
            winutil.make_overlay_window(int(self.winId()))
        if not self._timer.isActive():
            self._t0 = time.monotonic()
            self._timer.start()

    def _place(self) -> None:
        device = winutil.monitor_device(winutil.foreground_window())
        screen = next((s for s in QGuiApplication.screens() if s.name() == device), None)
        screen = screen or QGuiApplication.primaryScreen()
        geo = screen.availableGeometry()
        x = geo.center().x() - CANVAS_W // 2
        y = geo.top() + 18 if self.position == "top" else geo.bottom() - CANVAS_H - 34
        self.move(x, y)

    def _text_width(self) -> float:
        return sum(w.width for w in self._words) + self._space * max(0, len(self._words) - 1)

    def _text_area(self, pill_w: float) -> tuple[float, float]:
        """(left x, available width) of the transcript area for a pill of width pill_w (centred on 0)."""
        left = -pill_w / 2 + PAD_X + WAVE_W + TEXT_GAP
        right = pill_w / 2 - PAD_X - (22 if self.locked else 0)
        return left, max(0.0, right - left)

    def _target_width(self) -> float:
        if self.state == "notice":
            return min(MAX_W, PAD_X * 2 + 22 + self._fm.horizontalAdvance(self._notice))
        if self.state == "loading":
            return PAD_X * 2 + WAVE_W + TEXT_GAP + self._fm.horizontalAdvance("Loading speech model…")
        extra = 26 if self.locked else 0
        if not self._words:
            return MIN_W + extra
        return min(MAX_W, PAD_X * 2 + WAVE_W + TEXT_GAP + self._text_width() + extra + 4)

    def _tick(self) -> None:
        now = time.monotonic()
        if self._hide_at is not None and now >= self._hide_at:
            self._presence_target = 0.0
            self._hide_at = None
        step = 0.16 if self._presence_target > self._presence else 0.12
        self._presence += (self._presence_target - self._presence) * step * 1.6
        if abs(self._presence - self._presence_target) < 0.004:
            self._presence = self._presence_target
        self._width += (self._target_width() - self._width) * 0.2

        lvl = self.level_source() if self.state in ("listening",) else 0.0
        self._level += (lvl - self._level) * (0.5 if lvl > self._level else 0.15)
        t = now - self._t0
        for i in range(BARS):
            if self.state == "listening":
                wobble = 0.55 + 0.45 * math.sin(t * (7.0 + i * 1.3) + i * 1.7)
                shape = (0.55, 0.8, 1.0, 0.75, 0.5)[i]
                target = 0.12 + 0.88 * min(1.0, self._level * 1.25) * shape * wobble
                idle = 0.10 + 0.05 * (0.5 + 0.5 * math.sin(t * 2.2 + i * 0.9))
                target = max(target, idle)
            elif self.state == "finishing":
                target = 0.16 + 0.22 * max(0.0, math.sin(t * 9.0 - i * 0.8))
            else:
                target = 0.12
            self._bars[i] += (target - self._bars[i]) * 0.35
        for w in self._words:
            w.alpha = min(1.0, w.alpha + 0.09)
        target_scroll = max(0.0, self._text_width() - self._text_area(self._width)[1])
        self._scroll += (target_scroll - self._scroll) * 0.25
        if abs(target_scroll - self._scroll) < 0.3:
            self._scroll = target_scroll

        if self._presence <= 0.0 and self._presence_target == 0.0:
            self._timer.stop()
            self.hide()
            self.state = "hidden"
            self._words.clear()
            return
        self.update()

    # ------------------------------------------------------------------ painting
    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        e = _ease_out(max(0.0, min(1.0, self._presence)))
        p.setOpacity(e)
        w = self._width
        cx = CANVAS_W / 2
        cy = CANVAS_H / 2 + (1 - e) * 10
        scale = 0.94 + 0.06 * e
        p.translate(cx, cy)
        p.scale(scale, scale)
        pill = QRectF(-w / 2, -PILL_H / 2, w, PILL_H)

        # shadow
        for i, a in enumerate((0.10, 0.07, 0.05, 0.03)):
            grow = 3 + i * 4
            sh = pill.adjusted(-grow, -grow + 4, grow, grow + 6)
            path = QPainterPath()
            path.addRoundedRect(sh, sh.height() / 2, sh.height() / 2)
            p.fillPath(path, QColor(0, 0, 0, int(255 * a)))

        body = QPainterPath()
        body.addRoundedRect(pill, PILL_H / 2, PILL_H / 2)
        bg = QLinearGradient(pill.topLeft(), pill.bottomLeft())
        bg.setColorAt(0, QColor(28, 25, 37, 246))
        bg.setColorAt(1, QColor(16, 15, 22, 246))
        p.fillPath(body, bg)
        p.setPen(QPen(QColor(255, 255, 255, 22), 1))
        p.drawPath(body)
        p.setPen(Qt.PenStyle.NoPen)

        p.setClipPath(body)
        if self.state == "notice":
            self._paint_notice(p, pill)
        else:
            self._paint_wave(p, pill)
            self._paint_text(p, pill)
        p.end()

    def _paint_wave(self, p: QPainter, pill: QRectF) -> None:
        x0 = pill.left() + PAD_X if self._words or self.state == "loading" else -WAVE_W / 2
        if self.state == "loading":
            self._paint_spinner(p, QPointF(x0 + WAVE_W / 2, 0))
            return
        bar_w, gap = 3.2, 2.5
        total = BARS * bar_w + (BARS - 1) * gap
        start = x0 + (WAVE_W - total) / 2
        max_h = PILL_H * 0.52
        grad = theme.gradient(start, max_h / 2, start + total, -max_h / 2)
        for i, v in enumerate(self._bars):
            h = max(bar_w, max_h * v)
            r = QRectF(start + i * (bar_w + gap), -h / 2, bar_w, h)
            path = QPainterPath()
            path.addRoundedRect(r, bar_w / 2, bar_w / 2)
            p.fillPath(path, grad)

    def _paint_spinner(self, p: QPainter, c: QPointF) -> None:
        t = time.monotonic() - self._t0
        p.save()
        p.translate(c)
        p.rotate((t * 360) % 360)
        pen = QPen(QColor(theme.ROSE), 2.4)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        p.drawArc(QRectF(-8, -8, 16, 16), 0, 270 * 16)
        p.restore()
        p.setFont(self._font)
        p.setPen(theme.qcolor(theme.TEXT_2))
        p.drawText(QRectF(c.x() + WAVE_W / 2 + TEXT_GAP - 4, -PILL_H / 2, 400, PILL_H),
                   Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, "Loading speech model…")

    def _paint_text(self, p: QPainter, pill: QRectF) -> None:
        if not self._words:
            return
        left, avail = self._text_area(pill.width())
        p.save()
        p.setClipRect(QRectF(left - 2, pill.top(), avail + 4, pill.height()), Qt.ClipOperation.IntersectClip)
        p.setFont(self._font)
        x = left - self._scroll
        now = time.monotonic()
        edge = min(1.0, self._scroll / 24.0)  # the left fade only kicks in once the text scrolls
        for wa in self._words:
            if x + wa.width >= left - 40:
                rise = (1 - _ease_out(min(1.0, (now - wa.born) / 0.22))) * 6
                fade = 1.0
                if edge > 0 and x < left + 48:
                    fade = 1.0 - edge * (1.0 - max(0.0, min(1.0, (x - left + 12) / 60)))
                base = theme.qcolor(theme.TEXT) if wa.final else theme.qcolor("#CFC8DA")
                base.setAlphaF(max(0.0, min(1.0, wa.alpha * fade * (1.0 if wa.final else 0.78))))
                p.setPen(base)
                p.drawText(QPointF(x, self._fm.ascent() / 2 - self._fm.descent() / 2 + 1 + rise), wa.text)
            x += wa.width + self._space
        p.restore()
        if self.locked:
            self._paint_lock(p, QPointF(pill.right() - PAD_X - 8, 0))

    def _paint_lock(self, p: QPainter, c: QPointF) -> None:
        """Hands-free indicator: a pulsing ember 'recording' dot with a soft halo."""
        t = time.monotonic() - self._t0
        pulse = 0.5 + 0.5 * math.sin(t * 3.2)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(theme.qcolor(theme.EMBER, 0.14 + 0.16 * pulse))
        p.drawEllipse(c, 7.5 + 1.5 * pulse, 7.5 + 1.5 * pulse)
        p.setBrush(theme.qcolor(theme.EMBER, 0.85 + 0.15 * pulse))
        p.drawEllipse(c, 4.4, 4.4)

    def _paint_notice(self, p: QPainter, pill: QRectF) -> None:
        color = {"error": theme.DANGER, "warning": theme.EMBER, "ok": theme.OK}.get(self._notice_kind, theme.ROSE)
        c = QPointF(pill.left() + PAD_X + 5, 0)
        p.setBrush(QColor(color))
        p.drawEllipse(c, 4.5, 4.5)
        p.setFont(self._font)
        p.setPen(theme.qcolor(theme.TEXT))
        rect = QRectF(c.x() + 14, pill.top(), pill.width() - PAD_X * 2 - 14, pill.height())
        text = self._fm.elidedText(self._notice, Qt.TextElideMode.ElideRight, rect.width())
        p.drawText(rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, text)
