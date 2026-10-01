"""The global hotkey on a native thread (tiro_hook.dll, built from core/src/win_hook.cc).

The Python hook (tiro.platform.windows.hook) needs Python's lock for every key press. While a model loads, that
lock can be held for seconds, Windows gives up on the hook, and a press of the hotkey is lost. The native hook
runs the same state machine (core/src/hotkey.cc, a port of tiro.hotkey.HotkeyMachine) on its own thread, so it
always answers in time; the app reads what happened from a queue.

The objects below mimic the parts of HotkeyMachine / ChordHotkey / KeyboardHook that the app uses, so app.py can
use either implementation.
"""
from __future__ import annotations

import ctypes
import json
import logging
import os
import sys
import threading
from collections.abc import Callable
from pathlib import Path

from tiro.hotkey_marks import TIRO_INPUT_MARK
from tiro.keys import HotkeySpec
from tiro.paths import bundle_dir

log = logging.getLogger(__name__)

DLL = "tiro_hook.dll"
IDLE, PRESSED, TAP_PENDING, LOCK_HELD, LOCKED, TOGGLE_HELD, TOGGLED, WAIT_RELEASE, CHORD = range(9)
_MODS = ((1, "ctrl"), (2, "shift"), (4, "alt"), (8, "win"))


def _dll_path() -> Path | None:
    override = os.environ.get("TIRO_HOOK_DLL")
    if override:
        return Path(override)
    here = Path(__file__).resolve().parents[3]  # the project folder when running from source
    for folder in (bundle_dir(), here / "build" / "native"):
        for name in (DLL, "lib" + DLL):
            if (folder / name).is_file():
                return folder / name
    return None


def _sets(spec: HotkeySpec | None) -> list[list[int]] | None:
    return [sorted(s) for s in spec.vk_sets()] if spec else None


class _Machine:
    """What the app reads from the hotkey machine (state) and tells it (configure, force_idle)."""

    TAP_PENDING = TAP_PENDING

    def __init__(self, owner: NativeHotkeys):
        self._o = owner

    @property
    def state(self) -> int:
        return self._o._lib.tiro_hook_state(self._o._h)

    @property
    def active(self) -> bool:
        return self.state not in (IDLE, WAIT_RELEASE, CHORD)

    @property
    def down(self) -> dict:
        return {}  # not tracked on the Python side (key events carry their modifiers)

    def force_idle(self) -> None:
        self._o._lib.tiro_hook_force_idle(self._o._h)

    def configure(self, spec: HotkeySpec, *, mode: str, double_tap_lock: bool) -> None:
        self._o._config.update(hotkey=_sets(spec), mode=mode, double_tap_lock=double_tap_lock)
        self._o._push_config()


class _Fix:
    def __init__(self, owner: NativeHotkeys):
        self._o = owner

    def configure(self, spec: HotkeySpec | None) -> None:
        self._o._config["fix"] = _sets(spec)
        self._o._push_config()


