"""Global hotkey: a low-level keyboard hook (WH_KEYBOARD_LL) driving a small, testable state machine.

Hold mode:   press and hold the hotkey to talk, release to finish. A quick double-tap locks
             hands-free dictation; tap once more to finish.
Toggle mode: tap to start, tap again to finish.
Esc cancels an active dictation. The hotkey is swallowed so apps never see it, unless it is used as part
of a chord (e.g. Right Ctrl + C) within the first moments of pressing it; then it is replayed to the app.
"""

from __future__ import annotations

import logging
import sys
import threading
import time
from collections.abc import Callable

from tiro.keys import ALL_MODIFIERS, MODIFIER_VKS, VK_ESCAPE, HotkeySpec

log = logging.getLogger(__name__)

TAP_MAX_SEC = 0.28  # a press shorter than this counts as a tap
DOUBLE_TAP_SEC = 0.40  # max gap between the two taps of a double-tap
CHORD_WINDOW_SEC = 0.45  # another key pressed this soon after the hotkey means "this was a shortcut"
from tiro.hotkey_marks import TIRO_INPUT_MARK  # noqa: E402,F401  (re-exported)

Action = str  # "start" | "stop" | "cancel" | "lock"


class HotkeyMachine:
    """Pure hotkey logic. Feed it physical key events; it returns whether to swallow each one."""

    IDLE, PRESSED, TAP_PENDING, LOCK_HELD, LOCKED, TOGGLE_HELD, TOGGLED, WAIT_RELEASE, CHORD = range(9)

    def __init__(
        self,
        spec: HotkeySpec,
        on_action: Callable[[Action], None],
        replay: Callable[[list[tuple[int, int, int, bool]]], None],
        *,
        mode: str = "hold",
        double_tap_lock: bool = True,
        clock: Callable[[], float] = time.monotonic,
        schedule: Callable[[float, Callable[[], None]], None] | None = None,
        speech_started: Callable[[], bool] = lambda: True,
    ):
        self.on_action = on_action
        self.replay = replay
        self.clock = clock
        self.speech_started = speech_started  # has the current dictation heard any speech yet?
        self.schedule = schedule or _thread_timer
        self._lock = threading.RLock()
        self.down: dict[int, tuple[int, int]] = {}  # vk -> (scan, flags) of physically held keys
        self.configure(spec, mode=mode, double_tap_lock=double_tap_lock)

    def configure(self, spec: HotkeySpec, *, mode: str, double_tap_lock: bool) -> None:
        with self._lock:
            self.spec = spec
            self.sets = spec.vk_sets()
            self.spec_vks = frozenset().union(*self.sets)
            self.mode = mode
            self.double_tap_lock = double_tap_lock
            self.state = self.IDLE
            self.trigger: int | None = None
            self.t_press = 0.0
            self._tap_token = 0

    # ------------------------------------------------------------------ helpers
    @property
    def active(self) -> bool:
        return self.state not in (self.IDLE, self.WAIT_RELEASE, self.CHORD)

    def _complete(self) -> bool:
        return all(any(vk in self.down for vk in s) for s in self.sets)

    def _extra_keys_down(self) -> bool:
        return any(vk not in self.spec_vks for vk in self.down)

    def _combo_broken(self, vk: int) -> bool:
        """True if releasing vk leaves the combo incomplete."""
        return vk in self.spec_vks and not self._complete()

    def _emit(self, action: Action) -> None:
        try:
            self.on_action(action)
        except Exception:  # pragma: no cover
            log.exception("hotkey action handler failed")

    # ------------------------------------------------------------------ events
    def on_key(self, vk: int, down: bool, scan: int = 0, flags: int = 0) -> bool:
        with self._lock:
            repeat = down and vk in self.down
            if down:
                self.down[vk] = (scan, flags)
            else:
                self.down.pop(vk, None)
            return self._handle(vk, down, repeat, scan, flags)

    def _handle(self, vk: int, down: bool, repeat: bool, scan: int, flags: int) -> bool:
        now = self.clock()
        st = self.state
        is_trigger = vk == self.trigger

        if st == self.CHORD:
            if not any(v in self.down for v in self.spec_vks):
                self.state = self.IDLE
                self.trigger = None
            return False

        if st == self.WAIT_RELEASE:
            if is_trigger:
                if not down:
                    self.state = self.IDLE
                    self.trigger = None
                return True
            if vk == VK_ESCAPE:
                return True
            return False

        if st == self.IDLE:
            if down and not repeat and vk in self.spec_vks and self._complete() and not self._extra_keys_down():
                self.trigger = vk
                self.t_press = now
                self.state = self.PRESSED if self.mode == "hold" else self.TOGGLE_HELD
                self._emit("start")
                return True
            return False

        # From here on a dictation is running (or a tap is pending).
        if down and vk == VK_ESCAPE and not is_trigger:
            self._emit("cancel")
            self.state = self.WAIT_RELEASE if self.trigger in self.down else self.IDLE
            if self.state == self.IDLE:
                self.trigger = None
            return True

        if st in (self.PRESSED, self.TOGGLE_HELD):
            if is_trigger:
                if down:
                    return True  # auto-repeat
                held = now - self.t_press
                if st == self.TOGGLE_HELD:
                    self.state = self.TOGGLED
                elif self.double_tap_lock and held < TAP_MAX_SEC:
                    self.state = self.TAP_PENDING
                    self._tap_token += 1
                    token = self._tap_token
                    self.schedule(DOUBLE_TAP_SEC, lambda: self._tap_expired(token))
                else:
                    self.state = self.IDLE
                    self.trigger = None
                    self._emit("stop")
                return True
            if not down and self._combo_broken(vk):
                # a modifier of a multi-key hotkey was released: finish, but swallow the trigger's release
                self._emit("stop" if self.mode == "hold" else "cancel")
                self.state = self.WAIT_RELEASE
                return False
            chord = now - self.t_press < CHORD_WINDOW_SEC or not self.speech_started()
            if down and not repeat and vk not in self.spec_vks and chord:
                # It was a shortcut such as Right Ctrl + C (or Shift + letter when Shift is the hotkey):
                # undo the dictation and hand the keys to the app. Once the user is talking, keys pass through.
                self._emit("cancel")
                self.state = self.CHORD
                trig_scan, trig_flags = self.down.get(self.trigger, (0, 0))
                self.replay([(self.trigger, trig_scan, trig_flags, True), (vk, scan, flags, True)])
                return True
            return False

        if st == self.TAP_PENDING:
            if is_trigger and down and not repeat:
                self.state = self.LOCK_HELD
                self._tap_token += 1
                self._emit("lock")
                return True
            if down and not repeat:
                self._tap_token += 1
                self.state = self.IDLE
                self.trigger = None
                self._emit("cancel")
            return False

        if st == self.LOCK_HELD:
            if is_trigger:
                if not down:
                    self.state = self.LOCKED
                return True
            return False

        if st in (self.LOCKED, self.TOGGLED):
            if is_trigger or (vk in self.spec_vks and len(self.sets) == 1):
                if down and not repeat:
                    self.trigger = vk
                    self.state = self.WAIT_RELEASE
                    self._emit("stop")
                return True
            if down and not repeat and vk in self.spec_vks and self._complete():
                self.trigger = vk
                self.state = self.WAIT_RELEASE
                self._emit("stop")
                return True
            return False
        return False

    def _tap_expired(self, token: int) -> None:
        with self._lock:
            if self.state == self.TAP_PENDING and token == self._tap_token:
                self.state = self.IDLE
                self.trigger = None
                self._emit("cancel")

    def force_idle(self) -> None:
        """Called when a dictation ends for reasons other than the hotkey (e.g. an error)."""
        with self._lock:
            if self.state in (self.PRESSED, self.TOGGLE_HELD, self.LOCK_HELD):
                self.state = self.WAIT_RELEASE
            elif self.state != self.WAIT_RELEASE:
                self.state = self.IDLE
                self.trigger = None


