"""Insert text into whatever control has keyboard focus.

"type"  - SendInput with KEYEVENTF_UNICODE, one atomic batch per commit. Works in browsers, Electron
          and native apps, never touches the clipboard. Line breaks are sent as Shift+Enter so chat
          apps insert a newline instead of sending the message.
"paste" - puts the text on the clipboard (flagged so it stays out of clipboard history), presses Ctrl+V
          (Shift+Insert in terminals) and restores the previous clipboard afterwards.
"""

from __future__ import annotations

import ctypes
import logging
import os
import threading
import time
from ctypes import wintypes

from tiro import winutil
from tiro.hotkey import TIRO_INPUT_MARK
from tiro.keys import ALL_MODIFIERS, VK_BACK, VK_CONTROL, VK_INSERT, VK_LMENU, VK_LWIN, VK_MASK, VK_RETURN, VK_RMENU, VK_RWIN, VK_SHIFT

log = logging.getLogger(__name__)

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

INPUT_KEYBOARD = 1
KEYEVENTF_EXTENDEDKEY, KEYEVENTF_KEYUP, KEYEVENTF_UNICODE = 0x1, 0x2, 0x4
_EXTENDED_VKS = {0xA3, 0xA5, 0x5B, 0x5C, 0x2D, 0x2E, 0x24, 0x23, 0x21, 0x22, 0x25, 0x26, 0x27, 0x28, 0x6F, 0x90}


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT)]


class INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]


user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
user32.SendInput.restype = wintypes.UINT
user32.MapVirtualKeyW.argtypes = [wintypes.UINT, wintypes.UINT]
user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
user32.GetAsyncKeyState.restype = ctypes.c_short


def _vk_event(vk: int, up: bool = False, scan: int | None = None, flags: int = 0) -> INPUT:
    if scan is None:
        scan = user32.MapVirtualKeyW(vk, 0)
    f = (KEYEVENTF_KEYUP if up else 0) | (KEYEVENTF_EXTENDEDKEY if (vk in _EXTENDED_VKS or flags & 1) else 0)
    return INPUT(type=INPUT_KEYBOARD, ki=KEYBDINPUT(vk, scan, f, 0, TIRO_INPUT_MARK))


def _unicode_events(ch: str) -> list[INPUT]:
    out = []
    data = ch.encode("utf-16-le")
    for i in range(0, len(data), 2):
        unit = int.from_bytes(data[i : i + 2], "little")
        for up in (False, True):
            flags = KEYEVENTF_UNICODE | (KEYEVENTF_KEYUP if up else 0)
            out.append(INPUT(type=INPUT_KEYBOARD, ki=KEYBDINPUT(0, unit, flags, 0, TIRO_INPUT_MARK)))
    return out


def send(events: list[INPUT]) -> int:
    if not events:
        return 0
    arr = (INPUT * len(events))(*events)
    sent = user32.SendInput(len(events), arr, ctypes.sizeof(INPUT))
    if sent != len(events):
        log.warning("SendInput delivered %d/%d events (err %d)", sent, len(events), ctypes.get_last_error())
    return sent


def _held_modifiers() -> list[int]:
    return [vk for vk in sorted(ALL_MODIFIERS) if vk not in (0x10, 0x11, 0x12) and user32.GetAsyncKeyState(vk) & 0x8000]


def _release_modifiers() -> list[INPUT]:
    """Key-ups for any modifier the user is physically holding (e.g. part of a multi-key hotkey)."""
    held = _held_modifiers()
    events: list[INPUT] = []
    if any(vk in (VK_LMENU, VK_RMENU, VK_LWIN, VK_RWIN) for vk in held):
        events += [_vk_event(VK_MASK), _vk_event(VK_MASK, up=True)]
    events += [_vk_event(vk, up=True) for vk in held]
    return events


def replay_keys(keys: list[tuple[int, int, int, bool]]) -> None:
    """Re-send swallowed physical key events (vk, scan, flags, down) to the focused app."""
    send([_vk_event(vk, up=not down, scan=scan, flags=flags) for vk, scan, flags, down in keys])


