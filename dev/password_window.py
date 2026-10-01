"""A window with a password field (focused first) and a normal field (focused after 3 s)."""
import sys
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QLineEdit, QVBoxLayout, QWidget

app = QApplication(sys.argv)
w = QWidget()
w.setWindowTitle("Tiro secure-field probe")
lay = QVBoxLayout(w)
pw = QLineEdit(); pw.setEchoMode(QLineEdit.EchoMode.Password)
normal = QLineEdit()
lay.addWidget(pw); lay.addWidget(normal)
w.resize(320, 100); w.show(); w.raise_(); w.activateWindow()
pw.setFocus()
QTimer.singleShot(3000, normal.setFocus)
QTimer.singleShot(6000, app.quit)
app.exec()
