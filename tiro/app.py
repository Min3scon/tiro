"""Application controller: wires hotkey, dictation sessions, overlay, tray and settings together."""

from __future__ import annotations

import logging
import os
import threading
import time

from PySide6.QtCore import QObject, Qt, QTimer, Signal, Slot
from PySide6.QtGui import QGuiApplication

from tiro import APP_NAME, audio, autostart, sounds, winutil
from tiro.asr import ParakeetEngine
from tiro.config import Settings
from tiro.context import InputContext
from tiro.correct.service import CorrectionService
from tiro.hotkey import ChordHotkey, HotkeyCapture, HotkeyMachine, KeyboardHook, modifiers_held
from tiro.injector import Injector, replay_keys
from tiro.keys import ALL_MODIFIERS, HotkeySpec, hotkey_problem, name_vks
from tiro.learn import CorrectionLearner
from tiro.models import MODELS, DownloadCancelled, download_model, find_model, vad_model_path
from tiro.paths import history_path, log_dir
from tiro.session import DictationSession, SessionCallbacks
from tiro.textproc import FormatOptions
from tiro.ui.overlay import Overlay
from tiro.ui.tray import Tray
from tiro.vad import SileroVad
from tiro.vocab import Vocabulary

log = logging.getLogger(__name__)

REVEAL_DELAY_MS = 220  # overlay + chime appear this long after the key goes down (if it wasn't a shortcut)
MODIFIER_START_DELAY = 0.1  # with Shift/Ctrl/Alt/Win as the hotkey, most shortcut chords cancel before the mic opens

_SHELL_WINDOWS = {"Shell_TrayWnd", "Shell_SecondaryTrayWnd", "NotifyIconOverflowWindow", "Progman", "WorkerW",
                  "TopLevelWindowForOverflowXamlIsland", "com.apple.dock", "com.apple.systemuiserver"}


def _open_path(path) -> None:
    import subprocess
    import sys

    if sys.platform == "win32":
        os.startfile(path)
    else:
        subprocess.Popen(["open", str(path)])


class _Bridge(QObject):
    action = Signal(str)
    learned = Signal(str, str, str)
    session_state = Signal(int, str)
    session_text = Signal(int, str, str)
    session_notice = Signal(int, str, str)
    engine_status = Signal(int, str, str)
    captured = Signal(object)
    lm_status = Signal(str)
    degrade = Signal(str)
    update_result = Signal(str, str)
    call = Signal(object)  # run a function on the GUI thread


class _EngineRef:
    """What a session sees as 'the engine': waits while a model (re)loads and always uses the current one."""

    def __init__(self, app: TiroApp):
        self.app = app

    def transcribe(self, audio_):
        self.app.engine_ready.wait()
        return self.app.engine.transcribe(audio_)

    def ping(self) -> None:
        eng = self.app.engine
        if eng is not None and self.app.engine_ready.is_set():
            eng.ping()

    def urgent(self, on: bool) -> None:
        eng = self.app.engine
        flag = getattr(eng, "priority", None)
        if flag is None:
            return
        if on:
            flag.set()
        else:
            flag.clear()


