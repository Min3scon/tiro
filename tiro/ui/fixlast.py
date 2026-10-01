"""'Fix last transcription': correct a misheard word once, and Tiro gets it right from then on."""

from __future__ import annotations

import logging
import time

from PySide6.QtCore import QEvent, QObject, Qt, QTimer
from PySide6.QtGui import QGuiApplication, QKeyEvent
from PySide6.QtWidgets import QCheckBox, QDialog, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QVBoxLayout

from tiro import winutil
from tiro.learn import fixes_from_edit
from tiro.ui import theme
from tiro.ui.dialogs import _qss
from tiro.ui.icons import make_icon

log = logging.getLogger(__name__)

MAX_REPLACE_CHARS = 2000  # longer dictations are corrected via the clipboard instead


class _EnterSaves(QObject):
    """Enter saves, Shift+Enter starts a new line."""

    def __init__(self, on_enter):
        super().__init__()
        self.on_enter = on_enter

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Type.KeyPress and isinstance(event, QKeyEvent):
            if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and not (
                    event.modifiers() & Qt.KeyboardModifier.ShiftModifier):
                self.on_enter()
                return True
        return False


class FixLastWindow(QDialog):
    _instance: FixLastWindow | None = None

    @classmethod
    def open(cls, app, session) -> None:
        if cls._instance is not None:
            cls._instance.close()
        w = cls(app, session)
        cls._instance = w
        w.show()
        winutil.dark_title_bar(int(w.winId()))
        w.raise_()
        w.activateWindow()
        w.edit.setFocus()

    def __init__(self, app, session):
        super().__init__()
        self.app = app
        self.session = session
        self.original = session.text.strip()
        self.target = session.inject_hwnd
        # replacing in place is only safe if nothing was typed or clicked in any app since Tiro typed this
        self.can_replace = (
            winutil.is_window(self.target)
            and app.context.events == session.last_inject_events
            and 0 < len(self.original) <= MAX_REPLACE_CHARS
        )
        self.setWindowTitle("Fix last transcription")
        self.setWindowIcon(make_icon("app"))
        self.setStyleSheet(_qss() + f"""
            QPlainTextEdit {{ background: {theme.INK_800}; border: 1px solid rgba(255,255,255,0.14);
                border-radius: 8px; padding: 6px 8px; selection-background-color: {theme.ROSE}; font-size: 14px; }}
            QPlainTextEdit:focus {{ border-color: {theme.ROSE}; }}
            QCheckBox {{ color: {theme.TEXT_2}; }}
            QLabel#dlgtitle {{ font-size: 17px; font-weight: 600; }}
        """)
        self.setMinimumWidth(520)
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(22, 20, 22, 18)
        lay.setSpacing(10)
        head = QLabel("Fix last transcription")
        head.setObjectName("dlgtitle")
        lay.addWidget(head)
        hint = QLabel("Correct any word Tiro misheard, then press Enter. Tiro remembers the fix and spells it "
                      "your way next time. Only word swaps are learned, and they stay on this computer.")
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        lay.addWidget(hint)
        self.edit = QPlainTextEdit(self.original)
        self.edit.setTabChangesFocus(True)
        self.edit.setMinimumHeight(96)
        self._filter = _EnterSaves(self.save)
        self.edit.installEventFilter(self._filter)
        cursor = self.edit.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        self.edit.setTextCursor(cursor)
        lay.addWidget(self.edit)
        app_name = session.app_name or "the app"
        self.replace_box = QCheckBox(f"Also replace it in {app_name.title() if session.app_name else app_name}")
        self.replace_box.setChecked(self.can_replace)
        self.replace_box.setVisible(self.can_replace)
        lay.addWidget(self.replace_box)
        if not self.can_replace:
            note = QLabel("You've typed or clicked since, so the corrected text will be copied for you to paste.")
            note.setObjectName("hint")
            note.setWordWrap(True)
            lay.addWidget(note)
        row = QHBoxLayout()
        row.addStretch(1)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.close)
        ok = QPushButton("Save fix")
        ok.setObjectName("primary")
        ok.clicked.connect(self.save)
        row.addWidget(cancel)
        row.addWidget(ok)
        lay.addLayout(row)

    def save(self) -> None:
        new = self.edit.toPlainText().strip()
        old = self.original
        learned = []
        for heard, written in fixes_from_edit(old, new):
            if self.app.correction.learn(heard, written, old):
                learned.append(written)
        changed = new != old
        replace = changed and self.can_replace and self.replace_box.isChecked()
        self.close()
        if replace:
            QTimer.singleShot(60, lambda: self._replace_in_place(old, new, learned))
            return
        if changed:
            QGuiApplication.clipboard().setText(new)
        self._report(learned, copied=changed)

    def _replace_in_place(self, old: str, new: str, learned: list[str]) -> None:
        ok = False
        if winutil.activate(self.target):
            time.sleep(0.05)
            if winutil.foreground_window() == self.target:
                p = 0
                while p < min(len(old), len(new)) and old[p] == new[p]:
                    p += 1
                ok = self.app.injector.replace_tail(len(old) - p, new[p:])
        if not ok:
            QGuiApplication.clipboard().setText(new)
        else:
            self.session.raw_text = ""  # what's in the app now is the user's text
        self._report(learned, copied=not ok)

    def _report(self, learned: list[str], copied: bool) -> None:
        if learned:
            names = ", ".join(f"“{w}”" for w in learned[:3])
            msg = f"Learned {names}. Tiro will spell it that way from now on."
        elif copied:
            msg = "Nothing to learn there (only misheard words are learned)."
        else:
            return
        if copied:
            msg += " Corrected text copied."
        self.app.overlay.notify(msg, "ok" if learned else "warning", seconds=4.5)

    def closeEvent(self, event) -> None:
        if FixLastWindow._instance is self:
            FixLastWindow._instance = None
        super().closeEvent(event)
