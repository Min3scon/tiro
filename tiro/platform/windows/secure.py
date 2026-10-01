"""Is the focused field a password (or otherwise secure) field?

Tiro still types into such fields, but it never learns from them, never saves them to history and never
"corrects" them. Two checks, cheapest first:

* classic Win32 edit controls with the ES_PASSWORD style;
* UI Automation's IsPassword property of the focused element (browsers, WPF, UWP, Qt, Electron...).

If the answer can't be determined in time, the dictation is treated as secure (nothing is learned).
"""

from __future__ import annotations

import ctypes
import logging
import queue
import sys
import threading
from ctypes import wintypes

log = logging.getLogger(__name__)

ES_PASSWORD = 0x0020
GWL_STYLE = -16
UIA_TIMEOUT = 0.4  # seconds
_TIMED_OUT = object()

if sys.platform == "win32":
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    ole32 = ctypes.OleDLL("ole32")

    class GUITHREADINFO(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD),
            ("flags", wintypes.DWORD),
            ("hwndActive", wintypes.HWND),
            ("hwndFocus", wintypes.HWND),
            ("hwndCapture", wintypes.HWND),
            ("hwndMenuOwner", wintypes.HWND),
            ("hwndMoveSize", wintypes.HWND),
            ("hwndCaret", wintypes.HWND),
            ("rcCaret", wintypes.RECT),
        ]

    class GUID(ctypes.Structure):
        _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD), ("Data3", wintypes.WORD),
                    ("Data4", ctypes.c_ubyte * 8)]

        def __init__(self, text: str):
            super().__init__()
            ole32.CLSIDFromString(ctypes.c_wchar_p(text), ctypes.byref(self))

    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    user32.GetGUIThreadInfo.argtypes = [wintypes.DWORD, ctypes.POINTER(GUITHREADINFO)]
    user32.GetGUIThreadInfo.restype = wintypes.BOOL
    user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.GetWindowLongW.restype = ctypes.c_long
    user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]

    CLSID_CUIAutomation = "{ff48dba4-60ef-4201-aa87-54103eef594e}"
    IID_IUIAutomation = "{30cbe57d-d9d0-452a-ab13-7ac5ac4825ee}"
    _HRESULT_PTR = ctypes.WINFUNCTYPE(ctypes.HRESULT, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p))
    _HRESULT_BOOL = ctypes.WINFUNCTYPE(ctypes.HRESULT, ctypes.c_void_p, ctypes.POINTER(wintypes.BOOL))
    _ULONG = ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)


def _method(obj: ctypes.c_void_p, index: int, proto):
    vtable = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)))[0]
    return proto(vtable[index])


def focused_hwnd(foreground: int) -> int:
    tid = user32.GetWindowThreadProcessId(foreground, None)
    info = GUITHREADINFO(cbSize=ctypes.sizeof(GUITHREADINFO))
    if tid and user32.GetGUIThreadInfo(tid, ctypes.byref(info)) and info.hwndFocus:
        return int(info.hwndFocus)
    return foreground


def win32_password_edit(hwnd: int) -> bool:
    buf = ctypes.create_unicode_buffer(64)
    user32.GetClassNameW(hwnd, buf, 64)
    if "edit" not in buf.value.lower():
        return False
    return bool(user32.GetWindowLongW(hwnd, GWL_STYLE) & ES_PASSWORD)


class _UiaWorker:
    """One background thread that owns a UI Automation client (COM objects are thread-bound)."""

    def __init__(self):
        self._requests: queue.Queue = queue.Queue()
        self._thread = threading.Thread(target=self._run, name="tiro-uia", daemon=True)
        self._thread.start()

    def ask(self, timeout: float):
        reply: queue.Queue = queue.Queue(maxsize=1)
        self._requests.put(reply)
        try:
            return reply.get(timeout=timeout)
        except queue.Empty:
            return _TIMED_OUT

    def _run(self) -> None:
        uia = None
        try:
            ole32.CoInitializeEx(None, 0x0)  # multithreaded apartment
            ptr = ctypes.c_void_p()
            ole32.CoCreateInstance(ctypes.byref(GUID(CLSID_CUIAutomation)), None, 1,
                                   ctypes.byref(GUID(IID_IUIAutomation)), ctypes.byref(ptr))
            uia = ptr
        except OSError as exc:
            log.warning("UI Automation unavailable: %s", exc)
        while True:
            reply = self._requests.get()
            result: bool | None = None
            if uia is not None:
                try:
                    result = self._is_password(uia)
                except OSError as exc:
                    log.debug("UI Automation query failed: %s", exc)
            try:
                reply.put_nowait(result)
            except queue.Full:
                pass

    @staticmethod
    def _is_password(uia: ctypes.c_void_p) -> bool | None:
        element = ctypes.c_void_p()
        _method(uia, 8, _HRESULT_PTR)(uia, ctypes.byref(element))  # IUIAutomation::GetFocusedElement
        if not element:
            return None
        try:
            flag = wintypes.BOOL()
            _method(element, 35, _HRESULT_BOOL)(element, ctypes.byref(flag))  # get_CurrentIsPassword
            return bool(flag.value)
        finally:
            _method(element, 2, _ULONG)(element)  # Release


_uia: _UiaWorker | None = None
_uia_lock = threading.Lock()


def is_secure_field(foreground: int) -> bool | None:
    """True for password fields, False for ordinary ones, None if it couldn't be determined."""
    if sys.platform != "win32" or not foreground:
        return None
    global _uia
    try:
        if win32_password_edit(focused_hwnd(foreground)):
            return True
    except OSError:
        pass
    with _uia_lock:
        if _uia is None:
            _uia = _UiaWorker()
        worker = _uia
    result = worker.ask(UIA_TIMEOUT)
    if result is _TIMED_OUT:
        with _uia_lock:
            if _uia is worker:
                _uia = None  # it may be stuck on a hung app; use a fresh one next time
        return None
    return result
