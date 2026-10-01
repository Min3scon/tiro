"""Learn words the user fixes right after Tiro typed them.

Tiro knows what it just typed. If the next thing the user does is backspace over a word and type a
similar-sounding replacement ("geogasser" -> "GeoGuessr"), that replacement is almost certainly a word the
recogniser doesn't know, so it is added to the personal dictionary. Rewording ("big" -> "large"), typo
fixes of ordinary words, and anything after the caret moves (arrows, clicks, other windows) are ignored.
"""

from __future__ import annotations

import logging
import re
import threading
from difflib import SequenceMatcher
from collections.abc import Callable

from tiro import winutil
from tiro.correct.text import common_words, letters, ratio, sound_key, split_punct

log = logging.getLogger(__name__)

VK_BACK, VK_TAB, VK_RETURN = 0x08, 0x09, 0x0D
_NAV = {0x1B, 0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27, 0x28, 0x2D, 0x2E}  # esc, pgup/dn, end, home, arrows, ins, del
_MODIFIERS = {0x10, 0x11, 0x12, 0x14, 0x5B, 0x5C, 0xA0, 0xA1, 0xA2, 0xA3, 0xA4, 0xA5, 0x90, 0x91}
_WORD = re.compile(r"[\w'’-]+")
MAX_TAIL = 400


class CorrectionLearner:
    def __init__(self, on_learn: Callable[[str, str, str], None]):
        self.on_learn = on_learn
        self.enabled = True
        self._lock = threading.Lock()
        self._active = False
        self._hwnd = 0
        self._original = ""  # text Tiro typed
        self._current: list[str] = []  # what is now left of the caret, as far as we can tell
        self._edited = False
        self._timer: threading.Timer | None = None

    # ------------------------------------------------------------------ events
    def note_injection(self, text: str, hwnd: int) -> None:
        if not self.enabled or not text:
            return
        learned = None
        with self._lock:
            if self._active and hwnd == self._hwnd and not self._edited:
                self._original += text
                self._current.extend(text)
            else:
                learned = self._check()
                self._original, self._current = text, list(text)
                self._hwnd, self._active, self._edited = hwnd, True, False
            self._trim()
        self._emit(learned)

    def note_key(self, vk: int, scan: int, shift: bool, shortcut: bool, hwnd: int) -> None:
        """A physical key press that reached the focused app (called from the keyboard hook)."""
        if not self._active or vk in _MODIFIERS:
            return
        learned = None
        with self._lock:
            if not self._active:
                return
            if hwnd != self._hwnd:
                self._active = False
                return
            if vk == VK_BACK:
                if shortcut:  # Ctrl+Backspace deletes a word
                    while self._current and self._current[-1].isspace():
                        self._current.pop()
                    while self._current and not self._current[-1].isspace():
                        self._current.pop()
                elif self._current:
                    self._current.pop()
                self._edited = True
                return
            if vk in _NAV or shortcut:
                self._active = False  # the caret moved or a shortcut ran: we no longer know the text
                return
            if vk in (VK_RETURN, VK_TAB):
                learned = self._check()
                self._active = False
            else:
                ch = winutil.key_to_char(vk, scan, shift)
                if ch is None:
                    return
                self._current.append(ch)
                self._edited = True
                if not ch.isalnum() and ch not in "'’-":
                    learned = self._check()
                self._schedule()
        self._emit(learned)

    def note_click(self) -> None:
        learned = None
        with self._lock:
            if self._active:
                learned = self._check()
                self._active = False
        self._emit(learned)

    # ------------------------------------------------------------------ logic
    def _schedule(self) -> None:
        if self._timer is not None:
            self._timer.cancel()
        self._timer = threading.Timer(2.0, self._idle_check)
        self._timer.daemon = True
        self._timer.start()

    def _idle_check(self) -> None:
        with self._lock:
            learned = self._check() if self._active else None
        self._emit(learned)

    def _emit(self, fix: tuple[str, str, str] | None) -> None:
        if fix:
            log.info("learned a corrected word")
            try:
                self.on_learn(*fix)
            except Exception:  # pragma: no cover
                log.exception("on_learn failed")

    def _trim(self) -> None:
        if len(self._original) > MAX_TAIL:
            cut = len(self._original) - MAX_TAIL
            self._original = self._original[cut:]
            self._current = self._current[cut:] if len(self._current) > cut else []

    def _check(self) -> tuple[str, str, str] | None:
        """If the user replaced dictated words with a similar-sounding one: (heard, written, context)."""
        if not self._edited:
            return None
        cur = "".join(self._current)
        fix = learnable_fix(self._original, cur)
        if fix:
            self._original, self._edited = cur, False  # don't learn the same fix twice
            return fix[0], fix[1], self._original[-120:]
        return None


def learnable_correction(original: str, current: str) -> str | None:
    """The new spelling the user taught by retyping, if any (see learnable_fix)."""
    fix = learnable_fix(original, current)
    return fix[1] if fix else None


def learnable_fix(original: str, current: str) -> tuple[str, str] | None:
    """(misheard words, the word typed instead) if the edit looks like fixing a mishearing."""
    p = 0
    while p < min(len(original), len(current)) and original[p] == current[p]:
        p += 1
    while p > 0 and not original[p - 1].isspace():
        p -= 1
    removed = _WORD.findall(original[p:])
    added = _WORD.findall(current[p:])
    # the user may have retyped words that followed the misheard one; drop the identical tail
    while removed and added and removed[-1] == added[-1]:
        removed.pop()
        added.pop()
    if len(added) != 1 or not removed or len(removed) > 3:
        return None
    new, old = added[0], " ".join(removed)
    key_new, key_old = letters(new), letters(old)
    if len(key_new) < 3 or key_new == key_old and new.lower() == new:
        return None
    common = common_words()
    if key_new in common and new == new.lower():
        return None  # fixing an ordinary word (typo or rewording), not teaching a name
    if key_new == key_old:
        return (old, new) if key_new not in common else None  # capitalisation only: "jira" -> "Jira"
    similarity = max(ratio(key_old, key_new), ratio(sound_key(key_old), sound_key(key_new)))
    if key_old[:3] == key_new[:3]:
        similarity = max(similarity, 0.6)  # same first syllable ("George" for "GeoGuessr"): a mishearing
    return (old, new) if similarity >= 0.5 else None


def fixes_from_edit(old: str, new: str) -> list[tuple[str, str]]:
    """Word swaps between a transcription and the user's corrected version: [(heard, written), ...].

    Only replacements of up to three words by up to three words count (a misheard name, a split or
    run-together word, a casing fix); inserted or deleted words are edits, not mishearings.
    """
    a, b = old.split(), new.split()
    sm = SequenceMatcher(a=[letters(w) for w in a], b=[letters(w) for w in b], autojunk=False)
    out: list[tuple[str, str]] = []
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op == "equal":
            for x, y in zip(a[i1:i2], b[j1:j2], strict=True):
                cx, cy = split_punct(x)[1], split_punct(y)[1]
                if cx != cy:
                    out.append((cx, cy))  # same letters, new spelling: "github" -> "GitHub"
        elif op == "replace" and i2 - i1 <= 3 and j2 - j1 <= 3:
            heard = " ".join(split_punct(w)[1] for w in a[i1:i2])
            written = " ".join(split_punct(w)[1] for w in b[j1:j2])
            if letters(heard) and letters(written):
                out.append((heard, written))
    return out
