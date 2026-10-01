"""Start an isolated frozen Tiro (separate profile, so it can't talk to a running instance) and check it logs."""

import os
import subprocess
import time
from pathlib import Path

EXE = r"D:\dictation\dist\Tiro\Tiro.exe"
LOG = Path(os.environ["LOCALAPPDATA"]) / "Tiro" / "logs" / "tiro.log"
env = dict(os.environ, TIRO_CONFIG_DIR=r"D:\dictation\dev\probe_profile")
before = LOG.stat().st_size if LOG.exists() else 0
p = subprocess.Popen([EXE], env=env)
time.sleep(12)
data = LOG.read_bytes()
print("log grew by", len(data) - before, "bytes")
print(data[before:].decode("utf-8", "replace"))
subprocess.run([EXE, "--quit"], env=env, timeout=30)
try:
    p.wait(15)
    print("probe instance exited:", p.returncode)
except subprocess.TimeoutExpired:
    print("probe instance did not exit")
