"""Small dialogs in Tiro's style."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout

from tiro import winutil
from tiro.ui import theme
from tiro.ui.icons import make_icon


def _qss() -> str:
    t = theme
    return f"""
    QDialog {{ background: {t.INK_900}; }}
    QWidget {{ color: {t.TEXT}; font-family: "{t.FONT_FAMILY}"; font-size: 13px; }}
    QLabel#hint {{ color: {t.TEXT_2}; }}
    QLabel#dlgtitle {{ font-size: 17px; font-weight: 600; }}
    QLineEdit {{ background: {t.INK_800}; border: 1px solid rgba(255,255,255,0.14); border-radius: 8px;
        padding: 8px 10px; selection-background-color: {t.ROSE}; }}
    QLineEdit:focus {{ border-color: {t.ROSE}; }}
    QPushButton {{ background: {t.INK_800}; border: 1px solid {t.LINE}; border-radius: 8px; padding: 7px 16px; }}
    QPushButton:hover {{ background: {t.INK_750}; }}
    QPushButton#primary {{
        background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {t.TYRIAN}, stop:0.55 {t.ROSE}, stop:1 {t.EMBER});
        border: none; color: white; font-weight: 600; padding: 8px 20px;
    }}
    """


def ask_text(title: str, hint: str, placeholder: str = "", parent=None) -> str | None:
    dlg = QDialog(parent)
    dlg.setWindowTitle(title)
    dlg.setWindowIcon(make_icon("app"))
    dlg.setStyleSheet(_qss())
    dlg.setMinimumWidth(440)
    dlg.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint)
    lay = QVBoxLayout(dlg)
    lay.setContentsMargins(22, 20, 22, 18)
    lay.setSpacing(10)
    head = QLabel(title)
    head.setObjectName("dlgtitle")
    lay.addWidget(head)
    info = QLabel(hint)
    info.setObjectName("hint")
    info.setWordWrap(True)
    lay.addWidget(info)
    edit = QLineEdit()
    edit.setPlaceholderText(placeholder)
    lay.addWidget(edit)
    row = QHBoxLayout()
    row.addStretch(1)
    cancel = QPushButton("Cancel")
    cancel.clicked.connect(dlg.reject)
    ok = QPushButton("Add")
    ok.setObjectName("primary")
    ok.setDefault(True)
    ok.clicked.connect(dlg.accept)
    row.addWidget(cancel)
    row.addWidget(ok)
    lay.addLayout(row)
    dlg.show()
    winutil.dark_title_bar(int(dlg.winId()))
    dlg.raise_()
    dlg.activateWindow()
    edit.setFocus()
    if dlg.exec() == QDialog.DialogCode.Accepted and edit.text().strip():
        return edit.text().strip()
    return None
