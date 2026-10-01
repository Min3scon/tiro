"""A console program that receives typed text and shows how much arrived in its window title.

The latency harness reads the title (TIRO-TEST-TERM <chars>) to time when the last character landed.
Ctrl+C or the window closing ends it; the received text is written to the file given as argv[1].
"""
import ctypes
import msvcrt
import sys
import time

kernel32 = ctypes.WinDLL("kernel32")
out = sys.argv[1] if len(sys.argv) > 1 else None
buf = []
kernel32.SetConsoleTitleW("TIRO-TEST-TERM 0")
print("Tiro latency test: this window receives dictated text. Do not type here.")
try:
    while True:
        if msvcrt.kbhit():
            while msvcrt.kbhit():
                ch = msvcrt.getwch()
                if ch == "\x03":
                    raise KeyboardInterrupt
                buf.append(ch)
                sys.stdout.write(ch)
            sys.stdout.flush()
            kernel32.SetConsoleTitleW(f"TIRO-TEST-TERM {len(buf)}")
        else:
            time.sleep(0.002)
except KeyboardInterrupt:
    pass
finally:
    if out:
        with open(out, "w", encoding="utf-8") as f:
            f.write("".join(buf))
