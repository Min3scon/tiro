"""Settings window: a sidebar of pages (General, Recognition, Accuracy, Dictionary & learning, Privacy, About)."""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from tiro import APP_NAME, __version__, winutil
from tiro.models import LANGUAGE_MODELS, MODELS, find_model, is_installed
from tiro.paths import asset
from tiro.ui import theme
from tiro.ui.icons import make_icon
from tiro.ui.widgets import Combo, HotkeyButton, LevelMeter, Mark, Segmented, Toggle, section_title

if TYPE_CHECKING:
    from tiro.app import TiroApp

IS_MAC = sys.platform == "darwin"
PAGES = (
    ("general", "General"),
    ("recognition", "Recognition"),
    ("accuracy", "Accuracy"),
    ("dictionary", "Dictionary & learning"),
    ("privacy", "Privacy"),
    ("about", "About"),
)
PRIVACY_PROMISE = (
    "Everything stays on this computer. No audio, text or history is ever sent anywhere: speech recognition, "
    "corrections and learning all run locally."
)


def _qss() -> str:
    t = theme
    return f"""
    QWidget#root {{ background: {t.INK_900}; }}
    QWidget {{ color: {t.TEXT}; font-family: "{t.FONT_FAMILY}"; font-size: 13px; }}
    QScrollArea, QScrollArea > QWidget > QWidget {{ background: transparent; border: none; }}
    QWidget#sidebar {{ background: {t.INK_950}; border-right: 1px solid {t.LINE}; }}
    QListWidget#nav {{ background: transparent; border: none; outline: none; padding: 4px 8px; }}
    QListWidget#nav::item {{ padding: 9px 12px; border-radius: 8px; margin: 1px 0; color: {t.TEXT_2}; }}
    QListWidget#nav::item:hover {{ background: {t.INK_850}; color: {t.TEXT}; }}
    QListWidget#nav::item:selected {{ background: {t.INK_800}; color: {t.TEXT}; }}
    QLabel#section {{ color: {t.TEXT_3}; padding-top: 6px; }}
    QLabel#pagetitle {{ font-size: 20px; font-weight: 600; }}
    QPlainTextEdit#dictionary {{
        background: {t.INK_800}; border: 1px solid {t.LINE}; border-radius: 8px; padding: 6px 8px;
        font-family: "{t.FONT_FAMILY}"; font-size: 13px; selection-background-color: {t.ROSE};
    }}
    QPlainTextEdit#dictionary:focus {{ border-color: rgba(230,80,142,0.6); }}
    QLabel#hint {{ color: {t.TEXT_3}; font-size: 12px; }}
    QLabel#promise {{ color: {t.TEXT}; font-size: 13px; }}
    QLabel#stat {{ color: {t.TEXT_2}; font-size: 12px; }}
    QLabel#title {{ font-size: 15px; font-weight: 600; }}
    QLabel#subtitle {{ color: {t.TEXT_2}; font-size: 12px; }}
    QLabel#rowlabel {{ color: {t.TEXT}; }}
    QFrame#card {{ background: {t.INK_850}; border: 1px solid {t.LINE}; border-radius: 12px; }}
    QFrame#promisecard {{ background: rgba(92,214,162,0.08); border: 1px solid rgba(92,214,162,0.35);
        border-radius: 12px; }}
    QComboBox {{
        background: {t.INK_800}; border: 1px solid {t.LINE}; border-radius: 8px; padding: 6px 30px 6px 10px;
        min-width: 210px; color: {t.TEXT};
    }}
    QComboBox:hover {{ border-color: rgba(255,255,255,0.16); }}
    QComboBox::drop-down {{ border: none; width: 28px; }}
    QComboBox::down-arrow {{ image: url("{asset("icons", "chevron.png").as_posix()}"); width: 10px; height: 10px;
        margin-right: 10px; }}
    QComboBox QAbstractItemView {{
        background: {t.INK_800}; border: 1px solid {t.LINE}; outline: none; padding: 4px;
        selection-background-color: {t.INK_700}; color: {t.TEXT};
    }}
    QPushButton {{
        background: {t.INK_800}; border: 1px solid {t.LINE}; border-radius: 8px; padding: 7px 14px; color: {t.TEXT};
    }}
    QPushButton:hover {{ background: {t.INK_750}; border-color: rgba(255,255,255,0.16); }}
    QPushButton:disabled {{ color: {t.TEXT_3}; }}
    QPushButton#danger {{ color: {t.DANGER}; }}
    QPushButton#primary {{
        background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {t.TYRIAN}, stop:0.55 {t.ROSE}, stop:1 {t.EMBER});
        border: none; color: white; font-weight: 600; padding: 8px 22px;
    }}
    QPushButton#hotkey {{
        font-weight: 600; padding: 7px 16px; min-width: 150px; background: {t.INK_800};
        border: 1px solid rgba(255,255,255,0.14);
    }}
    QPushButton#hotkey[capturing="true"] {{ border: 1px solid {t.ROSE}; color: {t.ROSE}; }}
    QWidget#segmented {{ background: {t.INK_800}; border: 1px solid {t.LINE}; border-radius: 9px; }}
    QPushButton#segment {{ border: none; border-radius: 7px; padding: 6px 14px; background: transparent;
        color: {t.TEXT_2}; }}
    QPushButton#segment:checked {{ background: {t.INK_700}; color: {t.TEXT}; font-weight: 600; }}
    QPushButton#link {{ background: transparent; border: none; color: {t.TEXT_2}; padding: 6px 4px; }}
    QPushButton#link:hover {{ color: {t.TEXT}; }}
    QTableWidget, QListWidget#terms {{
        background: {t.INK_800}; border: 1px solid {t.LINE}; border-radius: 8px; gridline-color: {t.LINE};
        selection-background-color: {t.INK_700}; selection-color: {t.TEXT}; outline: none;
    }}
    QHeaderView::section {{ background: {t.INK_850}; color: {t.TEXT_3}; border: none; padding: 6px 8px;
        font-size: 11px; }}
    QTableWidget QLineEdit {{ background: {t.INK_750}; border: 1px solid {t.ROSE}; padding: 2px 4px; }}
    QScrollBar:vertical {{ background: transparent; width: 8px; margin: 4px 0; }}
    QScrollBar::handle:vertical {{ background: {t.INK_700}; border-radius: 4px; min-height: 30px; }}
    QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
    """


