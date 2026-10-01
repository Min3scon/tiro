"""Windows keyboard hook (WH_KEYBOARD_LL) on its own thread with a message loop."""

from __future__ import annotations

import ctypes
import logging
import threading
from collections.abc import Callable
from ctypes import wintypes

from tiro.hotkey_marks import TIRO_INPUT_MARK

log = logging.getLogger(__name__)

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

LRESULT = ctypes.c_ssize_t
WH_KEYBOARD_LL = 13
WM_KEYDOWN, WM_KEYUP, WM_SYSKEYDOWN, WM_SYSKEYUP = 0x0100, 0x0101, 0x0104, 0x0105
WM_QUIT, WM_APP = 0x0012, 0x8000
LLKHF_EXTENDED, LLKHF_INJECTED, LLKHF_UP = 0x01, 0x10, 0x80


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("vkCode", wintypes.DWORD),
        ("scanCode", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD]
user32.SetWindowsHookExW.restype = wintypes.HHOOK
user32.CallNextHookEx.argtypes = [wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
user32.CallNextHookEx.restype = LRESULT
user32.UnhookWindowsHookEx.argtypes = [wintypes.HHOOK]
user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
user32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
kernel32.GetModuleHandleW.restype = wintypes.HMODULE

KeyHandler = Callable[[int, bool, int, int, bool], bool]  # (vk, down, scan, flags, injected_by_other) -> swallow


class KeyboardHook:
    """Runs WH_KEYBOARD_LL on its own thread with a message loop. Re-installs itself periodically,
    because Windows silently drops low-level hooks that ever take too long to respond."""

    REINSTALL_SEC = 60.0

    def __init__(self, handler: KeyHandler, can_reinstall: Callable[[], bool] = lambda: True):
        self.handler = handler
        self.can_reinstall = can_reinstall
        self._proc = HOOKPROC(self._callback)  # keep a reference: it must outlive the hook
        self._hook = None
        self._thread: threading.Thread | None = None
        self._tid = 0
        self._ready = threading.Event()
        self.capture: Callable[[int, bool], None] | None = None  # set while recording a new hotkey

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="tiro-keyboard-hook", daemon=True)
        self._thread.start()
        self._ready.wait(5)

    def reinstall(self) -> None:
        """Re-register the hook (from any thread). Windows silently drops a low-level hook whose callback
        was ever too slow, e.g. while a large model was loading and holding Python's lock."""
        if self._tid:
            user32.PostThreadMessageW(self._tid, WM_APP + 1, 0, 0)

    def stop(self) -> None:
        if self._tid:
            user32.PostThreadMessageW(self._tid, WM_QUIT, 0, 0)
        if self._thread:
            self._thread.join(2)

    def _install(self) -> None:
        hmod = kernel32.GetModuleHandleW(None)
        self._hook = user32.SetWindowsHookExW(WH_KEYBOARD_LL, self._proc, hmod, 0)
        if not self._hook:
            raise ctypes.WinError(ctypes.get_last_error())

    def _run(self) -> None:
        self._tid = kernel32.GetCurrentThreadId()
        try:
            self._install()
        finally:
            self._ready.set()
        timer_id = user32.SetTimer(None, 0, int(self.REINSTALL_SEC * 1000), None)
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == WM_APP + 1:  # reinstall request
                if self._hook:
                    user32.UnhookWindowsHookEx(self._hook)
                self._install()
                continue
            if msg.message == 0x0113 and msg.wParam == timer_id:  # WM_TIMER
                if self.can_reinstall():
                    user32.UnhookWindowsHookEx(self._hook)
                    self._install()
                continue
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
        user32.KillTimer(None, timer_id)
        if self._hook:
            user32.UnhookWindowsHookEx(self._hook)
            self._hook = None

    def _callback(self, n_code: int, w_param: int, l_param: int) -> int:
        if n_code == 0:
            kb = ctypes.cast(l_param, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
            ours = kb.dwExtraInfo == TIRO_INPUT_MARK
            if not ours:
                down = w_param in (WM_KEYDOWN, WM_SYSKEYDOWN)
                try:
                    if self.capture is not None:
                        self.capture(kb.vkCode, down)
                        return 1
                    if self.handler(kb.vkCode, down, kb.scanCode, kb.flags, bool(kb.flags & LLKHF_INJECTED)):
                        return 1
                except Exception:  # never let an exception escape into the hook chain
                    log.exception("keyboard hook handler failed")
        return user32.CallNextHookEx(None, n_code, w_param, l_param)


