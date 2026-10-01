"""Remembers what was typed last (by the user or by Tiro) so a new dictation can join it cleanly.

If the user just typed "I think" and dictates "We should go", Tiro types " we should go": a space
because the caret follows a word, lowercase because the sentence is already underway. Anything that may
have moved the caret (mouse clicks, arrow keys, switching windows) makes the context unknown, and then
Tiro inserts the text exactly as recognised.
"""

from __future__ import annotations

import threading
import time

from tiro import winutil

WORD, MID, END, SPACE, NEWLINE, UNKNOWN = "word", "mid", "end", "space", "newline", "unknown"
_STALE_SEC = 15 * 60


def classify_key(vk: int, shift: bool) -> str | None:
    """Class of the character a key press produced (US-style layout). None = no character (ignore)."""
    if vk in (0x10, 0x11, 0x12, 0xA0, 0xA1, 0xA2, 0xA3, 0xA4, 0xA5, 0x14, 0x90, 0x91):
        return None  # modifiers / lock keys do not move the caret
    if 0x41 <= vk <= 0x5A or 0x60 <= vk <= 0x69:
        return WORD
    if 0x30 <= vk <= 0x39:
        if shift:
            return END if vk == 0x31 else UNKNOWN  # "!" ends a sentence; other symbols are ambiguous
        return WORD
    if vk == 0x20:
        return SPACE
    if vk in (0x0D, 0x09):
        return NEWLINE
    if vk == 0xBE:  # . >
        return UNKNOWN if shift else END
    if vk == 0xBF:  # / ?
        return END if shift else UNKNOWN
    if vk in (0xBC, 0xBA):  # , ;  (and < :)
        return MID if (vk == 0xBA or not shift) else UNKNOWN
    return UNKNOWN


def classify_char(ch: str) -> str:
    if ch in "\n\r":
        return NEWLINE
    if ch.isspace():
        return SPACE
    if ch in ".?!":
        return END
    if ch in ",;:":
        return MID
    if ch.isalnum() or ch in "'\")":
        return WORD
    return UNKNOWN


class InputContext:
    def __init__(self, on_click=None):
        self.on_click = on_click  # also told about mouse clicks (the caret may have moved)
        self._lock = threading.Lock()
        self._last: str | None = None  # class of the last character
        self._last_nonspace: str | None = None
        self._hwnd = 0
        self._time = 0.0
        self._armed = threading.Event()
        self.events = 0  # bumps on every key press or click that may have changed text or moved a caret
        winutil.watch_mouse_clicks(self._clicked, self._armed)

    def _set(self, cls: str, hwnd: int) -> None:
        self._last = cls
        if cls != SPACE:
            self._last_nonspace = cls
        self._hwnd = hwnd
        self._time = time.monotonic()
        self._armed.set()

    def invalidate(self) -> None:
        with self._lock:
            self._last = self._last_nonspace = None
            self._armed.clear()

    def note_key(self, vk: int, shift: bool, shortcut: bool) -> None:
        """Called from the keyboard hook for physical key presses that reached the app."""
        if vk not in (0x10, 0x11, 0x12, 0xA0, 0xA1, 0xA2, 0xA3, 0xA4, 0xA5, 0x5B, 0x5C, 0x14, 0x90, 0x91):
            self.events += 1
        if shortcut:
            if vk not in (0x10, 0x11, 0x12, 0xA0, 0xA1, 0xA2, 0xA3, 0xA4, 0xA5, 0x5B, 0x5C):
                self.invalidate()
            return
        cls = classify_key(vk, shift)
        if cls is None:
            return
        with self._lock:
            if cls == UNKNOWN:
                self._last = self._last_nonspace = None
                return
            self._set(cls, winutil.foreground_window())

    def note_inject(self, text: str, hwnd: int) -> None:
        if not text:
            return
        with self._lock:
            for ch in text[-2:]:
                self._set(classify_char(ch), hwnd)
            if not text[-1].isspace():
                self._last_nonspace = classify_char(text[-1])

    def snapshot(self, hwnd: int) -> tuple[bool, bool]:
        """(needs_leading_space, continues_sentence) for text about to be inserted into hwnd."""
        with self._lock:
            if (
                self._last is None
                or hwnd != self._hwnd
                or time.monotonic() - self._time > _STALE_SEC
                or self._last == UNKNOWN
            ):
                return False, False
            last, prev = self._last, self._last_nonspace
        if last == NEWLINE:
            return False, False
        if last == SPACE:
            return False, prev in (WORD, MID)
        return True, last in (WORD, MID)

    def _clicked(self, elsewhere: bool) -> None:
        """A mouse button went down. Unless it was on the taskbar/menu bar or one of Tiro's windows, the
        caret may have moved."""
        if not elsewhere:
            self.events += 1
        self.invalidate()
        if self.on_click is not None:
            self.on_click()