class TiroApp(QObject):
    def __init__(self, qapp, *, autostarted: bool = False, setup: bool = False, safe_mode: bool = False,
                 update_trial: str | None = None, rolled_back_from: str | None = None):
        super().__init__()
        self.qapp = qapp
        self.autostarted = autostarted
        self.setup_window = None
        self._engine_started = False
        self.settings = Settings.load()
        self.safe_mode = safe_mode
        self.rolled_back_from = rolled_back_from
        if safe_mode:
            log.warning("safe mode: optional features are off for this run")
            self.settings.apply_safe_mode()
        self.bridge = _Bridge()
        self.bridge.action.connect(self._on_action, Qt.ConnectionType.QueuedConnection)
        self.bridge.session_state.connect(self._on_session_state, Qt.ConnectionType.QueuedConnection)
        self.bridge.session_text.connect(self._on_session_text, Qt.ConnectionType.QueuedConnection)
        self.bridge.session_notice.connect(self._on_session_notice, Qt.ConnectionType.QueuedConnection)
        self.bridge.engine_status.connect(self._on_engine_status, Qt.ConnectionType.QueuedConnection)
        self.bridge.captured.connect(self._on_captured, Qt.ConnectionType.QueuedConnection)
        self.bridge.lm_status.connect(self._on_lm_status, Qt.ConnectionType.QueuedConnection)
        self.bridge.degrade.connect(self._on_degrade, Qt.ConnectionType.QueuedConnection)
        self.bridge.update_result.connect(self._on_update_result, Qt.ConnectionType.QueuedConnection)
        self.bridge.call.connect(lambda fn: fn(), Qt.ConnectionType.QueuedConnection)

        s = self.settings
        self.engine: ParakeetEngine | None = None
        self.correction = CorrectionService(
            history_path(), dictionary=s.dictionary, history_enabled=s.history, mode=s.correction_mode,
            gate_conf=s.correction_threshold or None, enabled=s.correction, on_knowledge=self._knowledge_changed,
        )
        self.learner = CorrectionLearner(on_learn=self.bridge.learned.emit)
        self.learner.enabled = self.settings.learn_corrections
        self.bridge.learned.connect(self._on_learned, Qt.ConnectionType.QueuedConnection)
        self.context = InputContext(on_click=self.learner.note_click)
        self.injector = Injector(self.settings.insertion)
        self.vocab = Vocabulary.from_lines(self.settings.dictionary, extra=self.correction.boost_terms())
        self.last_session: DictationSession | None = None  # the last dictation that typed something
        self.vad = SileroVad(vad_model_path())

        self.engine: ParakeetEngine | None = None
        self.engine_ready = threading.Event()
        self.engine_state = "loading"  # loading | downloading | ready | error
        self.engine_message = ""
        self._engine_gen = 0
        self._engine_ref = _EngineRef(self)

        self.session: DictationSession | None = None
        self._token = 0
        self.last_text = ""
        self._capture_cb = None
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.timeout.connect(self._refresh_audio_devices)
        self._last_target = 0  # the last app window you used (for starting dictation from the menu)
        self._menu_session = False
        self._target_timer = QTimer(self)
        self._target_timer.setInterval(500)
        self._target_timer.timeout.connect(self._track_target)
        self._target_timer.start()

        self.overlay = Overlay(level_source=self._level)
        self.overlay.position = self.settings.overlay_position
        self.overlay.show_text = self.settings.show_live_text
        self.tray = Tray(self)
        self.settings_window = None
        from tiro.update.controller import UpdateController

        self.updates = UpdateController(self, update_trial=update_trial)

        self.fix_key = ChordHotkey(self._fix_spec(), on_fire=lambda: self.bridge.action.emit("fix_last"))
        self.machine = HotkeyMachine(
            self.settings.hotkey_spec,
            on_action=self.bridge.action.emit,
            replay=self._replay_chord,
            mode=self.settings.mode,
            double_tap_lock=self.settings.double_tap_lock,
            speech_started=lambda: bool(self.session is not None and self.session.speech_heard),
        )
        self._revealed = False  # overlay/sound shown for the current dictation yet?
        self._load_lock = threading.Lock()
        self._download_cancel: threading.Event | None = None
        self.hub: audio.MicHub | None = None  # open microphone for "instant start"
        self._sync_hub()
        self.native_keys = self._start_native_hook()
        if self.native_keys is None:
            self.hook = KeyboardHook(self._on_key,
                                     can_reinstall=lambda: not self.machine.active and not self.machine.down)
            self.hook.start()
        autostart.refresh_path()
        if not setup:
            self.start_engine()  # (during setup the wizard starts it once the models are downloaded)
        self.updates.start()

    # ================================================================== keyboard hook (hook thread)
    def _start_native_hook(self):
        """Windows: run the hotkey on a native thread (tiro_hook.dll). The Python hook needs Python's lock for
        every key press, and while a model loads that lock can be busy long enough for Windows to drop the hook,
        losing a press of the hotkey. Falls back to the Python hook if the DLL is missing or fails."""
        import sys

        if sys.platform != "win32" or os.environ.get("TIRO_PY_HOOK") == "1" or self.safe_mode:
            return None
        try:
            from tiro.platform.windows import native_hook

            if not native_hook.available():
                log.info("keyboard hook: Python (tiro_hook.dll not found)")
                return None
            nk = native_hook.NativeHotkeys(
                self.settings.hotkey_spec, self._fix_spec(), mode=self.settings.mode,
                double_tap_lock=self.settings.double_tap_lock, on_action=self.bridge.action.emit,
                on_fix=lambda: self.bridge.action.emit("fix_last"), on_typed=self._note_typed)
            nk.start()
        except Exception:
            log.exception("native keyboard hook unavailable; using the Python one")
            return None
        self.hook = nk
        self.machine = nk.machine
        self.fix_key = nk.fix_key
        return nk

    def _on_key(self, vk: int, down: bool, scan: int, flags: int, injected: bool) -> bool:
        if self.fix_key.on_key(vk, down):
            return True
        swallow = self.machine.on_key(vk, down, scan, flags)
        if down and not swallow:
            self._note_typed(vk, scan, modifiers_held(self.machine.down))
        return swallow

    def _note_typed(self, vk: int, scan: int, mods: set[str]) -> None:
        shift, shortcut = "shift" in mods, bool(mods & {"ctrl", "alt", "win"})
        self.context.note_key(vk, shift, shortcut)
        self.learner.note_key(vk, scan, shift, shortcut, winutil.foreground_window())

    def _replay_chord(self, keys: list[tuple[int, int, int, bool]]) -> None:
        """A swallowed hotkey turned out to be part of a shortcut: hand the keys back to the app."""
        threading.Thread(target=replay_keys, args=(keys,), daemon=True).start()
        mods = modifiers_held(self.machine.down)
        for vk, scan, _flags, down in keys:
            if down and vk not in ALL_MODIFIERS:
                self._note_typed(vk, scan, mods)  # the replayed key is invisible to our own hook

    def _level(self) -> float:
        s = self.session
        return s.level if s is not None else 0.0

    # ================================================================== dictation
    @property
    def dictating(self) -> bool:
        return self.session is not None and self.session.alive

    def _track_target(self) -> None:
        fg = winutil.foreground_window()
        if fg and winutil.window_pid(fg) != os.getpid() and winutil.window_class(fg) not in _SHELL_WINDOWS:
            self._last_target = fg

    def toggle_dictation(self) -> None:
        """Start or stop from the menu. Starting dictates hands-free into the app you were last using."""
        if self.dictating:
            self.session.stop()
            return
        target = self._last_target
        if target and winutil.is_window(target):
            winutil.activate(target)
        QTimer.singleShot(150, lambda: self._start_session(from_menu=True))

    @Slot(str)
    def _on_action(self, action: str) -> None:
        log.debug("hotkey action: %s", action)
        if action == "start" and self._menu_session and self.dictating:
            self.session.stop()  # the hotkey also ends a dictation started from the menu
            self.machine.force_idle()
        elif action == "start":
            self._start_session()
        elif action == "stop" and self.session:
            self.session.stop()
        elif action == "cancel" and self.session:
            self.session.cancel()
        elif action == "lock" and self.session:
            self.session.lock()
            self._reveal(self._token, force=True)
            self.overlay.set_locked(True)
        elif action == "fix_last":
            self.show_fix_last()

    def _start_session(self, from_menu: bool = False) -> None:
        self._menu_session = from_menu
        if self.engine_state == "error":
            self.overlay.notify(self.engine_message or "The speech model failed to load.", "error")
            self.machine.force_idle()
            return
        prev = self.session if (self.session is not None and self.session.alive) else None
        spec = self.settings.hotkey_spec
        single_modifier = len(spec.keys) == 1 and bool(name_vks(spec.keys[0]) & ALL_MODIFIERS)
        test_wav = os.environ.get("TIRO_TEST_WAV")
        hub = self.hub
        if test_wav:
            capture = audio.FileCapture(test_wav)
        elif hub is not None:
            capture = hub.subscribe()  # includes the moment before the key went down
        else:
            capture = audio.AudioCapture(self.settings.microphone)
        self._token += 1
        token = self._token
        s = self.settings
        callbacks = SessionCallbacks(
            on_state=lambda st: self.bridge.session_state.emit(token, st),
            on_text=lambda c, p: self.bridge.session_text.emit(token, c, p),
            on_notice=lambda k, m: self.bridge.session_notice.emit(token, k, m),
        )
        self.session = DictationSession(
            engine=self._engine_ref,
            engine_ready=self.engine_ready,
            vad=self.vad,
            capture=capture,
            injector=self.injector,
            context=self.context,
            learner=self.learner,
            fmt=FormatOptions(remove_fillers=s.remove_fillers, voice_commands=s.voice_commands),
            smart_spacing=s.smart_spacing,
            mode="locked" if from_menu else ("hold" if s.mode == "hold" else "toggle"),
            hands_free_pause_sec=s.hands_free_pause_sec,
            callbacks=callbacks,
            start_delay=MODIFIER_START_DELAY if (single_modifier and hub is None) else 0.0,
            previous=prev,
            correction=self.correction.begin(),
            correction_budget_ms=float(s.correction_budget_ms),
            late_fixes=s.late_fixes,
            on_finish=self._session_finished,
            on_speech=(lambda: self.native_keys.set_speech(True)) if self.native_keys is not None else None,
        )
        if self.native_keys is not None:
            self.native_keys.set_speech(False)
        self.updates.service.dictation_started()  # update downloads pause while you dictate
        self.session.start()
        # Audio is captured from the first instant, but the overlay and chime wait a moment: if the key turns
        # out to be half of a shortcut (Shift + letter, Ctrl + C) nothing should flash or beep.
        self._revealed = False
        QTimer.singleShot(REVEAL_DELAY_MS, lambda: self._reveal(token))

    def _reveal(self, token: int, force: bool = False) -> None:
        sess = self.session
        if token != self._token or self._revealed or sess is None or not sess.alive:
            return
        if not force and self.machine.state == HotkeyMachine.TAP_PENDING:
            QTimer.singleShot(120, lambda: self._reveal(token))  # a lone tap may still turn into a double-tap
            return
        self._revealed = True
        self.overlay.begin(loading=not self.engine_ready.is_set())
        self.overlay.set_locked(sess.mode == "locked")
        self.tray.set_state("live")
        if self.settings.sounds:
            sounds.play("start")

    @Slot(int, str)
    def _on_session_state(self, token: int, state: str) -> None:
        if token != self._token:
            return
        if state in ("listening", "loading", "finishing"):
            if self._revealed:
                self.overlay.set_state(state)
            return
        if self._revealed:
            self.overlay.set_state(state)
        self.tray.set_state("idle" if self.engine_state == "ready" else "busy")
        self.machine.force_idle()
        sess = self.session
        if state == "done" and sess is not None and sess.text.strip():
            self.last_text = sess.text
            if self.settings.sounds:
                sounds.play("stop")
        elif state == "error" and self.settings.sounds:
            sounds.play("error")
        self._refresh_timer.start(2000)

    @Slot(int, str, str)
    def _on_session_text(self, token: int, committed: str, pending: str) -> None:
        if token == self._token:
            if not self._revealed and (committed or pending):
                self._reveal(token, force=True)
            if self._revealed:
                self.overlay.set_text(committed, pending)

    @Slot(int, str, str)
    def _on_session_notice(self, token: int, kind: str, message: str) -> None:
        if token == self._token:
            self.overlay.notify(message, kind, seconds=4.0 if kind == "error" else 3.2)

    def _refresh_audio_devices(self) -> None:
        if self.session is not None and self.session.alive:
            return
        threading.Thread(target=audio.refresh_devices, daemon=True).start()

    def _sync_hub(self) -> None:
        """Open or close the always-on microphone to match the 'instant start' setting."""
        want = self.settings.instant_start and not os.environ.get("TIRO_TEST_WAV")
        old, self.hub = self.hub, None
        if old is not None:
            old.stop()
        if not want:
            return
        hub = audio.MicHub(self.settings.microphone)
        try:
            hub.start()
        except audio.MicError as exc:
            log.warning("instant start unavailable: %s", exc)
            self.overlay.notify(str(exc), "error")
            return
        self.hub = hub

    def _session_finished(self, sess: DictationSession) -> None:
        """Runs on the session thread once a dictation is over (whatever the outcome)."""
        self.updates.service.dictation_ended()
        ok = sess.error is None and not sess._cancel.is_set()
        text = sess.text if ok else ""
        self.correction.end(text, app=sess.app_name, title=sess.window_title, secure=sess.private)
        if sess.end_latency_ms is not None and text:
            self.correction.metrics.add_end(sess.end_latency_ms, sess.end_correction_ms)
        if text.strip():
            self.last_session = sess
        step = self.correction.check_speed(float(self.settings.correction_budget_ms))
        if step:
            self.bridge.degrade.emit(step)

    def _configure_language(self) -> None:
        """Load (or unload) the tier-2 language model to match the settings and the speech model's device."""
        eng = self.engine
        s = self.settings
        if eng is None:
            return
        self.correction.configure_language(
            enabled=s.ai_correction and s.correction, choice=s.ai_model, device=eng.device,
            vram_gb=eng.gpu_info.memory_gb if eng.gpu_info else None, gpu_lock=eng._lock,
            gpu_yield=getattr(eng, "wants_gpu", None), on_status=self.bridge.lm_status.emit,
        )

    @Slot(str)
    def _on_lm_status(self, text: str) -> None:
        if not text.startswith(("Loading", "Downloading")):
            self.hook.reinstall()  # (see _on_engine_status)
        if self.settings_window is not None and self.settings_window.isVisible():
            self.settings_window.refresh()

    @Slot(str)
    def _on_degrade(self, step: str) -> None:
        """Correction made typing late too often: step down, and say so plainly."""
        why = "Corrections were slowing your typing down, so "
        if step == "small":
            self.update_setting("ai_model", "tiny")
            msg = why + "Tiro switched to its smaller AI model."
        elif step == "gate":
            self.update_setting("correction_threshold", 0.6)
            msg = why + "Tiro now double-checks fewer words."
        else:
            self.update_setting("ai_correction", False)
            msg = why + "Tiro turned the AI check off. Your dictionary and history still work."
        log.warning("auto-degrade: %s", step)
        self.overlay.notify(msg, "warning", seconds=6.0)

    # ================================================================== correction & learning
    def _fix_spec(self) -> HotkeySpec | None:
        try:
            return HotkeySpec.parse(self.settings.fix_hotkey) if self.settings.fix_hotkey else None
        except ValueError:
            return None

    def _knowledge_changed(self) -> None:
        """Runs on the correction worker after a rebuild: refresh the decoder's boosted words."""
        self._update_boosting()

    def _update_boosting(self) -> None:
        vocab = Vocabulary.from_lines(self.settings.dictionary, extra=self.correction.boost_terms())
        self.vocab = vocab
        eng = self.engine
        if eng is not None:
            n = eng.set_vocabulary(vocab)
            log.info("boosting %d dictionary + %d learned/frequent words (%d decoder spellings)",
                     len(vocab.words), len(vocab.extra), n)

    @Slot(str, str, str)
    def _on_learned(self, heard: str, written: str, context: str) -> None:
        if self.correction.learn(heard, written, context):
            self.overlay.notify(f"Learned \u201c{written}\u201d. Tiro will spell it that way from now on.", "ok",
                                seconds=4.0)
            if self.settings_window is not None and self.settings_window.isVisible():
                self.settings_window.refresh()

    def show_fix_last(self) -> None:
        from tiro.ui.fixlast import FixLastWindow

        sess = self.last_session
        if sess is None or not sess.text.strip():
            self.overlay.notify("Nothing to fix yet. Dictate something first.", "warning", seconds=3.0)
            return
        FixLastWindow.open(self, sess)

    # ================================================================== engine
    def start_engine(self) -> None:
        """Load the speech model unless that's already happening (safe to call from any thread)."""
        if self._engine_started:
            return
        self._engine_started = True
        if threading.current_thread() is threading.main_thread():
            self._load_engine()
        else:
            self.bridge.call.emit(self._load_engine)

    def show_setup(self, installed: bool = False) -> None:
        from tiro.ui.setup_wizard import SetupWizard

        if self.setup_window is None:
            self.setup_window = SetupWizard(self, installed=installed)
        w = self.setup_window
        w.show()
        w.raise_()
        w.activateWindow()

    def _load_engine(self) -> None:
        """(Re)load the speech model in the background. A newer request cancels an older one's download."""
        self._engine_gen += 1
        gen = self._engine_gen
        if self._download_cancel is not None:
            self._download_cancel.set()
        cancel = self._download_cancel = threading.Event()
        self.engine_ready.clear()
        old, self.engine = self.engine, None
        self.engine_state = "loading"
        self.engine_message = ""
        self.tray.set_state("busy")
        spec = MODELS[self.settings.model]
        device = self.settings.device

        def work():
            nonlocal old
            old = None  # drop the previous model (frees GPU memory) before loading the next
            with self._load_lock:  # one load at a time; superseded ones bail out below
                if gen != self._engine_gen:
                    return
                try:
                    folder = find_model(spec)
                    if folder is None:
                        self.bridge.engine_status.emit(gen, "downloading", f"Downloading {spec.title}…")

                        def progress(done, total):
                            if total:
                                self.bridge.engine_status.emit(
                                    gen, "downloading", f"Downloading {spec.title}… {done * 100 // total}%"
                                )

                        folder = download_model(spec, progress, cancel, variant="int8" if device == "cpu" else "fp32")
                    if gen != self._engine_gen:
                        return
                    eng = ParakeetEngine(folder, device=device)
                    eng.load()
                    eng.set_vocabulary(self.vocab)
                    self.correction.attach_engine(eng)
                except DownloadCancelled:
                    log.info("download of %s cancelled", spec.key)
                    return
                except Exception as exc:
                    log.exception("model load failed")
                    self.bridge.engine_status.emit(gen, "error", f"Could not load the speech model: {exc}")
                    return
                if gen != self._engine_gen:
                    return
                self.engine = eng
                self.engine_ready.set()
                self.bridge.engine_status.emit(gen, "ready", eng.device_label)
                self._configure_language()

        threading.Thread(target=work, name="tiro-model-loader", daemon=True).start()

    @Slot(int, str, str)
    def _on_engine_status(self, gen: int, state: str, message: str) -> None:
        if gen != self._engine_gen:
            return
        self.engine_state = state
        self.engine_message = message
        if state == "ready":
            self.hook.reinstall()  # loading the model may have made Windows drop the keyboard hook
            self.tray.set_state("idle")
            if self.engine is not None:
                self.updates.engine_ready(self.engine)  # confirms this start to the launcher
            self._start_notices()
            if self.engine and self.engine.fallback_reason and self.settings.device != "cpu":
                log.warning("running on CPU: %s", self.engine.fallback_reason)
            if not self.settings.welcome_shown and not self.autostarted:
                self.settings.welcome_shown = True
                self.settings.save()
                how = "Hold" if self.settings.mode == "hold" else "Press"
                self.overlay.notify(f"{APP_NAME} is ready — {how.lower()} {self.settings.hotkey_spec.label} and speak",
                                    "ok", seconds=5.0)
        elif state == "error":
            self.overlay.notify(message, "error", seconds=6.0)
            if self.updates.update_trial:
                # a new version that can't load the speech model: let the launcher go back to the previous one
                log.error("update trial failed: %s", message)
                QTimer.singleShot(500, lambda: os._exit(3))
        self.tray.set_state("idle" if state in ("ready", "error") else "busy")
        if self.settings_window is not None and self.settings_window.isVisible():
            self.settings_window.refresh()

    # ================================================================== settings & UI actions
    def status_line(self) -> str:
        if self.session is not None and self.session.alive:
            return "Listening…"
        if self.engine_state in ("loading", "downloading"):
            return self.engine_message or "Loading speech model…"
        if self.engine_state == "error":
            return "Speech model failed to load"
        how = "hold" if self.settings.mode == "hold" else "press"
        return f"Ready · {how} {self.settings.hotkey_spec.label}"

    def engine_description(self) -> str:
        spec = MODELS[self.settings.model]
        if self.engine_state != "ready" or self.engine is None:
            return f"{spec.title}: {self.engine_message or 'loading…'}"
        text = f"{spec.title} running on {self.engine.device_label}."
        if self.engine.device == "cpu" and self.engine.fallback_reason and self.settings.device != "cpu":
            text += f" GPU unavailable: {self.engine.fallback_reason}."
        return text

    def update_setting(self, key: str, value) -> None:
        if getattr(self.settings, key) == value:
            return
        getattr(self.settings, "_safe", {}).pop(key, None)  # your own choice, even in safe mode
        setattr(self.settings, key, value)
        self.settings.save()
        if key in ("hotkey", "mode", "double_tap_lock"):
            self.machine.configure(self.settings.hotkey_spec, mode=self.settings.mode,
                                   double_tap_lock=self.settings.double_tap_lock)
        elif key == "insertion":
            self.injector.method = value
        elif key in ("model", "device"):
            if self._engine_started:
                self._load_engine()
        elif key == "overlay_position":
            self.overlay.position = value
        elif key == "show_live_text":
            self.overlay.show_text = value
        elif key == "learn_corrections":
            self.learner.enabled = bool(value)
        elif key in ("instant_start", "microphone"):
            self._sync_hub()
        elif key == "dictionary":
            self.correction.set_dictionary(value)
            self._update_boosting()
        elif key == "correction":
            self.correction.set_enabled(bool(value))
            self._configure_language()
        elif key in ("correction_mode", "correction_threshold"):
            self.correction.set_mode(self.settings.correction_mode, self.settings.correction_threshold or None)
        elif key == "history":
            self.correction.set_history_enabled(bool(value))
        elif key in ("ai_correction", "ai_model"):
            self._configure_language()
        elif key == "fix_hotkey":
            self.fix_key.configure(self._fix_spec())
        self.tray.set_state("live" if (self.session and self.session.alive) else
                            ("idle" if self.engine_state in ("ready", "error") else "busy"))

    def add_to_dictionary(self, word: str, notify: bool = False) -> bool:
        word = word.strip()
        if not word or any(w.split("->")[-1].strip().lower() == word.lower() for w in self.settings.dictionary):
            return False
        self.update_setting("dictionary", [*self.settings.dictionary, word])
        if notify:
            self.overlay.notify(f"Learned “{word}”. Tiro will spell it that way from now on.", "ok", seconds=4.0)
        if self.settings_window is not None and self.settings_window.isVisible():
            self.settings_window.refresh()
        return True

    def prompt_add_word(self) -> None:
        from tiro.ui.dialogs import ask_text

        word = ask_text(
            "Add to dictionary",
            "A name, brand or term Tiro should know, spelled the way you want it typed.\n"
            "For stubborn ones you can write a rule, e.g.  super bass -> Supabase",
            placeholder="GeoGuessr",
        )
        if word:
            self.add_to_dictionary(word)

    def capture_hotkey(self, callback) -> None:
        self._capture_cb = callback

        def done(spec):
            self.hook.capture = None
            self.bridge.captured.emit(spec)

        self.hook.capture = HotkeyCapture(done)

    @Slot(object)
    def _on_captured(self, spec) -> None:
        cb, self._capture_cb = self._capture_cb, None
        if spec is not None:
            problem = hotkey_problem(spec)
            if problem:
                self.overlay.notify(problem, "warning", seconds=5.0)
                spec = None
        if cb:
            cb(spec)

    def list_mics(self) -> list[str]:
        try:
            return audio.list_input_devices()
        except Exception:
            log.exception("listing microphones failed")
            return []

    def default_mic_name(self) -> str | None:
        try:
            return audio.default_input_name()
        except Exception:
            return None

    def autostart_enabled(self) -> bool:
        return autostart.is_enabled()

    def set_autostart(self, on: bool) -> None:
        autostart.set_enabled(bool(on))

    def copy_last(self) -> None:
        if self.last_text:
            QGuiApplication.clipboard().setText(self.last_text)

    def show_settings(self, page: str | None = None) -> None:
        from tiro.ui.settings import SettingsWindow

        if self.settings_window is None:
            self.settings_window = SettingsWindow(self)
        w = self.settings_window
        w.refresh()
        if page:
            w.show_page(page)
        w.show()
        w.setWindowState(w.windowState() & ~Qt.WindowState.WindowMinimized)
        w.raise_()
        w.activateWindow()

    def data_folder(self):
        return history_path().parent

    def open_data_folder(self) -> None:
        _open_path(self.data_folder())

    def check_for_updates(self) -> None:
        """'Check now' (tray or Settings): ask the signed update feed right away."""
        self.updates.check_now()
        self.show_settings("updates")

    def updates_action(self) -> None:
        """The tray's update item: restart into a ready update, fetch an available one, or check."""
        st = self.updates.status
        if st.state == "ready":
            self.updates.restart_to_update()
        elif st.state == "available":
            self.updates.open_download_page()
        else:
            self.check_for_updates()

    @Slot(str, str)
    def _on_update_result(self, state: str, value: str) -> None:  # (kept for old callers)
        self.check_for_updates()

    def _start_notices(self) -> None:
        """Once per start: safe mode, a version that was rolled back, or what's new after an update."""
        if getattr(self, "_notices_done", False):
            return
        self._notices_done = True
        from tiro import __version__

        if self.safe_mode:
            self.tray.message(f"{APP_NAME} is in safe mode",
                              "Extras (corrections, learning, GPU) are off for now. Restart Tiro to go back to normal.")
        elif self.rolled_back_from:
            self.tray.message(f"{APP_NAME} went back to {__version__}",
                              f"Version {self.rolled_back_from} didn't start properly on this PC, so Tiro is using "
                              f"{__version__} again and won't try {self.rolled_back_from} again.")
        elif self.settings.setup_done and self.settings.whats_new_seen != __version__:
            if self.settings.whats_new_seen or self.updates.update_trial:
                self.tray.message(f"{APP_NAME} was updated to {__version__}", "Click to see what's new.",
                                  action="whats_new")
            self.settings.whats_new_seen = __version__
            self.settings.save()

    def restart_normally(self) -> None:
        """Leave safe mode: start Tiro again (through the launcher when installed) and quit this copy."""
        import subprocess
        import sys

        if self.updates.relaunch():
            return
        if getattr(sys, "frozen", False):
            subprocess.Popen([sys.executable, "--after-restart"], close_fds=True)
            self.quit()

    def show_whats_new(self) -> None:
        from PySide6.QtWidgets import QMessageBox

        from tiro import __version__
        from tiro.update.whatsnew import section

        box = QMessageBox()
        box.setWindowTitle(f"What's new in {APP_NAME} {__version__}")
        box.setText(section(__version__) or "Fixes and improvements.")
        box.setTextFormat(Qt.TextFormat.MarkdownText)
        box.setStandardButtons(QMessageBox.StandardButton.Ok)
        box.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        box.show()
        self._whats_new_box = box

    def open_logs(self) -> None:
        _open_path(log_dir())

    def restart_as_admin(self) -> None:
        if winutil.relaunch_as_admin(["--after-restart"]):
            self.quit()

    def quit(self) -> None:
        log.info("quitting")
        self.updates.stop()
        if self.session is not None:
            self.session.cancel()
        self.correction.close()
        if self.hub is not None:
            self.hub.stop()
        self.hook.stop()
        self.injector.flush()
        self.tray.tray.hide()
        self.overlay.hide()
        self.qapp.quit()

    def wait_idle(self, timeout: float = 5.0) -> None:
        end = time.monotonic() + timeout
        while self.session is not None and self.session.alive and time.monotonic() < end:
            time.sleep(0.05)