def _test_guard_ok() -> bool:
    """Automated tests only: never type into anything but the test's own window (by class and/or title)."""
    cls = os.environ.get("TIRO_TEST_TARGET_CLASS")
    title = os.environ.get("TIRO_TEST_TARGET_TITLE")
    hwnd_file = os.environ.get("TIRO_TEST_TARGET_HWND_FILE")  # one window handle per line, written by the test
    if not cls and not title and not hwnd_file:
        return True
    fg = winutil.foreground_window()
    if hwnd_file:
        try:
            allowed = {int(x) for x in open(hwnd_file, encoding="utf-8").read().split() if x.strip()}
        except (OSError, ValueError):
            allowed = set()
        return fg in allowed
    if cls and winutil.window_class(fg) != cls:
        return False
    return not title or title in winutil.window_title(fg)


class Injector:
    def __init__(self, method: str = "type"):
        self.method = method
        self._clip_lock = threading.Lock()
        self._restore_timer: threading.Timer | None = None
        self._saved_clip: list[tuple[int, bytes]] | None = None
        self._our_seq = 0

    def insert(self, text: str) -> bool:
        if not text:
            return True
        if not _test_guard_ok():
            log.warning("test guard: focus is not on the test window; dropped %d chars", len(text))
            return False
        try:
            if self.method == "paste":
                return self._paste(text)
            return self._type(text)
        except Exception:
            log.exception("text insertion failed")
            return False

    def replace_tail(self, delete: int, text: str) -> bool:
        """Backspace over the last `delete` characters (text Tiro typed), then insert `text` in their place."""
        if not _test_guard_ok():
            log.warning("test guard: focus is not on the test window; replacement dropped")
            return False
        events = _release_modifiers()
        for _ in range(max(0, delete)):
            events += [_vk_event(VK_BACK), _vk_event(VK_BACK, up=True)]
        if events and send(events) != len(events):
            return False
        return self.insert(text) if text else True

    # ------------------------------------------------------------------ typing
    def _type(self, text: str) -> bool:
        events = _release_modifiers()
        for ch in text.replace("\r\n", "\n"):
            if ch == "\n":
                events += [_vk_event(VK_SHIFT), _vk_event(VK_RETURN), _vk_event(VK_RETURN, up=True),
                           _vk_event(VK_SHIFT, up=True)]
            elif ch == "\t":
                events += [_vk_event(0x09), _vk_event(0x09, up=True)]
            else:
                events += _unicode_events(ch)
        return send(events) == len(events)

    # ------------------------------------------------------------------ clipboard
    def _paste(self, text: str) -> bool:
        with self._clip_lock:
            if self._restore_timer is not None:
                self._restore_timer.cancel()  # a paste is already pending restore: keep its saved copy
            elif self._saved_clip is None:
                self._saved_clip = winutil.clipboard_snapshot()
            if not winutil.set_clipboard_text(text.replace("\n", "\r\n")):
                return False
            self._our_seq = user32.GetClipboardSequenceNumber()
            if winutil.foreground_is_terminal():
                keys = [_vk_event(VK_SHIFT), _vk_event(VK_INSERT), _vk_event(VK_INSERT, up=True),
                        _vk_event(VK_SHIFT, up=True)]
            else:
                keys = [_vk_event(VK_CONTROL), _vk_event(0x56), _vk_event(0x56, up=True), _vk_event(VK_CONTROL, up=True)]
            ok = send(_release_modifiers() + keys) > 0
            self._restore_timer = threading.Timer(0.6, self._restore)
            self._restore_timer.daemon = True
            self._restore_timer.start()
            return ok

    def _restore(self) -> None:
        with self._clip_lock:
            self._restore_timer = None
            saved, self._saved_clip = self._saved_clip, None
            if saved is None:
                return
            if user32.GetClipboardSequenceNumber() != self._our_seq:
                return  # someone copied something new meanwhile; leave it
            winutil.clipboard_restore(saved)

    def flush(self) -> None:
        """Restore the clipboard now (used on exit)."""
        timer = self._restore_timer
        if timer is not None:
            timer.cancel()
            self._restore()


def wait_keys_released(timeout: float = 0.5) -> None:
    """Wait until no modifier is physically held (best effort, used before paste)."""
    end = time.monotonic() + timeout
    while time.monotonic() < end and _held_modifiers():
        time.sleep(0.01)
