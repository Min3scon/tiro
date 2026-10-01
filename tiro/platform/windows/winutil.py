"""Small Win32 helpers: foreground window/process info, elevation, clipboard, window styles."""

from __future__ import annotations

import ctypes
import logging
import os
import sys
import threading
import time
from ctypes import wintypes

log = logging.getLogger(__name__)

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
shell32 = ctypes.WinDLL("shell32", use_last_error=True)

user32.GetForegroundWindow.restype = wintypes.HWND
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.GetWindowThreadProcessId.restype = wintypes.DWORD
user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.IsWindow.argtypes = [wintypes.HWND]
user32.SetForegroundWindow.argtypes = [wintypes.HWND]
user32.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
user32.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
user32.SetWindowLongPtrW.restype = ctypes.c_ssize_t
user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
kernel32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
                                                ctypes.POINTER(wintypes.DWORD)]
advapi32.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
advapi32.GetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
                                         ctypes.POINTER(wintypes.DWORD)]
kernel32.GetCurrentProcess.restype = wintypes.HANDLE
kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
kernel32.GlobalLock.restype = ctypes.c_void_p
kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
kernel32.GlobalSize.argtypes = [wintypes.HGLOBAL]
kernel32.GlobalSize.restype = ctypes.c_size_t
kernel32.GlobalFree.argtypes = [wintypes.HGLOBAL]
user32.OpenClipboard.argtypes = [wintypes.HWND]
user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
user32.SetClipboardData.restype = wintypes.HANDLE
user32.GetClipboardData.argtypes = [wintypes.UINT]
user32.GetClipboardData.restype = wintypes.HANDLE
user32.EnumClipboardFormats.argtypes = [wintypes.UINT]
user32.EnumClipboardFormats.restype = wintypes.UINT
user32.RegisterClipboardFormatW.argtypes = [wintypes.LPCWSTR]
user32.RegisterClipboardFormatW.restype = wintypes.UINT
user32.GetClipboardSequenceNumber.restype = wintypes.DWORD
shell32.ShellExecuteW.argtypes = [wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.LPCWSTR,
                                  wintypes.LPCWSTR, ctypes.c_int]
shell32.ShellExecuteW.restype = ctypes.c_void_p

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
TOKEN_QUERY = 0x0008
TokenElevation = 20
GWL_EXSTYLE = -20
WS_EX_TOPMOST, WS_EX_TRANSPARENT, WS_EX_TOOLWINDOW, WS_EX_LAYERED, WS_EX_NOACTIVATE = (
    0x8, 0x20, 0x80, 0x80000, 0x08000000,
)
CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002
# Clipboard formats backed by GDI handles rather than HGLOBAL memory; they can't be copied as bytes
# (Windows re-synthesises them from CF_DIB / CF_ENHMETAFILE equivalents anyway).
_GDI_FORMATS = {2, 3, 9, 14, 0x82, 0x83, 0x8E}

TERMINAL_CLASSES = {"ConsoleWindowClass", "CASCADIA_HOSTING_WINDOW_CLASS", "mintty", "PuTTY", "VirtualConsoleClass"}


def foreground_window() -> int:
    return user32.GetForegroundWindow() or 0


def window_pid(hwnd: int) -> int:
    pid = wintypes.DWORD(0)
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value


def window_class(hwnd: int) -> str:
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buf, 256)
    return buf.value


def is_window(hwnd: int) -> bool:
    return bool(hwnd) and bool(user32.IsWindow(hwnd))


def activate(hwnd: int) -> bool:
    """Bring a window to the front (works while one of our windows has focus). True if it worked."""
    if not is_window(hwnd):
        return False
    user32.SetForegroundWindow(hwnd)
    return foreground_window() == hwnd


def window_title(hwnd: int) -> str:
    buf = ctypes.create_unicode_buffer(512)
    user32.GetWindowTextW(hwnd, buf, 512)
    return buf.value


def window_app(hwnd: int) -> str:
    """Executable name of the window's process, lowercase without extension ("chrome", "winword")."""
    name = process_name(window_pid(hwnd)) if hwnd else ""
    return os.path.splitext(name)[0].lower()


def window_rect(hwnd: int) -> tuple[int, int, int, int] | None:
    rect = wintypes.RECT()
    if not hwnd or not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        return None
    return rect.left, rect.top, rect.right, rect.bottom


def process_name(pid: int) -> str:
    h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = wintypes.DWORD(1024)
        if kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            return os.path.basename(buf.value)
        return ""
    finally:
        kernel32.CloseHandle(h)


