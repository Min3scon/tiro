"""Helper process for dev/hook_gil_probe.py: sends F23 taps every 100 ms and watches for presses the hook under
test failed to swallow in time.

Its own hook is installed BEFORE the hook under test, so Windows calls it AFTER that hook: it only ever sees a
press the hook under test let through. Being a separate, idle process, its sending never stalls when the process
under test is busy.

    python dev/latency/hook_observer.py OUT.json GO_FILE STOP_FILE
"""
from __future__ import annotations

import ctypes
import json
import sys
import threading
import time
from ctypes import wintypes
from pathlib import Path

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
LRESULT = ctypes.c_ssize_t
HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD]
user32.SetWindowsHookExW.restype = wintypes.HHOOK
user32.CallNextHookEx.argtypes = [wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
user32.CallNextHookEx.restype = LRESULT
user32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
F23 = 0x86


class KB(ctypes.Structure):
    _fields_ = [("vk", wintypes.DWORD), ("scan", wintypes.DWORD), ("flags", wintypes.DWORD), ("time", wintypes.DWORD),
                ("extra", ctypes.c_size_t)]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class _U(ctypes.Union):
    _fields_ = [("ki", KEYBDINPUT), ("pad", ctypes.c_byte * 32)]


class INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("type", wintypes.DWORD), ("u", _U)]


def tap(down: bool) -> None:
    i = INPUT(type=1, ki=KEYBDINPUT(F23, 0, 0 if down else 2, 0, 0))
    user32.SendInput(1, ctypes.byref(i), ctypes.sizeof(INPUT))


def main() -> None:
    out, go, stop = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
    leaked: list[float] = []
    sent: list[float] = []

    def proc(code, wp, lp):
        if code == 0 and wp in (0x100, 0x104):
            kb = ctypes.cast(lp, ctypes.POINTER(KB)).contents
            if kb.vk == F23:
                leaked.append(time.time())
                return 1  # swallow it here so no app ever gets F23
        return user32.CallNextHookEx(None, code, wp, lp)

    cb = HOOKPROC(proc)
    tid = kernel32.GetCurrentThreadId()

    def sender():
        while not go.exists():
            time.sleep(0.02)
        while not stop.exists():
            sent.append(time.time())
            tap(True)
            tap(False)
            time.sleep(0.1)
        user32.PostThreadMessageW(tid, 0x0012, 0, 0)  # WM_QUIT

    hook = user32.SetWindowsHookExW(13, cb, kernel32.GetModuleHandleW(None), 0)
    threading.Thread(target=sender, daemon=True).start()
    print("ready", flush=True)
    msg = wintypes.MSG()
    while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
        user32.TranslateMessage(ctypes.byref(msg))
        user32.DispatchMessageW(ctypes.byref(msg))
    user32.UnhookWindowsHookEx(hook)
    out.write_text(json.dumps({"sent": sent, "leaked": leaked}), encoding="utf-8")


if __name__ == "__main__":
    main()
