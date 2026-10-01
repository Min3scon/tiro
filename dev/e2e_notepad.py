"""End-to-end test: run Tiro with a WAV file as its microphone, hold Right Ctrl over Notepad, read what got typed.

Usage: python dev/e2e_notepad.py [path\\to\\Tiro.exe] [--wav file] [--mode hold|double|toggle]
Without an exe path it runs Tiro from source.
"""

from __future__ import annotations

import argparse
import ctypes
import os
import subprocess
import sys
import time
import wave
from ctypes import wintypes
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
user32 = ctypes.WinDLL("user32", use_last_error=True)
user32.FindWindowW.restype = wintypes.HWND
user32.FindWindowExW.restype = wintypes.HWND
user32.FindWindowExW.argtypes = [wintypes.HWND, wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR]
user32.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.SendMessageW.restype = ctypes.c_ssize_t
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]

sys.path.insert(0, str(ROOT))
from tiro.injector import INPUT, INPUT_KEYBOARD, KEYBDINPUT, KEYEVENTF_EXTENDEDKEY, KEYEVENTF_KEYUP  # noqa: E402

VK_RCONTROL, VK_MENU = 0xA3, 0x12
WM_GETTEXT, WM_GETTEXTLENGTH, WM_SETTEXT, WM_CLOSE, EM_SETMODIFY = 0x000D, 0x000E, 0x000C, 0x0010, 0x00B9
LOG = Path(os.environ["LOCALAPPDATA"]) / "Tiro" / "logs" / "tiro.log"


def key(vk: int, up: bool) -> None:
    flags = (KEYEVENTF_KEYUP if up else 0) | (KEYEVENTF_EXTENDEDKEY if vk == VK_RCONTROL else 0)
    scan = user32.MapVirtualKeyW(vk, 0)
    inp = INPUT(type=INPUT_KEYBOARD, ki=KEYBDINPUT(vk, scan, flags, 0, 0))
    user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))


def tap(vk: int, hold: float = 0.08) -> None:
    key(vk, False)
    time.sleep(hold)
    key(vk, True)


def wait_log(marker: str, since: int, timeout: float = 60) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        if LOG.exists():
            with LOG.open(encoding="utf-8", errors="replace") as f:
                f.seek(since)
                if marker in f.read():
                    return True
        time.sleep(0.25)
    return False