class ChordHotkey:
    """A plain shortcut such as Ctrl + Alt + F: fires when its last key goes down while the rest are held
    (and no other modifier is). That key press and its release are swallowed; the modifiers pass through."""

    def __init__(self, spec: HotkeySpec | None, on_fire: Callable[[], None]):
        self.on_fire = on_fire
        self._down: set[int] = set()
        self._swallowed: set[int] = set()
        self.configure(spec)

    def configure(self, spec: HotkeySpec | None) -> None:
        self.spec = spec
        sets = spec.vk_sets() if spec else []
        self._mods = [s for s in sets if s <= ALL_MODIFIERS]
        self._keys = [s for s in sets if not s <= ALL_MODIFIERS]

    def on_key(self, vk: int, down: bool) -> bool:
        if not down:
            self._down.discard(vk)
            if vk in self._swallowed:
                self._swallowed.discard(vk)
                return True
            return False
        repeat = vk in self._down
        self._down.add(vk)
        if not self._keys or vk not in self._keys[-1]:
            return False
        if repeat:
            return vk in self._swallowed
        if not all(any(v in self._down for v in m) for m in self._mods):
            return False
        allowed = frozenset().union(*self._mods)
        if any(v in ALL_MODIFIERS and v not in allowed for v in self._down):
            return False
        if not all(any(v in self._down for v in k) for k in self._keys[:-1]):
            return False
        self._swallowed.add(vk)
        try:
            self.on_fire()
        except Exception:  # pragma: no cover
            log.exception("shortcut handler failed")
        return True


def _thread_timer(delay: float, fn: Callable[[], None]) -> None:
    t = threading.Timer(delay, fn)
    t.daemon = True
    t.start()


# ====================================================================== Win32 hook

class HotkeyCapture:
    """Collects a new hotkey while the user presses it (all keys are swallowed during capture)."""

    def __init__(self, on_done: Callable[[HotkeySpec | None], None]):
        self.on_done = on_done
        self.pressed: list[int] = []
        self.held: set[int] = set()
        self.finished = False

    def __call__(self, vk: int, down: bool) -> None:
        if self.finished:
            return
        if down:
            if vk == VK_ESCAPE and not self.pressed:
                self.finished = True
                self.on_done(None)
                return
            if vk not in self.held:
                self.held.add(vk)
                if vk not in self.pressed:
                    self.pressed.append(vk)
        else:
            self.held.discard(vk)
            if not self.held and self.pressed:
                self.finished = True
                keys = self.pressed
                if len(keys) > 1 and all(k in ALL_MODIFIERS for k in keys) and len(keys) > 3:
                    keys = keys[:3]
                self.on_done(HotkeySpec.from_captured(keys))


def modifiers_held(down: dict[int, tuple[int, int]]) -> set[str]:
    return {name for name, vks in MODIFIER_VKS.items() if any(v in down for v in vks)}


KeyHandler = Callable[[int, bool, int, int, bool], bool]  # (vk, down, scan, flags, injected_by_other) -> swallow

if sys.platform == "darwin":
    from tiro.platform.mac.hook import KeyboardHook  # noqa: E402,F401
else:
    from tiro.platform.windows.hook import KeyboardHook  # noqa: E402,F401