class SettingsWindow(QWidget):
    def __init__(self, app: TiroApp):
        super().__init__(None)
        self.app = app
        self.setObjectName("root")
        self.setWindowTitle(f"{APP_NAME} Settings")
        self.setWindowIcon(make_icon("app"))
        self.setStyleSheet(_qss())
        self.setMinimumSize(780, 560)
        self.resize(860, 680)

        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        side = QWidget()
        side.setObjectName("sidebar")
        side.setFixedWidth(214)
        sl = QVBoxLayout(side)
        sl.setContentsMargins(14, 18, 6, 14)
        sl.setSpacing(10)
        brand = QHBoxLayout()
        brand.setSpacing(10)
        brand.addWidget(Mark(34))
        names = QVBoxLayout()
        names.setSpacing(0)
        name = QLabel(APP_NAME)
        name.setObjectName("title")
        name.setFont(theme.font(15, QFont.Weight.DemiBold))
        self.subtitle = QLabel()
        self.subtitle.setObjectName("subtitle")
        self.subtitle.setWordWrap(True)
        names.addWidget(name)
        names.addWidget(self.subtitle)
        brand.addLayout(names, 1)
        sl.addLayout(brand)
        sl.addSpacing(6)
        self.nav = QListWidget()
        self.nav.setObjectName("nav")
        self.nav.setCursor(Qt.CursorShape.PointingHandCursor)
        self.nav.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.nav.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        for key, label in PAGES:
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, key)
            item.setSizeHint(QSize(140, 36))
            self.nav.addItem(item)
        sl.addWidget(self.nav, 1)
        done = QPushButton("Done")
        done.setObjectName("primary")
        done.setCursor(Qt.CursorShape.PointingHandCursor)
        done.clicked.connect(self.close)
        sl.addWidget(done)
        outer.addWidget(side)

        self.stack = QStackedWidget()
        outer.addWidget(self.stack, 1)
        self._pages: dict[str, int] = {}
        for key, label in PAGES:
            body, lay = self._page(label)
            getattr(self, f"_build_{key}")(lay)
            lay.addStretch(1)
            self._pages[key] = self.stack.addWidget(body)
        self.nav.currentRowChanged.connect(self.stack.setCurrentIndex)
        self.nav.setCurrentRow(0)

        self._stats_timer = QTimer(self)
        self._stats_timer.setInterval(1000)
        self._stats_timer.timeout.connect(self._refresh_stats)
        self.refresh()

    def show_page(self, key: str) -> None:
        if key in self._pages:
            self.nav.setCurrentRow(self._pages[key])

    # ================================================================== pages
    def _build_general(self, lay) -> None:
        s = self.app.settings
        lay.addWidget(section_title("Shortcut"))
        _card, grid = self._card(lay)
        self.hotkey_btn = HotkeyButton(s.hotkey_spec.label, lambda: self._capture("hotkey", self.hotkey_btn))
        self._row(grid, 0, "Dictation key", self.hotkey_btn, "Click, then press the key or combination to use.")
        self.mode = Segmented([("hold", "Hold to talk"), ("toggle", "Press to toggle")], s.mode)
        self.mode.changed.connect(lambda v: self._set("mode", v))
        self._row(grid, 1, "Mode", self.mode)
        self.double_tap = Toggle(s.double_tap_lock)
        self.double_tap.toggled.connect(lambda v: self._set("double_tap_lock", v))
        self._row(grid, 2, "Double-tap for hands-free", self.double_tap,
                  "In hold mode, double-tap the key to keep listening; tap again to finish.")
        self.fix_btn = HotkeyButton(self._fix_label(), lambda: self._capture("fix_hotkey", self.fix_btn))
        self._row(grid, 3, "Fix last transcription", self.fix_btn,
                  "Opens your last dictation so you can correct a misheard word. Tiro learns the fix.")

        lay.addWidget(section_title("Microphone"))
        _card, grid = self._card(lay)
        self.mic = Combo()
        self.mic.currentIndexChanged.connect(lambda _i: self._set("microphone", self.mic.currentData()))
        test = QPushButton("Test")
        test.setCursor(Qt.CursorShape.PointingHandCursor)
        test.clicked.connect(self._test_mic)
        mic_row = QHBoxLayout()
        mic_row.setSpacing(8)
        mic_row.setContentsMargins(0, 0, 0, 0)
        mic_row.addWidget(self.mic, 1)
        mic_row.addWidget(test)
        holder = QWidget()
        holder.setLayout(mic_row)
        label = QLabel("Input")
        label.setObjectName("rowlabel")
        grid.addWidget(label, 0, 0, 1, 2)
        grid.addWidget(holder, 1, 0, 1, 2)
        self.meter = LevelMeter()
        self.meter.setVisible(False)
        grid.addWidget(self.meter, 2, 0, 1, 2)
        self.mic_hint = QLabel()
        self.mic_hint.setObjectName("hint")
        self.mic_hint.setVisible(False)
        grid.addWidget(self.mic_hint, 3, 0, 1, 2)
        self.instant = Toggle(s.instant_start)
        self.instant.toggled.connect(lambda v: self._set("instant_start", v))
        self._row(grid, 4, "Instant start", self.instant,
                  "Keeps the microphone open so your first word is never cut off. Audio is only held for a "
                  "second and never saved.")
        self._mic_timer = QTimer(self)
        self._mic_timer.setInterval(33)
        self._mic_timer.timeout.connect(self._poll_mic)
        self._mic_capture = None
        self._mic_ticks = 0

        lay.addWidget(section_title("Text"))
        _card, grid = self._card(lay)
        self.insertion = Segmented([("type", "Type it"), ("paste", "Paste it")], s.insertion)
        self.insertion.changed.connect(lambda v: self._set("insertion", v))
        self._row(grid, 0, "Insert text by", self.insertion,
                  "Typing never touches your clipboard. Pasting is instant; your clipboard is restored.")
        self.fillers = Toggle(s.remove_fillers)
        self.fillers.toggled.connect(lambda v: self._set("remove_fillers", v))
        self._row(grid, 1, "Remove filler words (um, uh)", self.fillers)
        self.commands = Toggle(s.voice_commands)
        self.commands.toggled.connect(lambda v: self._set("voice_commands", v))
        self._row(grid, 2, "Voice commands", self.commands, "Say “new line” or “new paragraph”.")
        self.spacing = Toggle(s.smart_spacing)
        self.spacing.toggled.connect(lambda v: self._set("smart_spacing", v))
        self._row(grid, 3, "Smart spacing and casing", self.spacing,
                  "Joins dictation onto what you just typed, with the right space and capitalisation.")

        lay.addWidget(section_title("App"))
        _card, grid = self._card(lay)
        self.autostart = Toggle(self.app.autostart_enabled())
        self.autostart.toggled.connect(self.app.set_autostart)
        self._row(grid, 0, "Launch at login" if IS_MAC else "Start with Windows", self.autostart)
        self.sounds = Toggle(s.sounds)
        self.sounds.toggled.connect(lambda v: self._set("sounds", v))
        self._row(grid, 1, "Sound effects", self.sounds)
        self.live = Toggle(s.show_live_text)
        self.live.toggled.connect(lambda v: self._set("show_live_text", v))
        self._row(grid, 2, "Show live words in the overlay", self.live)
        self.position = Segmented([("bottom", "Bottom"), ("top", "Top")], s.overlay_position)
        self.position.changed.connect(lambda v: self._set("overlay_position", v))
        self._row(grid, 3, "Overlay position", self.position)

    def _build_recognition(self, lay) -> None:
        s = self.app.settings
        lay.addWidget(section_title("Speech model"))
        _card, grid = self._card(lay)
        self.model = Combo()
        for key, spec in MODELS.items():
            self.model.addItem(f"{spec.title} — {spec.subtitle}", key)
        self.model.setCurrentIndex(max(0, self.model.findData(s.model)))
        self.model.currentIndexChanged.connect(self._model_changed)
        self._row(grid, 0, "Model", self.model)
        self.device = Combo()
        gpu = "Apple GPU / Neural Engine" if IS_MAC else "NVIDIA GPU (CUDA)"
        for key, label in (("auto", "Automatic (GPU when available)"), ("cuda", gpu), ("cpu", "CPU only")):
            self.device.addItem(label, key)
        self.device.setCurrentIndex(max(0, self.device.findData(s.device)))
        self.device.currentIndexChanged.connect(lambda _i: self._set("device", self.device.currentData()))
        self._row(grid, 1, "Processor", self.device)
        self.engine_hint = QLabel()
        self.engine_hint.setObjectName("hint")
        self.engine_hint.setWordWrap(True)
        grid.addWidget(self.engine_hint, 2, 0, 1, 2)

    def _build_accuracy(self, lay) -> None:
        s = self.app.settings
        intro = QLabel(
            "Tiro double-checks words it may have misheard, using your dictionary, the fixes you've taught it "
            "and what you usually say. It only ever swaps a misheard word or name for one that sounds like "
            "it. It never rewords, reorders, adds or removes anything, or touches your grammar or slang."
        )
        intro.setObjectName("hint")
        intro.setWordWrap(True)
        lay.addWidget(intro)
        lay.addWidget(section_title("Correction"))
        _card, grid = self._card(lay)
        self.correct_on = Toggle(s.correction)
        self.correct_on.toggled.connect(lambda v: self._set("correction", v))
        self._row(grid, 0, "Fix misheard words", self.correct_on)
        self.correct_mode = Segmented([("strict", "Strict"), ("balanced", "Balanced"), ("aggressive", "Aggressive")],
                                      s.correction_mode)
        self.correct_mode.changed.connect(lambda v: self._set("correction_mode", v))
        self._row(grid, 1, "How bold", self.correct_mode,
                  "Strict changes a word only when the evidence is overwhelming; aggressive fixes more, "
                  "including closer calls.")
        self.threshold = Combo()
        for value, label in ((0.0, "Automatic (set by the mode)"), (0.95, "0.95 — check almost every word"),
                             (0.9, "0.90"), (0.8, "0.80"), (0.7, "0.70"), (0.6, "0.60 — only clear doubts")):
            self.threshold.addItem(label, value)
        self.threshold.setCurrentIndex(max(0, self.threshold.findData(s.correction_threshold)))
        self.threshold.currentIndexChanged.connect(lambda _i: self._set("correction_threshold",
                                                                        float(self.threshold.currentData())))
        self._row(grid, 2, "Confidence threshold", self.threshold,
                  "Words the recogniser is less sure of than this get a closer look.")

        lay.addWidget(section_title("AI check"))
        _card, grid = self._card(lay)
        self.ai_on = Toggle(s.ai_correction)
        self.ai_on.toggled.connect(lambda v: self._set("ai_correction", v))
        self._row(grid, 0, "Use a small local AI for hard cases", self.ai_on,
                  "For words the dictionary can't settle (“Rust” or “rust”?), a small language model "
                  "running on this computer picks the option that fits your sentence. It can only choose between "
                  "candidates or keep what you said.")
        self.ai_model = Combo()
        self.ai_model.addItem("Automatic (best for this computer)", "auto")
        for key, spec in LANGUAGE_MODELS.items():
            self.ai_model.addItem(f"{spec.title} — {spec.subtitle}", key)
        self.ai_model.setCurrentIndex(max(0, self.ai_model.findData(s.ai_model)))
        self.ai_model.currentIndexChanged.connect(self._ai_model_changed)
        self._row(grid, 1, "Model", self.ai_model)
        self.ai_status = QLabel()
        self.ai_status.setObjectName("hint")
        self.ai_status.setWordWrap(True)
        grid.addWidget(self.ai_status, 2, 0, 1, 2)
        self.budget = Combo()
        for ms in (100, 150, 250, 400):
            self.budget.addItem(f"{ms} ms" + (" (default)" if ms == 150 else ""), ms)
        self.budget.setCurrentIndex(max(0, self.budget.findData(s.correction_budget_ms)))
        self.budget.currentIndexChanged.connect(lambda _i: self._set("correction_budget_ms", int(self.budget.currentData())))
        self._row(grid, 3, "Time limit", self.budget,
                  "Text is never held back longer than this. If a check runs late, your words are typed as heard.")
        self.late = Toggle(s.late_fixes)
        self.late.toggled.connect(lambda v: self._set("late_fixes", v))
        self._row(grid, 4, "Fix late words in place", self.late,
                  "If a check finishes after the words were typed, swap them, but only if you haven't typed, "
                  "clicked or switched windows since.")

        lay.addWidget(section_title("Latency"))
        _card, grid = self._card(lay)
        self.debug_on = Toggle(s.show_latency)
        self.debug_on.toggled.connect(self._toggle_stats)
        self._row(grid, 0, "Show latency details", self.debug_on, "How long correction adds, measured on every dictation.")
        self.stats = QLabel()
        self.stats.setObjectName("stat")
        self.stats.setWordWrap(True)
        self.stats.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        grid.addWidget(self.stats, 1, 0, 1, 2)

    def _build_dictionary(self, lay) -> None:
        s = self.app.settings
        lay.addWidget(section_title("Your dictionary"))
        _card, grid = self._card(lay)
        intro = QLabel(
            "Names, brands and jargon Tiro should know, one per line, spelled the way you want them typed. "
            "For anything stubborn, add a rule like  super bass -> Supabase."
        )
        intro.setObjectName("hint")
        intro.setWordWrap(True)
        grid.addWidget(intro, 0, 0, 1, 2)
        self.dictionary = QPlainTextEdit()
        self.dictionary.setObjectName("dictionary")
        self.dictionary.setPlaceholderText("GeoGuessr\nKubernetes\nSiobhan\nsuper bass -> Supabase")
        self.dictionary.setFixedHeight(120)
        self.dictionary.setPlainText("\n".join(s.dictionary))
        self._dict_timer = QTimer(self)
        self._dict_timer.setSingleShot(True)
        self._dict_timer.setInterval(700)
        self._dict_timer.timeout.connect(self._save_dictionary)
        self.dictionary.textChanged.connect(self._dict_timer.start)
        grid.addWidget(self.dictionary, 1, 0, 1, 2)

        lay.addWidget(section_title("Fixes Tiro learned"))
        _card, grid = self._card(lay)
        hint = QLabel("From “Fix last transcription” and from words you retyped. Double-click to edit.")
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        grid.addWidget(hint, 0, 0, 1, 2)
        self.fixes = QTableWidget(0, 3)
        self.fixes.setHorizontalHeaderLabels(["Heard", "Now typed as", "Times"])
        self.fixes.verticalHeader().setVisible(False)
        self.fixes.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.fixes.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.fixes.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.fixes.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.fixes.setEditTriggers(QAbstractItemView.EditTrigger.DoubleClicked)
        self.fixes.setFixedHeight(150)
        self.fixes.itemChanged.connect(self._fix_edited)
        grid.addWidget(self.fixes, 1, 0, 1, 2)
        row = QHBoxLayout()
        delete = QPushButton("Delete selected")
        delete.clicked.connect(self._delete_fixes)
        row.addWidget(delete)
        row.addStretch(1)
        self.learn = Toggle(s.learn_corrections)
        self.learn.toggled.connect(lambda v: self._set("learn_corrections", v))
        lbl = QLabel("Learn from words I retype")
        row.addWidget(lbl)
        row.addWidget(self.learn)
        holder = QWidget()
        holder.setLayout(row)
        row.setContentsMargins(0, 0, 0, 0)
        grid.addWidget(holder, 2, 0, 1, 2)

        lay.addWidget(section_title("Words from your history"))
        _card, grid = self._card(lay)
        hint = QLabel("Names and terms you use often. They count as evidence when Tiro isn't sure what it heard.")
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        grid.addWidget(hint, 0, 0, 1, 2)
        self.terms = QListWidget()
        self.terms.setObjectName("terms")
        self.terms.setFixedHeight(130)
        self.terms.setFlow(QListWidget.Flow.LeftToRight)
        self.terms.setWrapping(True)
        self.terms.setSpacing(4)
        grid.addWidget(self.terms, 1, 0, 1, 2)
        export = QPushButton("Export everything Tiro learned…")
        export.clicked.connect(self._export)
        grid.addWidget(export, 2, 0, 1, 2, alignment=Qt.AlignmentFlag.AlignLeft)

    def _build_privacy(self, lay) -> None:
        s = self.app.settings
        card = QFrame()
        card.setObjectName("promisecard")
        cl = QVBoxLayout(card)
        cl.setContentsMargins(16, 14, 16, 14)
        promise = QLabel(PRIVACY_PROMISE)
        promise.setObjectName("promise")
        promise.setWordWrap(True)
        cl.addWidget(promise)
        lay.addWidget(card)

        lay.addWidget(section_title("Learning from what you say"))
        _card, grid = self._card(lay)
        self.history_on = Toggle(s.history)
        self.history_on.toggled.connect(lambda v: self._set("history", v))
        self._row(grid, 0, "Learn from my dictation", self.history_on,
                  "Tiro keeps your past dictations on this computer to learn the names and words you use. "
                  "Switch off to stop saving; existing history is no longer used.")
        self.history_stat = QLabel()
        self.history_stat.setObjectName("stat")
        grid.addWidget(self.history_stat, 1, 0)
        clear = QPushButton("Clear history")
        clear.setObjectName("danger")
        clear.clicked.connect(self._clear_history)
        grid.addWidget(clear, 1, 1, alignment=Qt.AlignmentFlag.AlignRight)
        note = QLabel("Password fields and other secure fields are never learned from, saved or corrected.")
        note.setObjectName("hint")
        note.setWordWrap(True)
        grid.addWidget(note, 2, 0, 1, 2)

        lay.addWidget(section_title("Your data"))
        _card, grid = self._card(lay)
        forget = QPushButton("Forget everything learned")
        forget.setObjectName("danger")
        forget.clicked.connect(self._forget_all)
        self._row(grid, 0, "History and learned fixes", forget,
                  "Deletes your dictation history and every fix Tiro learned. Your dictionary stays.")
        folder = QPushButton("Open data folder")
        folder.clicked.connect(self.app.open_data_folder)
        self._row(grid, 1, "Where it's stored", folder, str(self.app.data_folder()))

    def _build_about(self, lay) -> None:
        _card, grid = self._card(lay)
        head = QHBoxLayout()
        head.addWidget(Mark(46))
        t = QVBoxLayout()
        name = QLabel(f"{APP_NAME}  v{__version__}")
        name.setObjectName("title")
        tag = QLabel("Fast, private dictation that runs entirely on your computer.")
        tag.setObjectName("subtitle")
        t.addWidget(name)
        t.addWidget(tag)
        head.addLayout(t, 1)
        holder = QWidget()
        holder.setLayout(head)
        head.setContentsMargins(0, 0, 0, 0)
        grid.addWidget(holder, 0, 0, 1, 2)
        updates = QPushButton("Check for updates")
        updates.clicked.connect(self.app.check_for_updates)
        self._row(grid, 1, "Updates", updates, "Asks GitHub for the latest version. Nothing about you is sent.")
        logs = QPushButton("Open log folder")
        logs.clicked.connect(self.app.open_logs)
        self._row(grid, 2, "Logs", logs, "Logs never contain what you dictate.")
        if not IS_MAC and not winutil.is_elevated():
            admin = QPushButton("Restart as administrator")
            admin.clicked.connect(self.app.restart_as_admin)
            self._row(grid, 3, "Admin windows", admin, "Needed only to type into apps that run as administrator.")
        credits = QLabel(
            "Speech: NVIDIA Parakeet TDT (CC-BY-4.0) · Voice detection: Silero VAD (MIT) · AI check: "
            "Qwen2.5 (Apache-2.0) · Word lists: wordfreq (CC-BY-SA 4.0), CMU Pronouncing Dictionary (BSD)"
        )
        credits.setObjectName("hint")
        credits.setWordWrap(True)
        lay.addWidget(credits)

    # ================================================================== layout helpers
    def _page(self, title: str):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        body = QWidget()
        scroll.setWidget(body)
        lay = QVBoxLayout(body)
        lay.setContentsMargins(28, 22, 28, 18)
        lay.setSpacing(10)
        head = QLabel(title)
        head.setObjectName("pagetitle")
        head.setFont(theme.font(20, QFont.Weight.DemiBold))
        lay.addWidget(head)
        return scroll, lay

    def _card(self, parent_layout):
        card = QFrame()
        card.setObjectName("card")
        grid = QGridLayout(card)
        grid.setContentsMargins(16, 12, 16, 12)
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(12)
        grid.setColumnStretch(0, 1)
        parent_layout.addWidget(card)
        return card, grid

    def _row(self, grid: QGridLayout, row: int, label: str, control: QWidget, hint: str | None = None) -> None:
        box = QVBoxLayout()
        box.setSpacing(2)
        lbl = QLabel(label)
        lbl.setObjectName("rowlabel")
        box.addWidget(lbl)
        if hint:
            h = QLabel(hint)
            h.setObjectName("hint")
            h.setWordWrap(True)
            h.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
            box.addWidget(h)
        w = QWidget()
        w.setLayout(box)
        box.setContentsMargins(0, 0, 0, 0)
        grid.addWidget(w, row, 0)
        grid.addWidget(control, row, 1, alignment=Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

    # ================================================================== state
    def refresh(self) -> None:
        s = self.app.settings
        self.subtitle.setText(self.app.status_line())
        self.engine_hint.setText(self.app.engine_description())
        self.hotkey_btn.set_label(s.hotkey_spec.label)
        self.fix_btn.set_label(self._fix_label())
        self.mode.set_value(s.mode)
        self.double_tap.setEnabled(s.mode == "hold")
        for toggle, value in ((self.autostart, self.app.autostart_enabled()), (self.ai_on, s.ai_correction),
                              (self.correct_on, s.correction), (self.history_on, s.history)):
            toggle.blockSignals(True)
            toggle.setChecked(value)
            toggle.blockSignals(False)
        self.correct_mode.set_value(s.correction_mode)
        for combo, value in ((self.ai_model, s.ai_model), (self.threshold, s.correction_threshold),
                             (self.budget, s.correction_budget_ms)):
            combo.blockSignals(True)
            combo.setCurrentIndex(max(0, combo.findData(value)))
            combo.blockSignals(False)
        for w in (self.correct_mode, self.threshold, self.ai_on, self.budget, self.late):
            w.setEnabled(s.correction)
        self.ai_model.setEnabled(s.correction and s.ai_correction)
        svc = self.app.correction
        self.ai_status.setText(self._ai_status_text())
        n = svc.history_count()
        self.history_stat.setText(f"{n} dictation{'s' if n != 1 else ''} stored on this computer" if s.history
                                  else "Not learning from new dictations")
        self._fill_mics()
        self._sync_dictionary()
        self._fill_fixes()
        self._fill_terms()
        self._refresh_stats()

    def _ai_status_text(self) -> str:
        s = self.app.settings
        if not s.correction:
            return "Correction is off."
        if not s.ai_correction:
            return "Off. Your dictionary, learned fixes and history still correct words."
        return f"Status: {self.app.correction.language_status}"

    def _fix_label(self) -> str:
        spec = self.app._fix_spec()
        return spec.label if spec is not None else "Not set"

    def _fill_mics(self) -> None:
        self.mic.blockSignals(True)
        self.mic.clear()
        default = self.app.default_mic_name()
        self.mic.addItem(f"System default{f' ({default})' if default else ''}", None)
        for name in self.app.list_mics():
            self.mic.addItem(name, name)
        idx = self.mic.findData(self.app.settings.microphone) if self.app.settings.microphone else 0
        self.mic.setCurrentIndex(max(0, idx))
        self.mic.blockSignals(False)

    def _fill_fixes(self) -> None:
        if self.fixes.state() == QAbstractItemView.State.EditingState:
            return
        self.fixes.blockSignals(True)
        rows = self.app.correction.corrections()
        self.fixes.setRowCount(len(rows))
        for r, c in enumerate(rows):
            heard = QTableWidgetItem(c.heard)
            heard.setData(Qt.ItemDataRole.UserRole, c.heard)
            self.fixes.setItem(r, 0, heard)
            self.fixes.setItem(r, 1, QTableWidgetItem(c.written))
            times = QTableWidgetItem(str(c.count))
            times.setFlags(times.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.fixes.setItem(r, 2, times)
        self.fixes.blockSignals(False)

    def _fill_terms(self) -> None:
        self.terms.clear()
        terms = self.app.correction.history_terms()[:200]
        if not terms:
            self.terms.addItem("Nothing yet. Tiro learns as you dictate." if self.app.settings.history
                               else "Learning from dictation is off.")
            return
        for written, count in terms:
            self.terms.addItem(f"{written}  · {count}")

    def _toggle_stats(self, on: bool) -> None:
        self._set("show_latency", on)

    def _refresh_stats(self) -> None:
        show = self.app.settings.show_latency
        self.stats.setVisible(show)
        if not show:
            self._stats_timer.stop()
            return
        if not self._stats_timer.isActive() and self.isVisible():
            self._stats_timer.start()
        m = self.app.correction.metrics.summary()
        lm = self.app.correction.corrector.language
        lines = []
        if m["ends"]:
            lines.append(f"End of speech → text typed: median {m['end_median_ms']:.0f} ms with correction, "
                         f"{m['end_without_ms']:.0f} ms without (p95 {m['end_p95_ms']:.0f} ms, last {m['ends']} dictations)")
        if m["commits"]:
            lines.append(f"Correction added per commit: median {m['median_ms']:.1f} ms, p95 {m['p95_ms']:.1f} ms "
                         f"over {m['commits']} commits")
            lines.append(f"Passed untouched by the quick check: {m['tier0_share'] * 100:.0f}% · asked the AI: "
                         f"{m['tier2_share'] * 100:.0f}% · words changed: {m['changes']}")
        if lm is not None and lm.avg_ms is not None:
            lines.append(f"AI check ({lm.name} on {lm.device}): {lm.avg_ms:.0f} ms per check, {lm.calls} checks")
        self.stats.setText("\n".join(lines) or "Dictate something to see measurements here.")

    def _set(self, key: str, value) -> None:
        self.app.update_setting(key, value)
        self.refresh()

    # ================================================================== actions
    def _save_dictionary(self) -> None:
        lines = [ln.strip() for ln in self.dictionary.toPlainText().splitlines() if ln.strip()]
        if lines != self.app.settings.dictionary:
            self.app.update_setting("dictionary", lines)

    def _sync_dictionary(self) -> None:
        """Show words learned while the window was open, unless the user is editing the list."""
        if self.dictionary.hasFocus() or self._dict_timer.isActive():
            return
        text = "\n".join(self.app.settings.dictionary)
        if self.dictionary.toPlainText() != text:
            self.dictionary.blockSignals(True)
            self.dictionary.setPlainText(text)
            self.dictionary.blockSignals(False)

    def _fix_edited(self, item: QTableWidgetItem) -> None:
        r = item.row()
        old = self.fixes.item(r, 0).data(Qt.ItemDataRole.UserRole)
        heard, written = self.fixes.item(r, 0).text().strip(), self.fixes.item(r, 1).text().strip()
        if heard and written:
            self.app.correction.update_correction(old, heard, written)
        QTimer.singleShot(0, self._fill_fixes)

    def _delete_fixes(self) -> None:
        rows = sorted({i.row() for i in self.fixes.selectedItems()}, reverse=True)
        for r in rows:
            self.app.correction.delete_correction(self.fixes.item(r, 0).data(Qt.ItemDataRole.UserRole))
        self._fill_fixes()

    def _export(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Export what Tiro learned", "tiro-learned.json",
                                              "JSON (*.json);;CSV (*.csv)")
        if path:
            self.app.correction.export(path)

    def _confirm(self, title: str, text: str) -> bool:
        box = QMessageBox(self)
        box.setWindowTitle(title)
        box.setText(text)
        box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel)
        box.setDefaultButton(QMessageBox.StandardButton.Cancel)
        return box.exec() == QMessageBox.StandardButton.Yes

    def _clear_history(self) -> None:
        if self._confirm("Clear history?", "Delete every saved dictation? Learned fixes and your dictionary stay."):
            self.app.correction.clear_history()
            QTimer.singleShot(400, self.refresh)

    def _forget_all(self) -> None:
        if self._confirm("Forget everything?", "Delete your dictation history and all learned fixes?"):
            self.app.correction.forget_everything()
            QTimer.singleShot(400, self.refresh)

    def _model_changed(self, _i: int) -> None:
        key = self.model.currentData()
        spec = MODELS[key]
        if not is_installed(spec) and not self._confirm(
                "Download speech model?",
                f"{spec.title} ({spec.subtitle}) isn't installed yet. Download it now? It's about "
                f"{spec.size_gb:.1f} GB from Hugging Face."):
            self.model.blockSignals(True)
            self.model.setCurrentIndex(max(0, self.model.findData(self.app.settings.model)))
            self.model.blockSignals(False)
            return
        self._set("model", key)

    def _ai_model_changed(self, _i: int) -> None:
        key = self.ai_model.currentData()
        spec = LANGUAGE_MODELS.get(key)
        if spec is not None and find_model(spec) is None and not self._confirm(
                "Download AI model?", f"{spec.title} isn't installed yet. Download it now? It's about "
                f"{spec.size_gb:.1f} GB from Hugging Face, and runs only on this computer."):
            self.ai_model.blockSignals(True)
            self.ai_model.setCurrentIndex(max(0, self.ai_model.findData(self.app.settings.ai_model)))
            self.ai_model.blockSignals(False)
            return
        self._set("ai_model", key)

    def _capture(self, key: str, button: HotkeyButton) -> None:
        if button.capturing:
            return
        button.set_capturing(True)

        def done(spec):
            button.set_capturing(False)
            if spec is not None:
                self._set(key, str(spec))

        self.app.capture_hotkey(done)

    def _test_mic(self) -> None:
        from tiro.audio import AudioCapture, MicError

        if self._mic_capture is not None:
            return
        cap = AudioCapture(self.app.settings.microphone)
        try:
            cap.open()
        except MicError as exc:
            self.mic_hint.setText(str(exc))
            self.mic_hint.setVisible(True)
            return
        self.mic_hint.setText("Say something — the bar should move.")
        self.mic_hint.setVisible(True)
        self._mic_capture = cap
        self._mic_ticks = 0
        self.meter.setVisible(True)
        self._mic_timer.start()

    def _poll_mic(self) -> None:
        cap = self._mic_capture
        if cap is None:
            return
        cap.drain()
        self.meter.set_level(cap.level)
        self._mic_ticks += 1
        if self._mic_ticks > 150:  # ~5 s
            self._stop_mic_test()

    def _stop_mic_test(self) -> None:
        self._mic_timer.stop()
        if self._mic_capture is not None:
            self._mic_capture.close()
            self._mic_capture = None
        self.meter.setVisible(False)
        self.mic_hint.setVisible(False)

    def showEvent(self, e) -> None:
        super().showEvent(e)
        if not IS_MAC:
            winutil.dark_title_bar(int(self.winId()))
        self.refresh()

    def closeEvent(self, e) -> None:
        self._stop_mic_test()
        self._stats_timer.stop()
        if self._dict_timer.isActive():
            self._dict_timer.stop()
            self._save_dictionary()
        super().closeEvent(e)
