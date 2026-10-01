"""One dictation: microphone -> VAD -> streaming ASR -> text formatting -> injection, on its own thread."""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, replace

import numpy as np

from tiro import secure, winutil
from tiro.asr import ParakeetEngine
from tiro.audio import AudioCapture, MicError
from tiro.context import InputContext
from tiro.injector import Injector
from tiro.stream import StreamingTranscriber, StreamParams, StreamUpdate
from tiro.textproc import FormatOptions, TextAssembler, hold_back
from tiro.vad import FRAME, SileroVad, SpeechGate

log = logging.getLogger(__name__)

RELEASE_TAIL_SEC = 0.25  # keep listening briefly after the key is released (people let go mid-syllable)
LATE_FIX_MAX_AGE = 4.0  # a correction that arrives after its words were typed may replace them for this long
LATE_FIX_MAX_CHARS = 400  # ... and only if it means retyping at most this much
SILENT_PEAK = 3e-4  # below -70 dBFS for a whole dictation: the device is muted, virtual, or not the right one


@dataclass
class SessionCallbacks:
    on_state: Callable[[str], None]  # "listening" | "loading" | "finishing" | "done" | "cancelled" | "error"
    on_text: Callable[[str, str], None]  # (committed text so far, pending hypothesis)
    on_notice: Callable[[str, str], None]  # (kind, message) for warnings/errors


