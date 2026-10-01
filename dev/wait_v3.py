"""Wait for the running instance's v3 download to finish and report what it logs."""

import os
import subprocess
import time
from pathlib import Path

d = Path(os.path.expandvars(r"%LOCALAPPDATA%\Tiro\models\parakeet-tdt-0.6b-v3"))
log = Path(os.path.expandvars(r"%LOCALAPPDATA%\Tiro\logs\tiro.log"))
end = time.time() + 180
while time.time() < end and (d / "encoder-model.onnx.data.part").exists():
    time.sleep(1)
time.sleep(15)  # give it time to load the model
print(sorted((p.name, p.stat().st_size // 2**20) for p in d.iterdir()))
print(log.read_bytes().decode("utf-8", "replace")[-500:])
out = subprocess.run([r"D:\dictation\.venv\Scripts\py-spy.exe", "dump", "--pid", "11780"], capture_output=True, text=True)
print("\n".join(line for line in out.stdout.splitlines() if "Thread" in line or "tiro" in line))
