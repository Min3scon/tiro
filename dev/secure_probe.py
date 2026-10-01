import subprocess, sys, time
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tiro import winutil
from tiro.secure import is_secure_field
p = subprocess.Popen([sys.executable, str(ROOT / "dev" / "password_window.py")])
t0 = time.time()
while p.poll() is None and time.time() - t0 < 8:
    fg = winutil.foreground_window()
    t1 = time.perf_counter()
    r = is_secure_field(fg)
    print(f"{time.time()-t0:4.1f}s fg={winutil.window_class(fg)!r:40s} secure={r} ({(time.perf_counter()-t1)*1000:.0f} ms)")
    time.sleep(0.5)