class DictationSession:
    def __init__(
        self,
        *,
        engine: ParakeetEngine,
        engine_ready: threading.Event,
        vad: SileroVad,
        capture: AudioCapture,
        injector: Injector,
        context: InputContext,
        fmt: FormatOptions,
        learner=None,
        smart_spacing: bool,
        mode: str,
        hands_free_pause_sec: float,
        callbacks: SessionCallbacks,
        params: StreamParams | None = None,
        start_delay: float = 0.0,
        previous: DictationSession | None = None,
        correction=None,
        correction_budget_ms: float = 150.0,
        late_fixes: bool = True,
        on_finish: Callable[[DictationSession], None] | None = None,
    ):
        self.start_delay = start_delay  # wait this long before opening the mic (lets shortcut chords cancel first)
        self._previous = previous  # an earlier dictation that may still be typing its last words
        self.engine = engine
        self.engine_ready = engine_ready
        self.vad = vad
        self.capture = capture
        self.injector = injector
        self.context = context
        self.learner = learner  # tiro.learn.CorrectionLearner: notices words the user retypes
        self.fmt = fmt
        self.mode = mode  # "hold" | "locked" | "toggle"
        self.hands_free_pause_sec = hands_free_pause_sec
        self.cb = callbacks
        self.params = replace(params) if params else StreamParams()
        self.target_hwnd = winutil.foreground_window()
        needs_space, mid = context.snapshot(self.target_hwnd) if smart_spacing else (False, False)
        self._asm_init = (needs_space, mid)
        self.assembler = TextAssembler(fmt, needs_space=needs_space, mid_sentence=mid)
        self._commits: list[tuple[int, list, list]] = []  # (correction batch, words as heard, changes applied)
        self._type_lock = threading.Lock()  # typing a commit vs. swapping in a late fix
        self._last_inject_time = 0.0
        self.late_fixes = 0
        self._stop = threading.Event()
        self._cancel = threading.Event()
        self._thread = threading.Thread(target=self._run, name="tiro-session", daemon=True)
        self._warned_uipi = False
        self._decode_avg: float | None = None
        self.speech_heard = False  # set once VAD hears speech (lets the hotkey tell dictation from a shortcut)
        self.error: str | None = None
        self.started = time.monotonic()
        self.correction = correction  # tiro.correct.CorrectionRun, or None when correction is off
        self.correction_budget_ms = correction_budget_ms
        if correction is not None and late_fixes:
            correction.on_late = self._on_late
        self.on_finish = on_finish  # called once when the dictation is over, whatever the outcome
        self.app_name = ""  # target app ("chrome", "winword"), for context
        self.window_title = ""
        self.secure: bool | None = None  # typing into a password field? None until known (or unknowable)
        self._probed = threading.Event()
        self.correction_ms = 0.0  # correction time spent before text could be typed, this dictation
        self.end_latency_ms: float | None = None  # end of audio -> last words typed
        self.end_correction_ms = 0.0  # ... of which the correction pass
        self.inject_hwnd = 0  # window the text went into
        self.last_inject_events = -1  # InputContext.events right after our last insertion
        self.raw_text = ""  # what the recogniser heard, before correction (for "Fix last transcription")

    # ------------------------------------------------------------------ control (any thread)
    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def cancel(self) -> None:
        self._cancel.set()

    def lock(self) -> None:
        self.mode = "locked"

    def join(self, timeout: float | None = None) -> None:
        self._thread.join(timeout)

    @property
    def alive(self) -> bool:
        return self._thread.is_alive()

    @property
    def level(self) -> float:
        return self.capture.level

    @property
    def text(self) -> str:
        return self.assembler.text

    # ------------------------------------------------------------------ worker
    def _run(self) -> None:
        try:
            if self.start_delay and self._cancel.wait(self.start_delay):
                self._previous = None
                self.cb.on_state("cancelled")  # it was a shortcut; never touched the microphone
                return
            threading.Thread(target=self._probe_target, name="tiro-target", daemon=True).start()
            try:
                self.capture.open()
            except MicError as exc:
                self.error = str(exc)
                self.cb.on_notice("error", str(exc))
                self.cb.on_state("error")
                return
            try:
                self._loop()
            except Exception as exc:
                log.exception("dictation session crashed")
                self.error = str(exc)
                self.cb.on_notice("error", f"Dictation failed: {exc}")
                self.cb.on_state("error")
            finally:
                self.capture.close()
                self._previous = None
        finally:
            if self.correction is not None:
                self.correction.close()
            if self.on_finish is not None:
                try:
                    self.on_finish(self)
                except Exception:
                    log.exception("on_finish failed")

    def _probe_target(self) -> None:
        """Which app are we typing into, and is it a password field? (UI Automation can take a moment.)"""
        try:
            self.app_name = winutil.window_app(self.target_hwnd)
            self.window_title = winutil.window_title(self.target_hwnd)
            self.secure = secure.is_secure_field(self.target_hwnd)
        except Exception:
            log.exception("probing the target field failed")
        finally:
            if self.secure:
                log.info("secure field: no correction, learning or history for this dictation")
            self._probed.set()

    @property
    def private(self) -> bool:
        """True unless the target is known to be an ordinary field (password or unknown: never learn)."""
        return self.secure is not False

    def _may_correct(self) -> bool:
        if self.correction is None:
            return False
        if not self._probed.is_set():
            self._probed.wait(0.15)
        return self.secure is False

    def _loop(self) -> None:
        stream = StreamingTranscriber(self._transcribe, self.params, hold_back=self._hold_back)
        self.vad.reset()
        gate = SpeechGate()
        pending = np.zeros(0, dtype=np.float32)
        loading = not self.engine_ready.is_set()
        self.cb.on_state("loading" if loading else "listening")
        if not loading:
            threading.Thread(target=self.engine.ping, daemon=True).start()  # wake the GPU while we listen
        stop_at: float | None = None
        while True:
            if self._cancel.is_set():
                self.cb.on_state("cancelled")
                return
            chunk = self.capture.read(timeout=0.03)
            if chunk is not None and chunk.size:
                pending = np.concatenate((pending, chunk)) if pending.size else chunk
                n = pending.size // FRAME * FRAME
                for i in range(0, n, FRAME):
                    frame = pending[i : i + FRAME]
                    speech = gate.update(self.vad(frame))
                    self.speech_heard = self.speech_heard or speech
                    stream.push(frame, speech)
                pending = pending[n:]
            if loading and self.engine_ready.is_set():
                loading = False
                self.cb.on_state("listening")
            if self._stop.is_set():
                if stop_at is None:
                    stop_at = time.monotonic() + RELEASE_TAIL_SEC
                    self.cb.on_state("finishing")
                if time.monotonic() >= stop_at:
                    break
                continue
            if loading:
                continue
            if stream.due():
                self._apply(stream.step())
            if (
                self.mode != "hold"
                and stream.has_speech
                and stream.silence_run >= self.hands_free_pause_sec * stream.sr
            ):
                self._apply(stream.finalize())
        # drain what the driver still holds, then finish the utterance
        for chunk in self.capture.drain():
            pending = np.concatenate((pending, chunk)) if pending.size else chunk
        n = pending.size // FRAME * FRAME
        for i in range(0, n, FRAME):
            frame = pending[i : i + FRAME]
            stream.push(frame, gate.update(self.vad(frame)))
        if self._cancel.is_set():
            self.cb.on_state("cancelled")
            return
        if not self.engine_ready.wait(timeout=120):
            self.cb.on_notice("error", "The speech model is still loading. Try again in a moment.")
            self.cb.on_state("error")
            return
        audio_end = time.perf_counter()
        before = self.correction_ms
        self._apply(stream.finalize())
        self.end_latency_ms = (time.perf_counter() - audio_end) * 1000
        self.end_correction_ms = self.correction_ms - before
        if time.monotonic() - self.started > 1.5 and self.capture.peak < SILENT_PEAK and not self.assembler.text:
            name = self.capture.opened_name or "the microphone"
            self.cb.on_notice("warning", f"No sound from {name}. Pick another microphone in the tray menu.")
        self.cb.on_state("done")

    def _hold_back(self, committable, rest) -> int:
        """Words to keep back from this commit: the start of a voice command, or a doubtful word whose
        neighbour isn't settled yet (a name split across two commits could not be corrected)."""
        n = hold_back([w.text for w in committable], self.fmt)
        if self.correction is not None and self.secure is False:
            n = max(n, self.correction.hold_back(committable, rest))
        return n

    # ------------------------------------------------------------------ late corrections
    def _replay(self, commits) -> TextAssembler:
        """The text this dictation would have typed for these commits (same formatting, same start)."""
        from tiro.correct.corrector import apply_changes

        needs_space, mid = self._asm_init
        asm = TextAssembler(self.fmt, needs_space=needs_space, mid_sentence=mid)
        for _batch, raw, changes in commits:
            asm.add([w.text for w in apply_changes(raw, changes)])
        return asm

    def _on_late(self, batch: int, changes) -> None:
        """A correction that arrived after its words were typed. It replaces them only if nothing has
        happened since (no typing, clicking or switching windows) and the swap is a clean edit at the end
        of what Tiro typed; otherwise the text stays exactly as it was typed."""
        from tiro.correct.corrector import validate

        with self._type_lock:
            idx = next((i for i, c in enumerate(self._commits) if c[0] == batch), None)
            if idx is None or self._cancel.is_set() or self.secure is not False:
                return
            if self.context.events != self.last_inject_events:
                log.info("late correction skipped: you typed or clicked since")
                return
            if winutil.foreground_window() != self.inject_hwnd or time.monotonic() - self._last_inject_time > LATE_FIX_MAX_AGE:
                log.info("late correction skipped: focus moved or too late")
                return
            b, raw, applied = self._commits[idx]
            merged = validate(raw, applied + changes, self.correction.c.knowledge)
            if len(merged) <= len(applied):
                return
            commits = list(self._commits)
            commits[idx] = (b, raw, merged)
            asm = self._replay(commits)
            old, new = self.assembler.text, asm.text
            p = 0
            while p < min(len(old), len(new)) and old[p] == new[p]:
                p += 1
            if old == new or len(old) - p > LATE_FIX_MAX_CHARS:
                return
            if not self.injector.replace_tail(len(old) - p, new[p:]):
                return
            self._commits, self.assembler = commits, asm
            if new[p:]:
                self.context.note_inject(new[p:], self.inject_hwnd)
            self.last_inject_events = self.context.events
            self.late_fixes += 1
            if self.learner is not None:
                self.learner.note_click()  # its idea of the text is out of date now
            log.info("late correction applied (%d chars retyped)", len(new) - p)
        self.cb.on_text(self.assembler.text, "")

    def _transcribe(self, audio: np.ndarray):
        t0 = time.perf_counter()
        words = self.engine.transcribe(audio)
        took = time.perf_counter() - t0
        # pace partial decodes to the hardware: 0.4 s on a GPU, slower on a CPU so we never queue up work
        self._decode_avg = took if self._decode_avg is None else 0.8 * self._decode_avg + 0.2 * took
        self.params.step_sec = min(1.2, max(0.4, 1.6 * self._decode_avg))
        return words

    def _apply(self, upd: StreamUpdate) -> None:
        if self._cancel.is_set():
            return
        if upd.committed:
            raw = upd.committed
            self.raw_text += (" " if self.raw_text else "") + " ".join(w.text for w in raw)
            batch, changes, words = -1, [], raw
            if self._may_correct():
                res = self.correction.correct(raw, after=[w.text for w in upd.pending], app=self.app_name,
                                              budget_ms=self.correction_budget_ms)
                words, batch, changes = res.words, res.batch, res.changes
                self.correction_ms += res.ms
            if self._previous is not None:  # keep text in order if the last dictation is still finishing
                self._previous.join(3.0)
                self._previous = None
            with self._type_lock:
                self._commits.append((batch, raw, changes))
                text = self.assembler.add([w.text for w in words])
                if text:
                    hwnd = winutil.foreground_window()
                    if not self._warned_uipi and winutil.foreground_blocked_by_uipi():
                        self._warned_uipi = True
                        self.cb.on_notice(
                            "warning", "That window runs as administrator. Restart Tiro as admin to type into it."
                        )
                    self.injector.insert(text)
                    self.context.note_inject(text, hwnd)
                    self.inject_hwnd = hwnd
                    self.last_inject_events = self.context.events
                    self._last_inject_time = time.monotonic()
                    if self.learner is not None and not self.private:
                        self.learner.note_injection(text, hwnd)
        if upd.pending and not upd.final and self.correction is not None and self.secure is False:
            self.correction.prefetch_async(upd.pending, self.app_name)
        self.cb.on_text(self.assembler.text, " ".join(w.text for w in upd.pending))
