"""Launch Tiro.exe without inherited std handles and with CWD=System32 (like Explorer), check its log."""

import os
import subprocess
import time
from pathlib import Path

import psutil

EXE = r"D:\dictation\dist\Tiro\Tiro.exe"
LOG = Path(os.path.expandvars(r"%LOCALAPPDATA%\Tiro\logs\tiro.log"))
PROFILE = r"D:\dictation\dev\probe_profile"


def tiros():
    return [p for p in psutil.process_iter(["name", "environ"]) if (p.info["name"] or "").lower() == "tiro.exe"]


env = dict(os.environ, TIRO_CONFIG_DIR=PROFILE)
before = LOG.stat().st_size
DETACHED_PROCESS = 0x00000008
p = subprocess.Popen([EXE], env=env, cwd=r"C:\Windows\System32", stdin=subprocess.DEVNULL,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True,
                     creationflags=DETACHED_PROCESS)
t0 = time.time()
seen = ""
while time.time() - t0 < 25:
    seen = LOG.read_bytes()[before:].decode("utf-8", "replace")
    if "ASR ready" in seen:
        break
    time.sleep(0.5)
print(f"[DEVNULL handles] after {time.time() - t0:.1f}s log shows: {seen!r}")
subprocess.run([EXE, "--quit"], env=env, timeout=30)
p.wait(15)

# now with truly no std handles: STARTUPINFO without STARTF_USESTDHANDLES
before = LOG.stat().st_size
si = subprocess.STARTUPINFO()
si.dwFlags = 0
p = subprocess.Popen([EXE], env=env, cwd=r"C:\Windows\System32", close_fds=True, startupinfo=si,
                     creationflags=DETACHED_PROCESS)
t0 = time.time()
while time.time() - t0 < 25:
    seen = LOG.read_bytes()[before:].decode("utf-8", "replace")
    if "ASR ready" in seen:
        break
    time.sleep(0.5)
print(f"[no std handles] after {time.time() - t0:.1f}s log shows: {seen!r}")
subprocess.run([EXE, "--quit"], env=env, timeout=30)
p.wait(15)
