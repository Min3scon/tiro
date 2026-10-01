"""First-run setup wizard (Windows and macOS): your computer, downloads, a real benchmark, permissions (Mac),
microphone, shortcut, learning, your words, and a live test. It drives the running app, so the benchmark
and the test use the real engine and hotkey."""

from __future__ import annotations

import logging
import sys
import threading
import time
from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from tiro import APP_NAME, winutil
from tiro.ui import theme
from tiro.ui.icons import make_icon
from tiro.ui.settings import PRIVACY_PROMISE, _qss
from tiro.ui.widgets import Combo, HotkeyButton, LevelMeter, Mark, Segmented, Toggle

if TYPE_CHECKING:
    from tiro.app import TiroApp

log = logging.getLogger(__name__)
IS_MAC = sys.platform == "darwin"


class _Signals(QObject):
    progress = Signal(str, float, str)  # item key, fraction, text
    item_done = Signal(str, bool, str)  # item key, ok, message
    all_done = Signal(bool)
    bench = Signal(str, object)  # step, result


def _wizard_qss() -> str:
    t = theme
    return _qss() + f"""
    QWidget#rail {{ background: {t.INK_950}; border-right: 1px solid {t.LINE}; }}
    QLabel#step {{ color: {t.TEXT_3}; padding: 6px 4px; }}
    QLabel#step[current="true"] {{ color: {t.TEXT}; font-weight: 600; }}
    QLabel#step[done="true"] {{ color: {t.TEXT_2}; }}
    QLabel#stepmark {{ color: {t.TEXT_3}; padding: 0; font-size: 12px; }}
    QLabel#stepmark[current="true"] {{ color: {t.ROSE}; }}
    QLabel#stepmark[done="true"] {{ color: {t.OK}; }}
    QLabel#big {{ font-size: 24px; font-weight: 600; }}
    QLabel#lead {{ color: {t.TEXT_2}; font-size: 14px; }}
    QLabel#good {{ color: {t.OK}; }}
    QLabel#warn {{ color: {t.EMBER}; }}
    QProgressBar {{ background: {t.INK_800}; border: none; border-radius: 4px; height: 8px; text-align: center;
        color: transparent; }}
    QProgressBar::chunk {{ border-radius: 4px; background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 {t.TYRIAN}, stop:0.55 {t.ROSE}, stop:1 {t.EMBER}); }}
    QPlainTextEdit#trybox {{ background: {t.INK_800}; border: 1px solid rgba(255,255,255,0.14); border-radius: 10px;
        padding: 10px; font-size: 15px; }}
    QPlainTextEdit#trybox:focus {{ border-color: {t.ROSE}; }}
    """


