"""System-tray icon (Windows) / menu-bar item (macOS) and its menu.

Both platforms build the menu from the same list of actions (MENU below), so they always offer the same
things. On Windows the menu is drawn in Tiro's own style with a header; on a Mac it's a native menu with a
monochrome template icon that changes while you dictate.
"""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction, QActionGroup, QCursor, QFont
from PySide6.QtWidgets import QHBoxLayout, QLabel, QMenu, QSystemTrayIcon, QVBoxLayout, QWidget, QWidgetAction

from tiro import APP_NAME
from tiro.paths import asset
from tiro.ui import theme
from tiro.ui.icons import make_icon, make_template_icon
from tiro.ui.widgets import Mark

if TYPE_CHECKING:
    from tiro.app import TiroApp

IS_MAC = sys.platform == "darwin"

# One menu for both platforms: (id, label). "-" is a separator.
MENU: tuple[tuple[str, str], ...] = (
    ("status", ""),
    ("toggle", "Start dictation"),
    ("fix", "Fix last transcription…"),
    ("copy", "Copy last dictation"),
    ("-", ""),
    ("mic", "Microphone"),
    ("dictionary", "Custom dictionary…"),
    ("add_word", "Add word to dictionary…"),
    ("-", ""),
    ("mode_hold", "Hold to talk"),
    ("mode_toggle", "Press to toggle"),
    ("autostart", "Launch at login" if IS_MAC else "Start with Windows"),
    ("settings", "Settings…"),
    ("updates", "Check for updates…"),
    ("-", ""),
    ("quit", f"Quit {APP_NAME}"),
)


def _menu_qss() -> str:
    check = asset("icons", "check.png").as_posix()
    dot = asset("icons", "dot.png").as_posix()
    return theme.menu_qss() + f"""
    QMenu::indicator:checked {{ image: url("{check}"); }}
    QMenu::indicator:exclusive:checked {{ image: url("{dot}"); }}
    """


def styled_menu(parent: QWidget | None = None, title: str = "") -> QMenu:
    m = QMenu(title, parent)
    if IS_MAC:
        return m  # native menus on macOS
    m.setWindowFlags(m.windowFlags() | Qt.WindowType.FramelessWindowHint | Qt.WindowType.NoDropShadowWindowHint)
    m.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
    m.setStyleSheet(_menu_qss())
    return m


class _Header(QWidget):
    def __init__(self):
        super().__init__()
        self.setMinimumWidth(236)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 8, 18, 8)
        lay.setSpacing(10)
        lay.addWidget(Mark(30))
        col = QVBoxLayout()
        col.setSpacing(0)
        name = QLabel(APP_NAME)
        name.setFont(theme.font(14, QFont.Weight.DemiBold))
        name.setStyleSheet(f"color: {theme.TEXT}; background: transparent;")
        self.status = QLabel("")
        self.status.setFont(theme.font(12))
        self.status.setStyleSheet(f"color: {theme.TEXT_2}; background: transparent;")
        col.addWidget(name)
        col.addWidget(self.status)
        lay.addLayout(col)
        lay.addStretch(1)