def _token_elevated(process_handle: int) -> bool | None:
    token = wintypes.HANDLE()
    if not advapi32.OpenProcessToken(process_handle, TOKEN_QUERY, ctypes.byref(token)):
        return None
    try:
        elevation = wintypes.DWORD(0)
        size = wintypes.DWORD(0)
        ok = advapi32.GetTokenInformation(token, TokenElevation, ctypes.byref(elevation), 4, ctypes.byref(size))
        return bool(elevation.value) if ok else None
    finally:
        kernel32.CloseHandle(token)


def is_elevated() -> bool:
    return bool(_token_elevated(kernel32.GetCurrentProcess()))


def process_elevated(pid: int) -> bool:
    h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return True  # can't even query it: almost certainly a higher-integrity process
    try:
        result = _token_elevated(h)
        return True if result is None else result
    finally:
        kernel32.CloseHandle(h)


def foreground_blocked_by_uipi() -> bool:
    """True if the focused window belongs to an elevated app while Tiro is not elevated."""
    hwnd = foreground_window()
    if not hwnd or is_elevated():
        return False
    pid = window_pid(hwnd)
    return pid != os.getpid() and process_elevated(pid)


def foreground_is_terminal() -> bool:
    return window_class(foreground_window()) in TERMINAL_CLASSES


def make_overlay_window(hwnd: int) -> None:
    """Never activate, never take focus, let clicks fall through, stay out of Alt+Tab."""
    ex = user32.GetWindowLongPtrW(hwnd, GWL_EXSTYLE)
    ex |= WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW | WS_EX_TOPMOST | WS_EX_TRANSPARENT | WS_EX_LAYERED
    user32.SetWindowLongPtrW(hwnd, GWL_EXSTYLE, ex)


def dark_title_bar(hwnd: int) -> None:
    """Ask DWM for a dark title bar (Windows 10 20H1+ / 11)."""
    try:
        dwm = ctypes.WinDLL("dwmapi")
        value = ctypes.c_int(1)
        for attr in (20, 19):  # DWMWA_USE_IMMERSIVE_DARK_MODE (and its pre-20H1 number)
            if dwm.DwmSetWindowAttribute(wintypes.HWND(hwnd), attr, ctypes.byref(value), 4) == 0:
                break
    except OSError:
        pass


class MONITORINFOEXW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("rcMonitor", wintypes.RECT),
        ("rcWork", wintypes.RECT),
        ("dwFlags", wintypes.DWORD),
        ("szDevice", wintypes.WCHAR * 32),
    ]


user32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
user32.MonitorFromWindow.restype = wintypes.HMONITOR
user32.GetMonitorInfoW.argtypes = [wintypes.HMONITOR, ctypes.c_void_p]


def monitor_device(hwnd: int) -> str:
    """GDI device name (e.g. \\\\.\\DISPLAY1) of the monitor showing hwnd; matches QScreen.name()."""
    mon = user32.MonitorFromWindow(hwnd, 2)  # MONITOR_DEFAULTTONEAREST
    info = MONITORINFOEXW()
    info.cbSize = ctypes.sizeof(MONITORINFOEXW)
    if mon and user32.GetMonitorInfoW(mon, ctypes.byref(info)):
        return info.szDevice
    return ""


def relaunch_as_admin(args: list[str]) -> bool:
    exe = sys.executable
    if not getattr(sys, "frozen", False):  # running from source: pythonw run_tiro.pyw ...
        from tiro.paths import app_root

        exe = str(os.path.join(os.path.dirname(sys.executable), "pythonw.exe"))
        args = [str(app_root() / "run_tiro.pyw"), *args]
    params = " ".join(f'"{a}"' for a in args)
    res = shell32.ShellExecuteW(None, "runas", exe, params, None, 1)
    return (res or 0) > 32


# ---------------------------------------------------------------------- clipboard

def _open_clipboard(retries: int = 20) -> bool:
    for _ in range(retries):
        if user32.OpenClipboard(None):
            return True
        time.sleep(0.015)
    return False


def _hglobal_from_bytes(data: bytes) -> int:
    h = kernel32.GlobalAlloc(GMEM_MOVEABLE, max(len(data), 1))
    if not h:
        return 0
    p = kernel32.GlobalLock(h)
    ctypes.memmove(p, data, len(data))
    kernel32.GlobalUnlock(h)
    return h


def _set_flags_no_history() -> None:
    zero = (0).to_bytes(4, "little")
    for name in ("ExcludeClipboardContentFromMonitorProcessing", "CanIncludeInClipboardHistory",
                 "CanUploadToCloudClipboard"):
        fmt = user32.RegisterClipboardFormatW(name)
        h = _hglobal_from_bytes(zero)
        if h and not user32.SetClipboardData(fmt, h):
            kernel32.GlobalFree(h)