class SetupWizard(QWidget):
    def __init__(self, app: TiroApp, installed: bool = False):
        super().__init__(None)
        self.app = app
        self.installed = installed  # the Windows installer already chose the setup and fetched the speech model
        self.sig = _Signals()
        self.setObjectName("root")
        self.setWindowTitle(f"Set up {APP_NAME}")
        self.setWindowIcon(make_icon("app"))
        self.setStyleSheet(_wizard_qss())
        self.setMinimumSize(860, 600)
        self.resize(900, 640)
        from tiro.setup.hardware import detect, recommend

        self.hw = detect()
        self.plan = recommend(self.hw)
        self.pages: list[tuple[str, str, QWidget]] = []
        steps = [("welcome", "Welcome"), ("computer", "Your Mac" if IS_MAC else "Your PC"),
                 ("download", "Download"), ("bench", "Speed check")]
        if IS_MAC:
            steps.append(("permissions", "Permissions"))
        steps += [("mic", "Microphone"), ("hotkey", "Shortcut"), ("learning", "Learning"), ("words", "Your words"),
                  ("test", "Try it")]
        if installed:
            steps = [s for s in steps if s[0] != "computer"]

        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        rail = QWidget()
        rail.setObjectName("rail")
        rail.setFixedWidth(210)
        rl = QVBoxLayout(rail)
        rl.setContentsMargins(20, 22, 12, 18)
        rl.setSpacing(2)
        brand = QHBoxLayout()
        brand.addWidget(Mark(32))
        name = QLabel(APP_NAME)
        name.setFont(theme.font(16, QFont.Weight.DemiBold))
        brand.addWidget(name, 1)
        rl.addLayout(brand)
        rl.addSpacing(18)
        self.step_labels: dict[str, QLabel] = {}
        self.step_marks: dict[str, QLabel] = {}
        for key, label in steps:
            row = QHBoxLayout()
            row.setSpacing(4)
            mark = QLabel("")
            mark.setObjectName("stepmark")
            mark.setFixedWidth(20)
            lbl = QLabel(label)
            lbl.setObjectName("step")
            row.addWidget(mark)
            row.addWidget(lbl, 1)
            rl.addLayout(row)
            self.step_labels[key] = lbl
            self.step_marks[key] = mark
        rl.addStretch(1)
        outer.addWidget(rail)

        right = QVBoxLayout()
        right.setContentsMargins(36, 30, 36, 24)
        self.stack = QStackedWidget()
        right.addWidget(self.stack, 1)
        nav = QHBoxLayout()
        self.back = QPushButton("Back")
        self.back.clicked.connect(lambda: self._go(self.index - 1))
        nav.addWidget(self.back)
        nav.addStretch(1)
        self.next = QPushButton("Next")
        self.next.setObjectName("primary")
        self.next.setCursor(Qt.CursorShape.PointingHandCursor)
        self.next.clicked.connect(self._next)
        nav.addWidget(self.next)
        right.addLayout(nav)
        holder = QWidget()
        holder.setLayout(right)
        outer.addWidget(holder, 1)

        for key, label in steps:
            page = getattr(self, f"_page_{key}")()
            self.pages.append((key, label, page))
            self.stack.addWidget(page)
        self.index = 0
        self._mic_cap = None
        self._mic_timer = QTimer(self)
        self._mic_timer.setInterval(33)
        self._mic_timer.timeout.connect(self._poll_mic)
        self._perm_timer = QTimer(self)
        self._perm_timer.setInterval(1000)
        self._perm_timer.timeout.connect(self._refresh_permissions)
        self.sig.progress.connect(self._on_progress)
        self.sig.item_done.connect(self._on_item_done)
        self.sig.all_done.connect(self._on_downloads_done)
        self.sig.bench.connect(self._on_bench)
        self._go(0)

    # ================================================================== navigation
    @property
    def key(self) -> str:
        return self.pages[self.index][0]

    def _go(self, i: int) -> None:
        i = max(0, min(len(self.pages) - 1, i))
        self._leave(self.key)
        self.index = i
        self.stack.setCurrentIndex(i)
        for n, (k, _l, _p) in enumerate(self.pages):
            for w in (self.step_labels[k], self.step_marks[k]):
                w.setProperty("current", n == i)
                w.setProperty("done", n < i)
                w.style().unpolish(w)
                w.style().polish(w)
            self.step_marks[k].setText("✓" if n < i else "●" if n == i else "")
        self.back.setVisible(i > 0 and self.key not in ("download", "bench"))
        self.next.setText({"welcome": "Get started", "test": "Finish"}.get(self.key, "Next"))
        self.next.setEnabled(True)
        self._enter(self.key)

    def _next(self) -> None:
        if self.key == "computer":
            self._apply_choices()
        elif self.key == "words":
            self._save_words()
        if self.index == len(self.pages) - 1:
            self._finish()
            return
        self._go(self.index + 1)

    def _enter(self, key: str) -> None:
        if key == "download":
            self._start_downloads()
        elif key == "bench":
            self._start_bench()
        elif key == "mic":
            self._start_mic()
        elif key == "permissions":
            self._refresh_permissions()
            self._perm_timer.start()
        elif key == "test":
            self._enter_test_text()
            QTimer.singleShot(100, self.try_box.setFocus)

    def _leave(self, key: str) -> None:
        if key == "mic":
            self._stop_mic()
        elif key == "permissions":
            self._perm_timer.stop()

    # ================================================================== helpers
    def _base(self, title: str, lead: str = ""):
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(12)
        t = QLabel(title)
        t.setObjectName("big")
        t.setWordWrap(True)
        lay.addWidget(t)
        if lead:
            l2 = QLabel(lead)
            l2.setObjectName("lead")
            l2.setWordWrap(True)
            lay.addWidget(l2)
        lay.addSpacing(6)
        return page, lay

    def _card(self, lay) -> QGridLayout:
        card = QFrame()
        card.setObjectName("card")
        grid = QGridLayout(card)
        grid.setContentsMargins(18, 14, 18, 14)
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(10)
        grid.setColumnStretch(1, 1)
        lay.addWidget(card)
        return grid

    @staticmethod
    def _hint(text: str, name: str = "hint") -> QLabel:
        h = QLabel(text)
        h.setObjectName(name)
        h.setWordWrap(True)
        return h

    # ================================================================== pages
    def _page_welcome(self) -> QWidget:
        if self.installed:
            page, lay = self._base("Tiro is installed. Let's make it yours.",
                                   "A few quick steps: check the AI helper, pick your microphone and shortcut, "
                                   "and try it out. It takes about a minute.")
        else:
            page, lay = self._base("Welcome to Tiro",
                                   "Fast, accurate dictation that types wherever your cursor is. Hold a key, speak, "
                                   "let go. Setup takes about two minutes.")
        card = QFrame()
        card.setObjectName("promisecard")
        cl = QVBoxLayout(card)
        cl.setContentsMargins(18, 14, 18, 14)
        cl.addWidget(self._hint(PRIVACY_PROMISE, "promise"))
        cl.addWidget(self._hint("Tiro learns the names and words you use from what you dictate, and keeps that "
                                "on this computer too. You can switch learning off or clear it any time. It never "
                                "learns from password fields."))
        lay.addWidget(card)
        lay.addStretch(1)
        return page

    def _page_computer(self) -> QWidget:
        page, lay = self._base(self.plan.headline, "Tiro checked this computer and picked the best way to run it:")
        grid = self._card(lay)
        for r, (k, v) in enumerate(self.hw.summary):
            grid.addWidget(self._hint(k), r, 0)
            grid.addWidget(QLabel(v), r, 1)
        for reason in self.plan.reasons:
            lay.addWidget(self._hint("•  " + reason, "stat"))
        lay.addWidget(self._hint(f"About {self.plan.download_gb:.1f} GB to download, once.", "stat"))
        adv = QPushButton("Advanced options")
        adv.setObjectName("link")
        adv.setCheckable(True)
        lay.addWidget(adv, alignment=Qt.AlignmentFlag.AlignLeft)
        box = QFrame()
        box.setObjectName("card")
        bl = QGridLayout(box)
        bl.setContentsMargins(18, 12, 18, 12)
        self.dev_combo = Combo()
        gpu_label = "Core ML (experimental)" if IS_MAC else "NVIDIA GPU (CUDA)"
        for key, label in (("auto", "Recommended"), ("cuda", gpu_label), ("cpu", "Processor only")):
            self.dev_combo.addItem(label, key)
        bl.addWidget(QLabel("Speech recognition runs on"), 0, 0)
        bl.addWidget(self.dev_combo, 0, 1)
        self.ai_combo = Combo()
        for key, label in (("auto", "Recommended"), ("small", "Larger model (1.5B)"), ("tiny", "Smaller model (0.5B)"),
                           ("off", "Off")):
            self.ai_combo.addItem(label, key)
        bl.addWidget(QLabel("AI check"), 1, 0)
        bl.addWidget(self.ai_combo, 1, 1)
        box.setVisible(False)
        adv.toggled.connect(box.setVisible)
        lay.addWidget(box)
        lay.addStretch(1)
        return page

    def _page_download(self) -> QWidget:
        page, lay = self._base("Downloading", "Interrupted downloads pick up where they stopped, and every file is "
                                              "checked before it's used.")
        self.dl_grid = self._card(lay)
        self.dl_rows: dict[str, tuple[QLabel, QProgressBar, QLabel]] = {}
        self.dl_note = self._hint("", "stat")
        lay.addWidget(self.dl_note)
        self.dl_retry = QPushButton("Try again")
        self.dl_retry.setVisible(False)
        self.dl_retry.clicked.connect(self._start_downloads)
        lay.addWidget(self.dl_retry, alignment=Qt.AlignmentFlag.AlignLeft)
        lay.addStretch(1)
        return page

    def _page_bench(self) -> QWidget:
        page, lay = self._base("Speed check", "A real test on this computer: Tiro transcribes a recording and "
                                              "runs its corrections, then picks the settings that keep up.")
        grid = self._card(lay)
        self.bench_rows: dict[str, QLabel] = {}
        for r, (key, label) in enumerate((("load", "Loading the speech model"), ("speech", "Speech recognition"),
                                          ("ai", "AI check"))):
            grid.addWidget(QLabel(label), r, 0, alignment=Qt.AlignmentFlag.AlignTop)
            value = self._hint("waiting…", "stat")
            grid.addWidget(value, r, 1)
            self.bench_rows[key] = value
        self.bench_note = self._hint("")
        lay.addWidget(self.bench_note)
        lay.addStretch(1)
        return page

    def _page_permissions(self) -> QWidget:
        from tiro.platform.mac.permissions import PERMISSIONS

        page, lay = self._base("Three permissions", "macOS asks you to allow these once. Each button opens the right "
                                                    "page in System Settings.")
        grid = self._card(lay)
        self.perm_rows = {}
        for r, perm in enumerate(PERMISSIONS):
            col = QVBoxLayout()
            col.addWidget(QLabel(perm.title))
            col.addWidget(self._hint(perm.why))
            col.addWidget(self._hint(perm.how))
            w = QWidget()
            w.setLayout(col)
            col.setContentsMargins(0, 0, 0, 0)
            grid.addWidget(w, r, 1)
            state = QLabel("")
            grid.addWidget(state, r, 0, alignment=Qt.AlignmentFlag.AlignTop)
            btn = QPushButton("Allow…")
            btn.clicked.connect(lambda _=False, k=perm.key: self._ask_permission(k))
            grid.addWidget(btn, r, 2, alignment=Qt.AlignmentFlag.AlignTop)
            self.perm_rows[perm.key] = (state, btn)
        lay.addWidget(self._hint("If Tiro still can't type after you allow it, quit Tiro from the menu bar and open "
                                 "it again: macOS applies some permissions only to newly started apps."))
        lay.addStretch(1)
        return page

    def _page_mic(self) -> QWidget:
        page, lay = self._base("Which microphone?", "Say something: the bar should move. If it doesn't, try "
                                                    "another microphone.")
        grid = self._card(lay)
        self.mic_combo = Combo()
        self.mic_combo.currentIndexChanged.connect(self._mic_changed)
        grid.addWidget(self.mic_combo, 0, 0, 1, 2)
        self.mic_meter = LevelMeter()
        self.mic_meter.setFixedHeight(10)
        grid.addWidget(self.mic_meter, 1, 0, 1, 2)
        self.mic_note = self._hint("")
        grid.addWidget(self.mic_note, 2, 0, 1, 2)
        lay.addStretch(1)
        return page

    def _page_hotkey(self) -> QWidget:
        s = self.app.settings
        page, lay = self._base("Your dictation key", "Hold it while you talk and let go when you're done. Pick a key "
                                                     "you don't otherwise use.")
        grid = self._card(lay)
        self.hk_btn = HotkeyButton(s.hotkey_spec.label, self._capture)
        grid.addWidget(QLabel("Key"), 0, 0)
        grid.addWidget(self.hk_btn, 0, 1, alignment=Qt.AlignmentFlag.AlignRight)
        self.hk_mode = Segmented([("hold", "Hold to talk"), ("toggle", "Press to start, again to stop")], s.mode)
        self.hk_mode.changed.connect(lambda v: self.app.update_setting("mode", v))
        grid.addWidget(QLabel("Style"), 1, 0)
        grid.addWidget(self.hk_mode, 1, 1, alignment=Qt.AlignmentFlag.AlignRight)
        lay.addWidget(self._hint("Tip: double-tap the key to keep dictating hands-free; tap it again to stop. Esc "
                                 "cancels."))
        lay.addStretch(1)
        return page

    def _page_learning(self) -> QWidget:
        s = self.app.settings
        page, lay = self._base("Tiro learns how you talk", "So it gets your names, brands and terms right.")
        grid = self._card(lay)
        self.learn_toggle = Toggle(s.history)
        self.learn_toggle.toggled.connect(lambda v: self.app.update_setting("history", v))
        grid.addWidget(QLabel("Learn from what I dictate"), 0, 0)
        grid.addWidget(self.learn_toggle, 0, 1, alignment=Qt.AlignmentFlag.AlignRight)
        for line in (
            "Tiro remembers what you dictate, on this computer only, and uses it to tell “Rust” from "
            "“rust” or “Shaun” from “Sean” the way you mean them.",
            "If Tiro mishears a word, press the Fix shortcut (or use the menu) to correct it once. It remembers.",
            "It only ever swaps a misheard word for one that sounds like it. It never rewrites your sentences.",
            "Nothing is ever learned from password fields. Switch this off, or clear everything, any time in "
            "Settings → Privacy.",
        ):
            lay.addWidget(self._hint("•  " + line, "stat"))
        lay.addStretch(1)
        return page

    def _page_words(self) -> QWidget:
        page, lay = self._base("Any words Tiro should know?",
                               "Names, brands, places or jargon you use, one per line, spelled the way you want "
                               "them typed. Optional: you can add more later.")
        self.words_box = QPlainTextEdit()
        self.words_box.setObjectName("dictionary")
        self.words_box.setPlaceholderText("Siobhan\nGeoGuessr\nKubernetes\nShoreditch")
        self.words_box.setPlainText("\n".join(self.app.settings.dictionary))
        lay.addWidget(self.words_box, 1)
        return page

    def _page_test(self) -> QWidget:
        page, lay = self._base("Try it", "")
        self.test_lead = self._hint("", "lead")
        lay.addWidget(self.test_lead)
        self.try_box = QPlainTextEdit()
        self.try_box.setObjectName("trybox")
        self.try_box.setPlaceholderText("Your words will appear here")
        self.try_box.textChanged.connect(self._tried)
        lay.addWidget(self.try_box, 1)
        self.test_note = self._hint("")
        lay.addWidget(self.test_note)
        return page

    # ================================================================== computer
    def _apply_choices(self) -> None:
        dev = self.dev_combo.currentData()
        ai = self.ai_combo.currentData()
        if dev == "auto":
            dev = "auto" if self.plan.device != "cpu" else "cpu"
        self.app.update_setting("device", dev)
        if ai == "off":
            self.app.update_setting("ai_correction", False)
        else:
            self.app.update_setting("ai_correction", True)
            self.app.update_setting("ai_model", "auto" if ai == "auto" else ai)

    # ================================================================== downloads
    def _items(self) -> list[tuple[str, str, object, str]]:
        """(key, title, spec, variant) still to download."""
        from tiro.models import LANGUAGE_MODELS, MLX_LANGUAGE_MODELS, MODELS, find_model

        s = self.app.settings
        out = []
        want_gpu = s.device != "cpu" and self.plan.device != "cpu"
        spec = MODELS[s.model]
        variant = "fp32" if want_gpu else "int8"
        if find_model(spec, variant) is None:
            out.append(("speech", f"Speech model · {spec.title}", spec, variant))
        if not IS_MAC and want_gpu:
            from tiro import gpu

            if not gpu.cuda_runtime_present():
                out.append(("cuda", "NVIDIA GPU libraries (CUDA 13, cuDNN)", None, ""))
        if s.ai_correction:
            from tiro.correct.service import _has_mlx

            choice = s.ai_model if s.ai_model != "auto" else self.plan.ai_model
            choice = choice if choice in LANGUAGE_MODELS else "tiny"
            mlx = IS_MAC and _has_mlx()
            lm = (MLX_LANGUAGE_MODELS if mlx else LANGUAGE_MODELS)[choice]
            lm_variant = "fp32" if (mlx or want_gpu) else "int8"  # (MLX has a single build)
            if find_model(lm, lm_variant) is None:
                out.append(("ai", f"AI check · {lm.title}", lm, lm_variant))
        return out

    def _start_downloads(self) -> None:
        self.dl_retry.setVisible(False)
        items = self._items()
        while self.dl_grid.count():
            w = self.dl_grid.takeAt(0).widget()
            if w is not None:
                w.deleteLater()
        self.dl_rows.clear()
        if not items:
            self.dl_note.setText("Everything Tiro needs is already on this computer. ✓")
            self.next.setEnabled(True)
            return
        self.next.setEnabled(False)
        for r, (key, title, _spec, _variant) in enumerate(items):
            name = QLabel(title)
            bar = QProgressBar()
            bar.setRange(0, 1000)
            info = self._hint("waiting…", "stat")
            self.dl_grid.addWidget(name, r * 2, 0)
            self.dl_grid.addWidget(info, r * 2, 1, alignment=Qt.AlignmentFlag.AlignRight)
            self.dl_grid.addWidget(bar, r * 2 + 1, 0, 1, 2)
            self.dl_rows[key] = (name, bar, info)
        self.dl_note.setText("")
        threading.Thread(target=self._download_all, args=(items,), name="tiro-setup-download", daemon=True).start()

    def _download_all(self, items) -> None:
        from tiro.models import download_model

        ok_all = True
        for key, _title, spec, variant in items:
            t0, last = time.monotonic(), [0.0, 0]

            def progress(done, total, key=key, t0=t0, last=last):
                now = time.monotonic()
                if now - last[0] < 0.2 and done < total:
                    return
                speed = done / max(0.5, now - t0)
                left = (total - done) / speed if speed > 0 else 0
                text = f"{done / 2**30:.2f} of {total / 2**30:.2f} GB · {speed / 2**20:.0f} MB/s"
                if left > 5:
                    text += f" · about {int(left // 60)} min {int(left % 60)} s left" if left >= 60 else \
                        f" · {int(left)} s left"
                last[0] = now
                self.sig.progress.emit(key, done / total if total else 0.0, text)

            try:
                if key == "cuda":
                    from tiro import gpu

                    gpu.download_cuda_runtime(progress)
                else:
                    download_model(spec, progress, variant=variant)
                self.sig.item_done.emit(key, True, "Ready ✓")
            except Exception as exc:
                log.exception("setup download failed")
                ok_all = False
                self.sig.item_done.emit(key, False, f"Failed: {exc}")
        self.sig.all_done.emit(ok_all)

    def _on_progress(self, key: str, frac: float, text: str) -> None:
        row = self.dl_rows.get(key)
        if row:
            row[1].setValue(int(frac * 1000))
            row[2].setText(text)

    def _on_item_done(self, key: str, ok: bool, text: str) -> None:
        row = self.dl_rows.get(key)
        if row:
            if ok:
                row[1].setValue(1000)
            row[2].setText(text)
            row[2].setObjectName("good" if ok else "warn")
            row[2].style().unpolish(row[2])
            row[2].style().polish(row[2])

    def _on_downloads_done(self, ok: bool) -> None:
        if ok:
            self.dl_note.setText("All downloaded and checked. ✓")
            self.next.setEnabled(True)
            QTimer.singleShot(700, lambda: self.key == "download" and self._next())
        else:
            self.dl_note.setText("Something went wrong. Check your connection and try again; finished parts are kept.")
            self.dl_retry.setVisible(True)

    # ================================================================== benchmark
    def _start_bench(self) -> None:
        self.next.setEnabled(False)
        self.bench_note.setText("")
        for v in self.bench_rows.values():
            v.setText("waiting…")
        self.bench_rows["load"].setText("loading…")
        threading.Thread(target=self._bench_work, name="tiro-setup-bench", daemon=True).start()

    def _bench_work(self) -> None:
        from tiro.setup import benchmark

        app = self.app
        t0 = time.monotonic()
        app.start_engine()
        if not app.engine_ready.wait(timeout=600) or app.engine is None:
            self.sig.bench.emit("load", None)
            return
        self.sig.bench.emit("load", (time.monotonic() - t0, app.engine.device_label, app.engine.fallback_reason))
        self.sig.bench.emit("speech", benchmark.speech(app.engine))
        s = app.settings
        if not s.ai_correction:
            self.sig.bench.emit("ai", "off")
            return
        for _attempt in range(2):
            end = time.monotonic() + 300
            while app.correction.corrector.language is None and time.monotonic() < end:
                status = app.correction.language_status
                if status.startswith(("AI check unavailable", "off")) and status != "off":
                    break
                time.sleep(0.2)
            res = benchmark.correction(app.correction.corrector)
            if res.ai_fast_enough:
                self.sig.bench.emit("ai", res)
                return
            if app.correction.language_choice == "small":
                self.sig.bench.emit("ai_retry", res)
                app.bridge.call.emit(lambda: app.update_setting("ai_model", "tiny"))  # reloads the smaller model
                time.sleep(1.0)
                continue
            break
        self.sig.bench.emit("ai_off", res)

    def _on_bench(self, step: str, res) -> None:
        rows = self.bench_rows
        if step == "load":
            if res is None:
                rows["load"].setText("The speech model didn't load. See Settings → Recognition.")
                self.next.setEnabled(True)
                return
            secs, label, fallback = res
            rows["load"].setText(f"Ready on {label} in {secs:.0f} s")
            if fallback:
                self.bench_note.setText(f"Tiro is using the processor because {fallback}.")
        elif step == "speech":
            if res.ok:
                rows["speech"].setText(f"{res.audio_s:.0f} s of speech understood in {res.decode_ms:.0f} ms, "
                                       f"{res.speedup:.0f}× faster than you talk ✓")
            else:
                rows["speech"].setText(f"Problem: {res.error or 'the test clip came out wrong'}")
            rows["ai"].setText("loading the AI check…")
        elif step == "ai":
            if res == "off":
                rows["ai"].setText("Off (your choice). Your dictionary and history still fix words.")
            else:
                rows["ai"].setText(f"{res.ai_ms:.0f} ms per check on the {'GPU' if res.ai_device != 'cpu' else 'processor'}"
                                   f" ✓ (limit 150 ms)")
            self.next.setEnabled(True)
        elif step == "ai_retry":
            rows["ai"].setText(f"{res.ai_ms:.0f} ms per check: too slow, trying the smaller model…")
        elif step == "ai_off":
            self.app.update_setting("ai_correction", False)
            took = f"{res.ai_ms:.0f} ms" if res.ok else "too long"
            rows["ai"].setText(f"Switched off: one check took {took} here, more than the 150 ms Tiro allows.")
            self.bench_note.setText("Your dictionary, the fixes you teach and your history still correct words. "
                                    "You can turn the AI check back on in Settings → Accuracy.")
            self.next.setEnabled(True)

    # ================================================================== permissions (Mac)
    def _refresh_permissions(self) -> None:
        if not IS_MAC:
            return
        from tiro.platform.mac.permissions import status

        for key, (state, btn) in self.perm_rows.items():
            ok = status(key) == "granted"
            state.setText("✓" if ok else "•")
            state.setObjectName("good" if ok else "warn")
            state.style().unpolish(state)
            state.style().polish(state)
            btn.setText("Allowed" if ok else "Allow…")
            btn.setEnabled(not ok)

    def _ask_permission(self, key: str) -> None:
        from tiro.platform.mac.permissions import request

        request(key)

    # ================================================================== microphone
    def _start_mic(self) -> None:
        self.mic_combo.blockSignals(True)
        self.mic_combo.clear()
        default = self.app.default_mic_name()
        self.mic_combo.addItem(f"System default{f' ({default})' if default else ''}", None)
        for name in self.app.list_mics():
            self.mic_combo.addItem(name, name)
        cur = self.app.settings.microphone
        self.mic_combo.setCurrentIndex(max(0, self.mic_combo.findData(cur)) if cur else 0)
        self.mic_combo.blockSignals(False)
        self._open_mic()

    def _open_mic(self) -> None:
        from tiro.audio import AudioCapture, MicError

        self._stop_mic()
        cap = AudioCapture(self.app.settings.microphone)
        try:
            cap.open()
        except MicError as exc:
            self.mic_note.setText(str(exc))
            return
        self.mic_note.setText("")
        self._mic_cap = cap
        self._mic_timer.start()

    def _mic_changed(self, _i: int) -> None:
        self.app.update_setting("microphone", self.mic_combo.currentData())
        self._open_mic()

    def _poll_mic(self) -> None:
        if self._mic_cap is not None:
            self._mic_cap.drain()
            self.mic_meter.set_level(self._mic_cap.level)

    def _stop_mic(self) -> None:
        self._mic_timer.stop()
        if self._mic_cap is not None:
            self._mic_cap.close()
            self._mic_cap = None

    # ================================================================== shortcut / words / test
    def _capture(self) -> None:
        if self.hk_btn.capturing:
            return
        self.hk_btn.set_capturing(True)

        def done(spec):
            self.hk_btn.set_capturing(False)
            if spec is not None:
                self.app.update_setting("hotkey", str(spec))
            self.hk_btn.set_label(self.app.settings.hotkey_spec.label)

        self.app.capture_hotkey(done)

    def _save_words(self) -> None:
        words = [w.strip() for w in self.words_box.toPlainText().splitlines() if w.strip()]
        merged = list(dict.fromkeys(self.app.settings.dictionary + words))
        if merged != self.app.settings.dictionary:
            self.app.update_setting("dictionary", merged)

    def _enter_test_text(self) -> None:
        s = self.app.settings
        how = "Hold" if s.mode == "hold" else "Press"
        self.test_lead.setText(f"Click in the box, {how.lower()} {s.hotkey_spec.label} and say something, like "
                               f"“Tiro is set up and ready to go.”")

    def _tried(self) -> None:
        if self.try_box.toPlainText().strip():
            self.test_note.setText("It works! \U0001F389  Tiro will type like this into any app. Click Finish.")
            self.test_note.setObjectName("good")
            self.test_note.style().unpolish(self.test_note)
            self.test_note.style().polish(self.test_note)

    def _finish(self) -> None:
        self.app.update_setting("setup_done", True)
        self.close()
        self.app.overlay.notify(f"{APP_NAME} is ready — {self.app.settings.hotkey_spec.label} to dictate", "ok",
                                seconds=5.0)

    def showEvent(self, e) -> None:
        super().showEvent(e)
        if not IS_MAC:
            winutil.dark_title_bar(int(self.winId()))
        self._enter_test_text()

    def closeEvent(self, e) -> None:
        self._stop_mic()
        self._perm_timer.stop()
        if not self.app.settings.setup_done:
            self.app.update_setting("setup_done", True)  # closing early still counts: settings are saved
            self.app.start_engine()
        super().closeEvent(e)