class NativeHotkeys:
    """Hotkey machine + fix shortcut + hook, all native. Callbacks run on a Python pump thread."""

    def __init__(self, spec: HotkeySpec, fix: HotkeySpec | None, *, mode: str, double_tap_lock: bool,
                 on_action: Callable[[str], None], on_fix: Callable[[], None],
                 on_typed: Callable[[int, int, set[str]], None]):
        path = _dll_path()
        if path is None:
            raise OSError(f"{DLL} not found")
        os.add_dll_directory(str(path.parent))
        lib = ctypes.CDLL(str(path))
        c = ctypes
        lib.tiro_hook_start.restype = c.c_void_p
        lib.tiro_hook_start.argtypes = [c.c_char_p, c.POINTER(c.c_void_p)]
        lib.tiro_hook_configure.argtypes = [c.c_void_p, c.c_char_p]
        lib.tiro_hook_set_speech.argtypes = [c.c_void_p, c.c_int]
        lib.tiro_hook_set_capture.argtypes = [c.c_void_p, c.c_int]
        lib.tiro_hook_force_idle.argtypes = [c.c_void_p]
        lib.tiro_hook_reinstall.argtypes = [c.c_void_p]
        lib.tiro_hook_state.argtypes = [c.c_void_p]
        lib.tiro_hook_wait.argtypes = [c.c_void_p, c.c_int, c.POINTER(c.c_void_p)]
        lib.tiro_hook_stop.argtypes = [c.c_void_p]
        lib.tiro_hook_free.argtypes = [c.c_void_p]
        self._lib = lib
        self.on_action, self.on_fix, self.on_typed = on_action, on_fix, on_typed
        self._config = {"hotkey": _sets(spec), "fix": _sets(fix), "mode": mode,
                        "double_tap_lock": double_tap_lock, "mark": TIRO_INPUT_MARK}
        self._capture: Callable[[int, bool], None] | None = None
        self._h = None
        self._running = False
        self._thread: threading.Thread | None = None
        self.machine = _Machine(self)
        self.fix_key = _Fix(self)
        self.path = path

    # --------------------------------------------------------------------------------- KeyboardHook interface
    def start(self) -> None:
        err = ctypes.c_void_p()
        h = self._lib.tiro_hook_start(json.dumps(self._config).encode(), ctypes.byref(err))
        if not h:
            msg = ctypes.string_at(err.value).decode("utf-8", "replace") if err.value else "unknown error"
            if err.value:
                self._lib.tiro_hook_free(err)
            raise OSError(f"native hook failed to start: {msg}")
        self._h = h
        self._running = True
        self._thread = threading.Thread(target=self._pump, name="tiro-hotkey-events", daemon=True)
        self._thread.start()
        log.info("keyboard hook: native (%s)", self.path.name)

    def stop(self) -> None:
        self._running = False
        if self._thread is not None:
            self._thread.join(2)
        if self._h:
            self._lib.tiro_hook_stop(self._h)
            self._h = None

    def reinstall(self) -> None:
        if self._h:
            self._lib.tiro_hook_reinstall(self._h)

    @property
    def capture(self) -> Callable[[int, bool], None] | None:
        return self._capture

    @capture.setter
    def capture(self, fn: Callable[[int, bool], None] | None) -> None:
        self._capture = fn
        if self._h:
            self._lib.tiro_hook_set_capture(self._h, 1 if fn is not None else 0)

    def set_speech(self, heard: bool) -> None:
        if self._h:
            self._lib.tiro_hook_set_speech(self._h, 1 if heard else 0)

    # --------------------------------------------------------------------------------------------- internals
    def _push_config(self) -> None:
        if self._h:
            self._lib.tiro_hook_configure(self._h, json.dumps(self._config).encode())

    def _pump(self) -> None:
        out = ctypes.c_void_p()
        while self._running:
            if not self._lib.tiro_hook_wait(self._h, 250, ctypes.byref(out)) or not out.value:
                continue
            try:
                events = json.loads(ctypes.string_at(out.value).decode("utf-8", "replace"))
            finally:
                self._lib.tiro_hook_free(out)
            for ev in events:
                try:
                    self._dispatch(ev)
                except Exception:
                    log.exception("hotkey event handler failed: %s", ev)

    def _dispatch(self, ev: dict) -> None:
        if "action" in ev:
            self.on_action(ev["action"])
        elif "fire" in ev:
            self.on_fix()
        elif "key" in ev:
            vk, scan, mods = ev["key"]
            self.on_typed(vk, scan, {name for bit, name in _MODS if mods & bit})
        elif "capture" in ev:
            fn = self._capture
            if fn is not None:
                vk, down = ev["capture"]
                fn(vk, bool(down))
        elif ev.get("reinstalled"):
            log.debug("keyboard hook re-registered")


def available() -> bool:
    return sys.platform == "win32" and _dll_path() is not None
