"""macOS text insertion: synthetic Unicode key events (Quartz), or paste via the clipboard.

Every event Tiro posts carries TIRO_INPUT_MARK so its own event tap ignores it, and has its modifier flags
cleared, so a hotkey still held down (e.g. Right Option) can't turn letters into symbols. Posting events
needs the Accessibility permission.
"""

from __future__ import annotations

import logging
import os
import threading
import time

from tiro.hotkey_marks import TIRO_INPUT_MARK

log = logging.getLogger(__name__)

KEY_RETURN, KEY_TAB, KEY_DELETE, KEY_V = 0x24, 0x30, 0x33, 0x09
FLAG_SHIFT, FLAG_COMMAND = 0x00020000, 0x00100000
CHUNK = 16  # UTF-16 units per synthetic event (the API takes up to 20)
CONCEALED_TYPES = ("org.nspasteboard.TransientType", "org.nspasteboard.ConcealedType")


def _source():
    import Quartz

    return Quartz.CGEventSourceCreate(Quartz.kCGEventSourceStatePrivate)


def _post(event) -> None:
    import Quartz

    Quartz.CGEventSetIntegerValueField(event, 42, TIRO_INPUT_MARK)  # kCGEventSourceUserData
    Quartz.CGEventPost(Quartz.kCGHIDEventTap, event)


def _key(src, code: int, flags: int = 0) -> None:
    import Quartz

    for down in (True, False):
        e = Quartz.CGEventCreateKeyboardEvent(src, code, down)
        Quartz.CGEventSetFlags(e, flags)
        _post(e)


def replay_keys(keys: list[tuple[int, int, int, bool]]) -> None:
    """Re-post swallowed physical key events (vk, mac keycode, flags, down) to the focused app."""
    import Quartz

    src = _source()
    for _vk, code, flags, down in keys:
        e = Quartz.CGEventCreateKeyboardEvent(src, code, down)
        Quartz.CGEventSetFlags(e, flags)
        _post(e)


def wait_keys_released(timeout: float = 0.5) -> None:  # parity with Windows; flags are cleared per event
    return


class Injector:
    def __init__(self, method: str = "type"):
        self.method = method
        self._clip_lock = threading.Lock()
        self._restore_timer: threading.Timer | None = None
        self._saved_clip = None
        self._our_change = -1

    def _guarded(self) -> bool:
        guard = os.environ.get("TIRO_TEST_TARGET_CLASS")  # automated tests only
        if not guard:
            return True
        from tiro import winutil

        return winutil.window_class(winutil.foreground_window()) == guard

    def insert(self, text: str) -> bool:
        if not text:
            return True
        if not self._guarded():
            log.warning("test guard: focus is not on the test window; dropped %d chars", len(text))
            return False
        try:
            return self._paste(text) if self.method == "paste" else self._type(text)
        except Exception:
            log.exception("text insertion failed")
            return False

    def replace_tail(self, delete: int, text: str) -> bool:
        """Delete the last `delete` characters (text Tiro typed), then insert `text` in their place."""
        if not self._guarded():
            return False
        try:
            src = _source()
            for _ in range(max(0, delete)):
                _key(src, KEY_DELETE)
            return self.insert(text) if text else True
        except Exception:
            log.exception("replacing text failed")
            return False

    def _type(self, text: str) -> bool:
        import Quartz

        src = _source()
        buf = ""
        for ch in text.replace("\r\n", "\n"):
            if ch in "\n\t":
                if buf:
                    self._type_chunk(src, buf)
                    buf = ""
                if ch == "\n":
                    _key(src, KEY_RETURN, FLAG_SHIFT)  # Shift+Return: a line break, not "send", in chat apps
                else:
                    _key(src, KEY_TAB)
                continue
            buf += ch
            if len(buf.encode("utf-16-le")) // 2 >= CHUNK:
                self._type_chunk(src, buf)
                buf = ""
        if buf:
            self._type_chunk(src, buf)
        del Quartz
        return True

    @staticmethod
    def _type_chunk(src, chunk: str) -> None:
        import Quartz

        units = len(chunk.encode("utf-16-le")) // 2
        for down in (True, False):
            e = Quartz.CGEventCreateKeyboardEvent(src, 0, down)
            Quartz.CGEventSetFlags(e, 0)
            Quartz.CGEventKeyboardSetUnicodeString(e, units, chunk)
            _post(e)
        time.sleep(0.002)  # let the target app keep up

    # ------------------------------------------------------------------ clipboard
    def _paste(self, text: str) -> bool:
        from AppKit import NSPasteboard, NSPasteboardTypeString

        pb = NSPasteboard.generalPasteboard()
        with self._clip_lock:
            if self._restore_timer is not None:
                self._restore_timer.cancel()
            elif self._saved_clip is None:
                self._saved_clip = _snapshot(pb)
            pb.clearContents()
            pb.declareTypes_owner_([NSPasteboardTypeString, *CONCEALED_TYPES], None)
            pb.setString_forType_(text, NSPasteboardTypeString)
            for t in CONCEALED_TYPES:  # clipboard managers skip transient/concealed content
                pb.setString_forType_("", t)
            self._our_change = pb.changeCount()
            _key(_source(), KEY_V, FLAG_COMMAND)
            self._restore_timer = threading.Timer(0.6, self._restore)
            self._restore_timer.daemon = True
            self._restore_timer.start()
        return True

    def _restore(self) -> None:
        from AppKit import NSPasteboard

        pb = NSPasteboard.generalPasteboard()
        with self._clip_lock:
            self._restore_timer = None
            saved, self._saved_clip = self._saved_clip, None
            if saved is None or pb.changeCount() != self._our_change:
                return  # nothing saved, or someone copied something new meanwhile
            _restore(pb, saved)

    def flush(self) -> None:
        timer = self._restore_timer
        if timer is not None:
            timer.cancel()
            self._restore()


def _snapshot(pb) -> list[list[tuple[str, bytes]]]:
    items = []
    for item in pb.pasteboardItems() or []:
        entry = []
        for t in item.types():
            data = item.dataForType_(t)
            if data is not None:
                entry.append((str(t), bytes(data)))
        items.append(entry)
    return items


def _restore(pb, items) -> None:
    from AppKit import NSPasteboardItem
    from Foundation import NSData

    pb.clearContents()
    objs = []
    for entry in items:
        item = NSPasteboardItem.alloc().init()
        for t, data in entry:
            item.setData_forType_(NSData.dataWithBytes_length_(data, len(data)), t)
        objs.append(item)
    if objs:
        pb.writeObjects_(objs)