def set_clipboard_text(text: str) -> bool:
    if not _open_clipboard():
        log.warning("clipboard busy")
        return False
    try:
        user32.EmptyClipboard()
        h = _hglobal_from_bytes((text + "\0").encode("utf-16-le"))
        if not h or not user32.SetClipboardData(CF_UNICODETEXT, h):
            if h:
                kernel32.GlobalFree(h)
            return False
        _set_flags_no_history()
        return True
    finally:
        user32.CloseClipboard()


def clipboard_snapshot() -> list[tuple[int, bytes]]:
    """Copy every memory-backed clipboard format so it can be put back later."""
    items: list[tuple[int, bytes]] = []
    if not _open_clipboard():
        return items
    try:
        fmt = 0
        while True:
            fmt = user32.EnumClipboardFormats(fmt)
            if not fmt:
                break
            if fmt in _GDI_FORMATS:
                continue
            h = user32.GetClipboardData(fmt)
            if not h:
                continue
            size = kernel32.GlobalSize(h)
            p = kernel32.GlobalLock(h)
            if not p:
                continue
            try:
                items.append((fmt, ctypes.string_at(p, size)))
            finally:
                kernel32.GlobalUnlock(h)
    finally:
        user32.CloseClipboard()
    return items


def clipboard_restore(items: list[tuple[int, bytes]]) -> None:
    if not _open_clipboard():
        return
    try:
        user32.EmptyClipboard()
        for fmt, data in items:
            h = _hglobal_from_bytes(data)
            if h and not user32.SetClipboardData(fmt, h):
                kernel32.GlobalFree(h)
        _set_flags_no_history()
    finally:
        user32.CloseClipboard()


# ---------------------------------------------------------------------- keyboard layout / mouse (for context)
user32.ToUnicodeEx.argtypes = [wintypes.UINT, wintypes.UINT, ctypes.c_char_p, wintypes.LPWSTR, ctypes.c_int,
                               wintypes.UINT, wintypes.HKL]
user32.GetKeyboardLayout.argtypes = [wintypes.DWORD]
user32.GetKeyboardLayout.restype = wintypes.HKL
user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
user32.GetAsyncKeyState.restype = ctypes.c_short
user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
user32.WindowFromPoint.argtypes = [wintypes.POINT]
user32.WindowFromPoint.restype = wintypes.HWND
user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
user32.GetAncestor.restype = wintypes.HWND
GA_ROOT = 2
SHELL_CLASSES = {"Shell_TrayWnd", "Shell_SecondaryTrayWnd", "NotifyIconOverflowWindow",
                 "TopLevelWindowForOverflowXamlIsland"}
MOUSE_BUTTONS = (0x01, 0x02, 0x04, 0x05, 0x06)


def key_to_char(vk: int, scan: int, shift: bool) -> str | None:
    """The character a key press produces in the foreground window's keyboard layout."""
    state = (ctypes.c_ubyte * 256)()
    if shift:
        state[0x10] = 0x80
    buf = ctypes.create_unicode_buffer(8)
    tid = user32.GetWindowThreadProcessId(foreground_window(), None)
    hkl = user32.GetKeyboardLayout(tid)
    n = user32.ToUnicodeEx(vk, scan, ctypes.cast(state, ctypes.c_char_p), buf, 8, 0x4, hkl)  # 0x4: keep dead keys
    if n == 1 and buf.value and buf.value.isprintable():
        return buf.value
    return None


def click_is_elsewhere() -> bool:
    """Was the click on the taskbar / tray or on one of Tiro's own windows (so no app's caret moved)?"""
    pt = wintypes.POINT()
    if not user32.GetCursorPos(ctypes.byref(pt)):
        return False
    hwnd = user32.WindowFromPoint(pt)
    root = user32.GetAncestor(hwnd, GA_ROOT) if hwnd else 0
    if not root:
        return False
    return window_class(root) in SHELL_CLASSES or window_pid(root) == os.getpid()


def watch_mouse_clicks(on_click, armed: threading.Event) -> None:
    """Call on_click(elsewhere) for every mouse button press, from a background thread. Polls only while
    `armed` is set (i.e. while Tiro cares about the caret position)."""

    def run():
        while True:
            armed.wait()
            if any(user32.GetAsyncKeyState(b) & 0x8000 for b in MOUSE_BUTTONS):
                on_click(click_is_elsewhere())
                while any(user32.GetAsyncKeyState(b) & 0x8000 for b in MOUSE_BUTTONS):
                    time.sleep(0.025)
            time.sleep(0.025)

    threading.Thread(target=run, name="tiro-mouse", daemon=True).start()
