"""Attach to another process's console and print its visible text whenever it changes.

    python console_peek.py <pid> [interval_ms]
Each change prints one JSON line: {"t": epoch seconds, "text": "<visible screen, rows joined by \\n>"}.
Used to time when dictated text shows up inside a terminal app (e.g. Claude Code's input box).
"""
import ctypes
import json
import sys
import time
from ctypes import wintypes

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)


class COORD(ctypes.Structure):
    _fields_ = [("X", wintypes.SHORT), ("Y", wintypes.SHORT)]


class SMALL_RECT(ctypes.Structure):
    _fields_ = [("Left", wintypes.SHORT), ("Top", wintypes.SHORT), ("Right", wintypes.SHORT),
                ("Bottom", wintypes.SHORT)]


class CSBI(ctypes.Structure):
    _fields_ = [("dwSize", COORD), ("dwCursorPosition", COORD), ("wAttributes", wintypes.WORD),
                ("srWindow", SMALL_RECT), ("dwMaximumWindowSize", COORD)]


def main() -> None:
    pid = int(sys.argv[1])
    interval = float(sys.argv[2]) / 1000 if len(sys.argv) > 2 else 0.01
    out = sys.stdout  # keep our own stdout (a pipe) before switching consoles
    kernel32.FreeConsole()
    if not kernel32.AttachConsole(pid):
        out.write(json.dumps({"error": f"AttachConsole failed: {ctypes.get_last_error()}"}) + "\n")
        out.flush()
        return
    h = kernel32.CreateFileW("CONOUT$", 0xC0000000, 3, None, 3, 0, None)
    last = None
    while True:
        info = CSBI()
        if not kernel32.GetConsoleScreenBufferInfo(h, ctypes.byref(info)):
            break
        w = info.srWindow
        width = w.Right - w.Left + 1
        rows = []
        for y in range(w.Top, w.Bottom + 1):
            buf = ctypes.create_unicode_buffer(width)
            n = wintypes.DWORD()
            kernel32.ReadConsoleOutputCharacterW(h, buf, width, COORD(w.Left, y), ctypes.byref(n))
            rows.append(buf.value[: n.value].rstrip())
        text = "\n".join(rows)
        if text != last:
            last = text
            out.write(json.dumps({"t": time.time(), "text": text}) + "\n")
            out.flush()
        time.sleep(interval)


if __name__ == "__main__":
    main()