class Tray:
    def __init__(self, app: TiroApp):
        self.app = app
        if IS_MAC:
            self.icons = {v: make_template_icon(v) for v in ("idle", "live", "busy")}
        else:
            self.icons = {v: make_icon(v) for v in ("idle", "live", "busy")}
        self.tray = QSystemTrayIcon(self.icons["busy"])
        self.tray.setToolTip(f"{APP_NAME} — starting…")
        self.menu = styled_menu()
        self.actions: dict[str, QAction] = {}
        self.header: _Header | None = None
        mode_group = QActionGroup(self.menu)
        mode_group.setExclusive(True)
        for key, label in MENU:
            if key == "-":
                self.menu.addSeparator()
            elif key == "status":
                if IS_MAC:
                    a = self.menu.addAction("")
                    a.setEnabled(False)
                    self.actions[key] = a
                else:
                    self.header = _Header()
                    head = QWidgetAction(self.menu)
                    head.setDefaultWidget(self.header)
                    self.menu.addAction(head)
                    self.menu.addSeparator()
            elif key == "mic":
                self.mic_menu = styled_menu(self.menu, label)
                self.menu.addMenu(self.mic_menu)
            else:
                checkable = key in ("mode_hold", "mode_toggle", "autostart")
                a = QAction(label, self.menu, checkable=checkable)
                if key.startswith("mode_"):
                    mode_group.addAction(a)
                a.triggered.connect(lambda checked=False, k=key: self._trigger(k, checked))
                self.menu.addAction(a)
                self.actions[key] = a
        self.menu.aboutToShow.connect(self._refresh)
        self.tray.setContextMenu(self.menu)
        self.tray.activated.connect(self._activated)
        self.tray.messageClicked.connect(self._message_clicked)
        self._message_action: str | None = None
        self.update_status = None  # tiro.update.service.Status when an update is ready / available
        self._state = "busy"
        self.tray.show()

    def _trigger(self, key: str, checked: bool) -> None:
        app = self.app
        {
            "toggle": app.toggle_dictation,
            "fix": app.show_fix_last,
            "copy": app.copy_last,
            "dictionary": lambda: app.show_settings("dictionary"),
            "add_word": app.prompt_add_word,
            "mode_hold": lambda: app.update_setting("mode", "hold"),
            "mode_toggle": lambda: app.update_setting("mode", "toggle"),
            "autostart": lambda: app.set_autostart(checked),
            "settings": app.show_settings,
            "updates": app.updates_action,
            "quit": app.quit,
        }[key]()

    def _activated(self, reason) -> None:
        if not IS_MAC and reason == QSystemTrayIcon.ActivationReason.Trigger:  # left click opens the same menu
            QTimer.singleShot(0, lambda: self._popup())

    def _popup(self) -> None:
        self.menu.popup(QCursor.pos())
        self.menu.activateWindow()

    def _refresh(self) -> None:
        app = self.app
        s = app.settings
        status = app.status_line()
        if self.header is not None:
            self.header.status.setText(status)
        if "status" in self.actions:
            self.actions["status"].setText(f"{APP_NAME} — {status}")
        self.actions["toggle"].setText("Stop dictation" if app.dictating else "Start dictation")
        self.actions["toggle"].setEnabled(app.engine_state != "error")
        self.actions["copy"].setEnabled(bool(app.last_text))
        self.actions["fix"].setEnabled(app.last_session is not None)
        self.actions["mode_hold"].setChecked(s.mode == "hold")
        self.actions["mode_toggle"].setChecked(s.mode == "toggle")
        self.actions["autostart"].setChecked(app.autostart_enabled())
        up = self.update_status
        if up is not None and up.state == "ready":
            self.actions["updates"].setText(f"Restart to update to {APP_NAME} {up.version}")
        elif up is not None and up.state == "available":
            self.actions["updates"].setText(f"Get {APP_NAME} {up.version}?")
        else:
            self.actions["updates"].setText("Check for updates?")
        self.mic_menu.clear()
        group = QActionGroup(self.mic_menu)
        default = app.default_mic_name()
        choices = [(None, f"System default{f' ({default})' if default else ''}")] + [(n, n) for n in app.list_mics()]
        for value, label in choices:
            a = QAction(label, self.mic_menu, checkable=True)
            a.setChecked(value == s.microphone)
            a.triggered.connect(lambda _=False, v=value: self.app.update_setting("microphone", v))
            group.addAction(a)
            self.mic_menu.addAction(a)

    def set_state(self, state: str) -> None:
        """idle | live | busy"""
        self._state = state
        icon = self.icons.get(state, self.icons["idle"])
        if self.update_status is not None and state != "live":
            icon = _badged(icon)  # a small dot: an update is waiting
        self.tray.setIcon(icon)
        tip = f"{APP_NAME} — {self.app.status_line()}"
        if self.update_status is not None and self.update_status.state == "ready":
            tip += f" · {APP_NAME} {self.update_status.version} is ready"
        self.tray.setToolTip(tip)

    def message(self, title: str, text: str, action: str | None = None) -> None:
        self._message_action = action
        self.tray.showMessage(title, text, self.icons["idle"], 8000)

    def notify_update(self, st) -> None:
        """'Tiro x.y is ready': a notification that never interrupts; click it to restart now."""
        summary = (st.summary.strip() + "\n") if st.summary else ""
        if st.critical:
            text = summary + "This update fixes a serious problem. Click to restart now."
        else:
            text = summary + "Click to restart now, or it installs the next time Tiro starts."
        self.message(f"{APP_NAME} {st.version} is ready", text, action="update")

    def set_update(self, st) -> None:
        self.update_status = st
        self.set_state(self._state)

    def _message_clicked(self) -> None:
        action, self._message_action = self._message_action, None
        if action == "update":
            self.app.updates_action()
        elif action == "whats_new":
            self.app.show_whats_new()


def _badged(icon):
    """The tray icon with a small accent dot in the corner."""
    from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap

    out = QIcon()
    for size in (16, 20, 24, 32, 48, 64):
        pm = icon.pixmap(size, size)
        if pm.isNull():
            continue
        pm = QPixmap(pm)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        d = max(5, int(size * 0.38))
        p.setPen(QColor(theme.INK_950))
        p.setBrush(QColor(theme.ROSE))
        p.drawEllipse(size - d - 1, size - d - 1, d, d)
        p.end()
        out.addPixmap(pm)
    return out
