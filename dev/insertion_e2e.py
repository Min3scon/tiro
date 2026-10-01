"""Text insertion correctness: every character arrives, in order, by typing and by pasting, and the clipboard comes
back. Uses Tiro's own Injector against a test Notepad window (and a console program for typing).

    python dev/insertion_e2e.py
"""
from __future__ import annotations

import ctypes
import json
import subprocess
import sys
import time
from ctypes import wintypes
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tiro import winutil  # noqa: E402
from tiro.injector import Injector  # noqa: E402

user32 = ctypes.WinDLL("user32", use_last_error=True)
user32.FindWindowExW.restype = wintypes.HWND
user32.FindWindowExW.argtypes = [wintypes.HWND, wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR]
user32.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.SendMessageW.restype = ctypes.c_ssize_t
WM_GETTEXT, WM_GETTEXTLENGTH, WM_SETTEXT, WM_CLOSE, EM_SETMODIFY = 0x000D, 0x000E, 0x000C, 0x0010, 0x00B9
WORK = ROOT / "work" / "results" / "insertion"

CASES = {
    "plain": "Can you pick up some milk and bread on your way home?",
    "punctuation": "Wait—really?! It's 50% off (today only): \"yes\", 'no' & <maybe> {braces} [brackets] #tag @you ~$5.",
    "accents": "Café naïve façade jalapeño Zoë Ångström Łódź São Paulo Straße",
    "non_english": "Привет, как дела? 你好，世界。 こんにちは。 مرحبا بالعالم। नमस्ते दुनिया",
    "emoji": "Great job 👍🏽 see you at 3pm 🎉🎉 — 👩‍💻 rocks",
    "newlines": "First line.\nSecond line.\n\nNew paragraph after a blank line.",
    "long": " ".join(f"word{i}" for i in range(400)),
}


def find_title(prefix: str, timeout: float = 15) -> int:
    end = time.time() + timeout
    proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    while time.time() < end:
        found = []

        def cb(hwnd, _):
            if user32.IsWindowVisible(hwnd) and winutil.window_title(hwnd).startswith(prefix):
                found.append(hwnd)
            return True

        user32.EnumWindows(proc(cb), 0)
        if found:
            return found[0]
        time.sleep(0.1)
    return 0


def read(edit: int) -> str:
    n = user32.SendMessageW(edit, WM_GETTEXTLENGTH, 0, 0)
    buf = ctypes.create_unicode_buffer(n + 1)
    user32.SendMessageW(edit, WM_GETTEXT, n + 1, ctypes.addressof(buf))
    return buf.value


def main() -> int:
    WORK.mkdir(parents=True, exist_ok=True)
    path = WORK / "TIRO-INSERT-TEST.txt"
    path.write_text("", encoding="utf-8")
    proc = subprocess.Popen(["notepad.exe", str(path)])
    hwnd = find_title("TIRO-INSERT-TEST.txt")
    edit = user32.FindWindowExW(hwnd, None, "Edit", None)
    if not hwnd or not edit:
        print("FAIL: no Notepad window")
        return 1
    saved = winutil.clipboard_snapshot()  # the user's clipboard, to check it comes back
    seq_before = user32.GetClipboardSequenceNumber()
    results = []
    try:
        for method in ("type", "paste"):
            inj = Injector(method)
            for name, text in CASES.items():
                user32.SendMessageW(edit, WM_SETTEXT, 0, ctypes.addressof(ctypes.create_unicode_buffer("")))
                user32.keybd_event(0xE8, 0, 0, 0)
                user32.keybd_event(0xE8, 0, 2, 0)
                user32.SetForegroundWindow(hwnd)
                time.sleep(0.3)
                if winutil.foreground_window() != hwnd:
                    results.append({"method": method, "case": name, "ok": False, "why": "focus"})
                    continue
                inj.insert(text)
                time.sleep(1.2 if method == "paste" else 0.6 + len(text) / 500)
                got = read(edit).replace("\r\n", "\n")
                ok = got == text
                results.append({"method": method, "case": name, "ok": ok,
                                "why": "" if ok else f"got {got[:80]!r}"})
                print(("PASS " if ok else "FAIL ") + f"{method:5s} {name}" + ("" if ok else f": {got[:80]!r}"),
                      flush=True)
            inj.flush()
        time.sleep(1.0)
        restored = winutil.clipboard_snapshot() == saved
        results.append({"method": "paste", "case": "clipboard restored", "ok": restored, "why": ""})
        print(("PASS " if restored else "FAIL ") + "clipboard restored after pasting", flush=True)
    finally:
        user32.SendMessageW(edit, EM_SETMODIFY, 0, 0)
        user32.SendMessageW(hwnd, WM_CLOSE, 0, 0)
        try:
            proc.wait(5)
        except subprocess.TimeoutExpired:
            proc.kill()
    del seq_before
    ok = all(r["ok"] for r in results)
    (WORK / "report.json").write_text(json.dumps({"ok": ok, "results": results}, indent=1, ensure_ascii=False),
                                      encoding="utf-8")
    print("ALL PASSED" if ok else "SOME FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
