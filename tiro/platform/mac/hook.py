"""macOS keyboard hook: a Quartz event tap on its own run-loop thread, with the same interface as the Windows
hook (tiro.platform.windows.hook.KeyboardHook).

Key codes are translated to Windows-style VK codes (see keys.py) so the shared HotkeyMachine works unchanged.
An active tap (one that can swallow the hotkey) needs the Accessibility permission; listening needs Input
Monitoring. Mouse-button presses seen by the same tap are forwarded to click listeners (InputContext).
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable

from tiro.hotkey_marks import TIRO_INPUT_MARK
from tiro.platform.mac.keys import MAC_TO_VK, MODIFIER_BITS

log = logging.getLogger(__name__)

KeyHandler = Callable[[int, bool, int, int, bool], bool]  # (vk, down, scan, flags, injected_by_other) -> swallow

_click_listeners: list[Callable[[], None]] = []
last_char: str | None = None  # the character produced by the key event being handled (for the learner)

EVENT_SOURCE_USER_DATA = 42  # kCGEventSourceUserData


def add_click_listener(fn: Callable[[], None]) -> None:
    _click_listeners.append(fn)


class KeyboardHook:
    def __init__(self, handler: KeyHandler, can_reinstall: Callable[[], bool] = lambda: True):
        self.handler = handler
        self.can_reinstall = can_reinstall
        self.capture: Callable[[int, bool], None] | None = None  # set while recording a new hotkey
        self._thread: threading.Thread | None = None
        self._loop = None
        self._tap = None
        self._ready = threading.Event()
        self._mods_down: set[int] = set()  # mac keycodes of modifiers currently held
        self.error: str | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="tiro-event-tap", daemon=True)
        self._thread.start()
        self._ready.wait(5)

    def reinstall(self) -> None:
        """Make sure the tap is enabled (macOS disables taps that were too slow to answer)."""
        import Quartz

        if self._tap is not None:
            Quartz.CGEventTapEnable(self._tap, True)

    def stop(self) -> None:
        import Quartz

        if self._loop is not None:
            Quartz.CFRunLoopStop(self._loop)
        if self._thread:
            self._thread.join(2)

    @property
    def active(self) -> bool:
        return self._tap is not None

    def _run(self) -> None:
        import Quartz

        mask = 0
        for ev in (Quartz.kCGEventKeyDown, Quartz.kCGEventKeyUp, Quartz.kCGEventFlagsChanged,
                   Quartz.kCGEventLeftMouseDown, Quartz.kCGEventRightMouseDown, Quartz.kCGEventOtherMouseDown):
            mask |= Quartz.CGEventMaskBit(ev)
        try:
            tap = Quartz.CGEventTapCreate(Quartz.kCGSessionEventTap, Quartz.kCGHeadInsertEventTap,
                                          Quartz.kCGEventTapOptionDefault, mask, self._callback, None)
            if tap is None:
                # no Accessibility permission: fall back to listening only (the hotkey can't be swallowed)
                tap = Quartz.CGEventTapCreate(Quartz.kCGSessionEventTap, Quartz.kCGHeadInsertEventTap,
                                              Quartz.kCGEventTapOptionListenOnly, mask, self._callback, None)
                if tap is not None:
                    log.warning("event tap is listen-only (grant Accessibility so the hotkey isn't typed)")
            if tap is None:
                self.error = "Tiro needs Input Monitoring and Accessibility permission to see the hotkey."
                log.error(self.error)
                return
            self._tap = tap
            source = Quartz.CFMachPortCreateRunLoopSource(None, tap, 0)
            self._loop = Quartz.CFRunLoopGetCurrent()
            Quartz.CFRunLoopAddSource(self._loop, source, Quartz.kCFRunLoopCommonModes)
            Quartz.CGEventTapEnable(tap, True)
        finally:
            self._ready.set()
        Quartz.CFRunLoopRun()
        self._tap = None

    def _callback(self, proxy, etype, event, refcon):
        import Quartz

        global last_char
        try:
            if etype in (Quartz.kCGEventTapDisabledByTimeout, Quartz.kCGEventTapDisabledByUserInput):
                if self._tap is not None:
                    Quartz.CGEventTapEnable(self._tap, True)  # macOS switched us off for being slow: back on
                return event
            if etype in (Quartz.kCGEventLeftMouseDown, Quartz.kCGEventRightMouseDown, Quartz.kCGEventOtherMouseDown):
                for fn in list(_click_listeners):
                    fn()
                return event
            if Quartz.CGEventGetIntegerValueField(event, EVENT_SOURCE_USER_DATA) == TIRO_INPUT_MARK:
                return event  # typed by Tiro itself
            code = int(Quartz.CGEventGetIntegerValueField(event, Quartz.kCGKeyboardEventKeycode))
            flags = int(Quartz.CGEventGetFlags(event))
            if etype == Quartz.kCGEventFlagsChanged:
                bit = MODIFIER_BITS.get(code)
                if bit is None:
                    return event
                down = bool(flags & bit)
                if code == 0x39:  # caps lock reports its lock state, not the key: treat as a press + release
                    down = code not in self._mods_down
                if down:
                    self._mods_down.add(code)
                else:
                    self._mods_down.discard(code)
            else:
                down = etype == Quartz.kCGEventKeyDown
            vk = MAC_TO_VK.get(code)
            if vk is None:
                return event
            last_char = None
            if etype == Quartz.kCGEventKeyDown:
                try:
                    _n, text = Quartz.CGEventKeyboardGetUnicodeString(event, 4, None, None)
                    last_char = text if text and text.isprintable() else None
                except Exception:
                    last_char = None
            if self.capture is not None:
                self.capture(vk, down)
                return None
            if self.handler(vk, down, code, flags, False):
                return None  # swallowed
        except Exception:  # never let an exception escape into the event system
            log.exception("event tap handler failed")
        return event
