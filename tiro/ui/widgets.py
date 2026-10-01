"""Small custom widgets in Tiro's style: toggle switch, segmented control, hotkey button, level meter."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Property, QEasingCurve, QPropertyAnimation, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QAbstractButton,
    QButtonGroup,
    QComboBox,
    QHBoxLayout,
    QPushButton,
    QSizePolicy,
    QWidget,
)

from tiro.ui import theme


class Combo(QComboBox):
    """A combo box that never changes on the mouse wheel (the wheel scrolls the page instead), so scrolling
    the settings window past it can't silently switch the microphone or start a model download."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def wheelEvent(self, e) -> None:
        e.ignore()


class Toggle(QAbstractButton):
    """iOS-style switch with an animated knob."""

    def __init__(self, checked: bool = False, parent: QWidget | None = None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setChecked(checked)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._pos = 1.0 if checked else 0.0
        self._anim = QPropertyAnimation(self, b"knob", self)
        self._anim.setDuration(160)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.toggled.connect(self._animate)

    def sizeHint(self) -> QSize:
        return QSize(40, 22)

    def _animate(self, on: bool) -> None:
        self._anim.stop()
        self._anim.setStartValue(self._pos)
        self._anim.setEndValue(1.0 if on else 0.0)
        self._anim.start()

    def _get_knob(self) -> float:
        return self._pos

    def _set_knob(self, v: float) -> None:
        self._pos = v
        self.update()

    knob = Property(float, _get_knob, _set_knob)

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(1, 1, self.width() - 2, self.height() - 2)
        track = QPainterPath()
        track.addRoundedRect(r, r.height() / 2, r.height() / 2)
        off = QColor(theme.INK_700)
        if self._pos > 0:
            g = theme.gradient(r.left(), 0, r.right(), 0, alpha=self._pos)
            p.fillPath(track, off)
            p.fillPath(track, g)
        else:
            p.fillPath(track, off)
        d = r.height() - 6
        x = r.left() + 3 + (r.width() - d - 6) * self._pos
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#FFFFFF") if self.isEnabled() else QColor(theme.TEXT_3))
        p.drawEllipse(QRectF(x, r.top() + 3, d, d))


class Segmented(QWidget):
    """A pill with mutually exclusive options."""

    changed = Signal(str)

    def __init__(self, options: list[tuple[str, str]], value: str, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("segmented")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(3, 3, 3, 3)
        lay.setSpacing(2)
        self._group = QButtonGroup(self)
        self._buttons: dict[str, QPushButton] = {}
        for key, label in options:
            b = QPushButton(label)
            b.setObjectName("segment")
            b.setCheckable(True)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setChecked(key == value)
            b.clicked.connect(lambda _=False, k=key: self.changed.emit(k))
            self._group.addButton(b)
            self._buttons[key] = b
            lay.addWidget(b)

    def set_value(self, key: str) -> None:
        if key in self._buttons:
            self._buttons[key].setChecked(True)


class HotkeyButton(QPushButton):
    """Shows the current hotkey; click it and press a new key (or combination) to change it."""

    def __init__(self, label: str, on_capture: Callable[[], None], parent: QWidget | None = None):
        super().__init__(label, parent)
        self.setObjectName("hotkey")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
        self._label = label
        self.capturing = False
        self.clicked.connect(on_capture)

    def set_capturing(self, on: bool) -> None:
        self.capturing = on
        self.setProperty("capturing", on)
        self.setText("Press a key…  (Esc to cancel)" if on else self._label)
        self.style().unpolish(self)
        self.style().polish(self)

    def set_label(self, label: str) -> None:
        self._label = label
        if not self.capturing:
            self.setText(label)


class LevelMeter(QWidget):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.level = 0.0
        self.setFixedHeight(6)

    def set_level(self, v: float) -> None:
        self.level = v
        self.update()

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(0, 0, self.width(), self.height())
        track = QPainterPath()
        track.addRoundedRect(r, 3, 3)
        p.fillPath(track, QColor(theme.INK_700))
        if self.level > 0.01:
            fill = QPainterPath()
            fill.addRoundedRect(QRectF(0, 0, max(6.0, r.width() * self.level), r.height()), 3, 3)
            p.fillPath(fill, theme.gradient(0, 0, r.width(), 0))


class Mark(QWidget):
    """The Tiro logo mark as a widget."""

    def __init__(self, size: int = 40, parent: QWidget | None = None):
        super().__init__(parent)
        self.setFixedSize(size, size)

    def paintEvent(self, _e) -> None:
        from tiro.ui.icons import paint_mark

        p = QPainter(self)
        paint_mark(p, self.width(), "app")


def section_title(text: str) -> QWidget:
    from PySide6.QtWidgets import QLabel

    lbl = QLabel(text.upper())
    lbl.setObjectName("section")
    f = theme.font(11, QFont.Weight.DemiBold)
    f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 1.2)
    lbl.setFont(f)
    return lbl


def pen(color: str, width: float = 1.0) -> QPen:
    return QPen(QColor(color), width)