def notepad_text(edit: int) -> str:
    n = user32.SendMessageW(edit, WM_GETTEXTLENGTH, 0, 0)
    buf = ctypes.create_unicode_buffer(n + 1)
    user32.SendMessageW(edit, WM_GETTEXT, n + 1, ctypes.addressof(buf))
    return buf.value


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("exe", nargs="?")
    ap.add_argument("--wav", default=str(ROOT / "tests" / "data" / "tts" / "notepad_test.wav"))
    ap.add_argument("--mode", default="hold", choices=["hold", "double", "toggle"])
    ap.add_argument("--insertion", default="type", choices=["type", "paste"])
    # F24 exists on no physical keyboard, so a test can never trigger a real Tiro the user is running
    ap.add_argument("--hotkey", default="f24", choices=["f24", "rctrl", "lshift"])
    ap.add_argument("--dictionary", default="", help="comma-separated dictionary entries for the test profile")
    ap.add_argument("--learn-test", default="", metavar="FIXED_TAIL",
                    help="after dictating, backspace over the text after 'playing ' and retype FIXED_TAIL")
    ap.add_argument("--chord", action="store_true", help="first type 'Hi ' with a Shift+H chord")
    ap.add_argument("--keep", action="store_true", help="leave Tiro running afterwards")
    args = ap.parse_args()

    with wave.open(args.wav) as w:
        duration = w.getnframes() / w.getframerate()
    profile = ROOT / "dev" / "e2e_profile"
    profile.mkdir(exist_ok=True)
    import json

    (profile / "settings.json").write_text(json.dumps({
        "hotkey": args.hotkey, "mode": "toggle" if args.mode == "toggle" else "hold", "double_tap_lock": True,
        "sounds": False, "welcome_shown": True, "insertion": args.insertion,
        "dictionary": [d.strip() for d in args.dictionary.split(",") if d.strip()],
    }))
    env = os.environ.copy()
    env["TIRO_TEST_WAV"] = args.wav
    env["TIRO_CONFIG_DIR"] = str(profile)
    env["TIRO_TEST_TARGET_CLASS"] = "Notepad"  # Tiro drops text if focus leaves the test window
    since = LOG.stat().st_size if LOG.exists() else 0
    if args.exe:
        cmd = [args.exe, "--verbose"]
    else:
        cmd = [str(ROOT / ".venv" / "Scripts" / "pythonw.exe"), str(ROOT / "run_tiro.pyw"), "--verbose"]
    t0 = time.time()
    tiro = subprocess.Popen(cmd, env=env, cwd=str(ROOT))
    if not wait_log("ASR ready", since, timeout=90):
        print("FAIL: Tiro did not report the model ready")
        return 1
    wait_log("language model", since, timeout=60)
    wait_log("correction knowledge", since, timeout=30)
    print(f"Tiro ready after {time.time() - t0:.1f}s")

    np_proc = subprocess.Popen(["notepad.exe"])
    hwnd = 0
    for _ in range(100):
        hwnd = user32.FindWindowW("Notepad", None)
        if hwnd:
            break
        time.sleep(0.1)
    edit = user32.FindWindowExW(hwnd, None, "Edit", None)
    tap(0xE8, 0.02)  # any input event grants foreground rights; 0xE8 is unassigned (Alt would open menus)
    user32.SetForegroundWindow(hwnd)
    time.sleep(0.6)
    if user32.GetForegroundWindow() != hwnd:
        print("WARN: Notepad is not in the foreground")

    foreign = set()

    def watch(seconds):
        end = time.time() + seconds
        while time.time() < end:
            fg = user32.GetForegroundWindow()
            if fg != hwnd:
                foreign.add(fg)
            time.sleep(0.02)

    from tiro import winutil

    sentinel = "CLIPBOARD-SENTINEL-42"
    winutil.set_clipboard_text(sentinel)
    hot = {"f24": 0x87, "rctrl": VK_RCONTROL, "lshift": 0xA0}[args.hotkey]

    if args.chord:
        if args.hotkey != "lshift":
            print("--chord needs --hotkey lshift (and no real Tiro using Left Shift may be running)")
            return 2
        key(0xA0, False)  # Shift down ... H ... Shift up, like typing a capital
        time.sleep(0.07)
        tap(0x48, 0.05)
        time.sleep(0.03)
        key(0xA0, True)
        time.sleep(0.05)
        tap(0x49, 0.05)
        tap(0x20, 0.05)
        time.sleep(0.8)
        print("after chord typing:", repr(notepad_text(edit)))

    t_press = time.time()
    if args.mode == "hold":
        key(hot, False)
        watch(duration + 0.6)
        key(hot, True)
    elif args.mode == "double":
        tap(hot, 0.08)
        time.sleep(0.12)
        tap(hot, 0.08)
        watch(duration + 0.4)
        tap(hot, 0.08)
    else:
        tap(hot, 0.1)
        watch(duration + 0.4)
        tap(hot, 0.1)
    if foreign:
        print(f"WARN: focus left Notepad during dictation: {[hex(h or 0) for h in foreign]}")
    t_release = time.time()
    last, stable_since = None, time.time()
    while time.time() - t_release < 6:
        text = notepad_text(edit)
        if text != last:
            last, stable_since = text, time.time()
        elif time.time() - stable_since > 1.0 and text:
            break
        time.sleep(0.05)
    print(f"final text settled {stable_since - t_release:.2f}s after release")
    print("NOTEPAD TEXT:\n" + repr(last))
    if args.learn_test and last and "playing " in last:
        tail = last.split("playing ", 1)[1]
        for _ in range(len(tail)):
            tap(0x08, 0.01)
            time.sleep(0.01)
        for ch in args.learn_test:
            if ch == " ":
                tap(0x20, 0.01)
            elif ch == ".":
                tap(0xBE, 0.01)
            elif ch.isupper():
                key(0xA0, False)
                tap(ord(ch), 0.01)
                key(0xA0, True)
            else:
                tap(ord(ch.upper()), 0.01)
            time.sleep(0.015)
        time.sleep(3.0)  # Tiro checks for a learnable correction after a short pause
        print("after correction:", repr(notepad_text(edit)))
        learned = json.loads((profile / "settings.json").read_text()).get("dictionary", [])
        print("dictionary now:", learned)
    time.sleep(0.8)
    snap = dict(winutil.clipboard_snapshot())
    clip = snap.get(13, b"").decode("utf-16-le", errors="replace").rstrip("\0")
    print("clipboard restored:" if clip == sentinel else "FAIL clipboard changed:", repr(clip))

    user32.SendMessageW(edit, WM_SETTEXT, 0, ctypes.addressof(ctypes.create_unicode_buffer("")))
    user32.SendMessageW(edit, EM_SETMODIFY, 0, 0)
    user32.SendMessageW(hwnd, WM_CLOSE, 0, 0)
    np_proc.wait(5)
    if not args.keep:
        subprocess.run(cmd[:-1] + ["--quit"], env=env, cwd=str(ROOT), timeout=20)
        try:
            tiro.wait(15)
            print("Tiro exited with", tiro.returncode)
        except subprocess.TimeoutExpired:
            print("FAIL: Tiro did not exit after --quit")
            tiro.kill()
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
