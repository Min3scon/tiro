"""Quit any running Tiro, launch Tiro.exe the way a double-click does (via Explorer), check it logs and loads."""

import os
import subprocess
import time
from pathlib import Path

import psutil

EXE = r"D:\dictation\dist\Tiro\Tiro.exe"
LOG = Path(os.path.expandvars(r"%LOCALAPPDATA%\Tiro\logs\tiro.log"))


def tiros():
    return [p for p in psutil.process_iter(["name"]) if (p.info["name"] or "").lower() == "tiro.exe"]


if tiros():
    subprocess.run([EXE, "--quit"], timeout=30)
    for _ in range(40):
        if not tiros():
            break
        time.sleep(0.25)
print("running before launch:", [p.pid for p in tiros()])
before = LOG.stat().st_size
subprocess.run(["explorer.exe", EXE])
t0 = time.time()
seen = ""
while time.time() - t0 < 40:
    seen = LOG.read_bytes()[before:].decode("utf-8", "replace")
    if "ASR ready" in seen or "Could not load" in seen:
        break
    time.sleep(0.5)
print(f"after {time.time() - t0:.1f}s the log shows:\n{seen}")
for p in tiros():
    print("pid", p.pid, "parent", p.ppid(), "cpu%", p.cpu_percent(interval=5.0), "mem MB", p.memory_info().rss // 2**20)
