"""Does loading a model starve the keyboard hook? (Python hook callbacks need Python's lock.)

A separate helper process (dev/latency/hook_observer.py) taps F23 every 100 ms and records any tap the hook under
test failed to swallow in time. Meanwhile this process loads a large ONNX model. Reported: how late the hook
under test handled each tap (Python hook) and how many taps went through unswallowed.

    python dev/hook_gil_probe.py [--native] [--model path.onnx] [--seconds 14]
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

F23 = 0x86


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=str(ROOT / "models" / "parakeet-tdt-0.6b-v2" / "encoder-model.int8.onnx"))
    ap.add_argument("--seconds", type=float, default=14.0)
    ap.add_argument("--load-at", type=float, default=3.0)
    ap.add_argument("--native", action="store_true")
    a = ap.parse_args()

    tmp = Path(tempfile.mkdtemp(prefix="tiro-hook-probe-"))
    out, go, stop = tmp / "observer.json", tmp / "go", tmp / "stop"
    observer = subprocess.Popen([sys.executable, str(ROOT / "dev" / "latency" / "hook_observer.py"), str(out),
                                 str(go), str(stop)], stdout=subprocess.PIPE, text=True)
    observer.stdout.readline()  # its hook is in place before ours, so Windows calls it after ours

    handled: list[float] = []  # time.time() when the hook under test handled a press (Python hook only)

    def handler(vk, down, scan, flags, injected):
        if vk == F23:
            if down:
                handled.append(time.time())
            return True
        return False

    if a.native:
        from tiro.keys import HotkeySpec
        from tiro.platform.windows.native_hook import NativeHotkeys

        hook = NativeHotkeys(HotkeySpec.parse("f23"), None, mode="hold", double_tap_lock=False,
                             on_action=lambda act: None, on_fix=lambda: None, on_typed=lambda *x: None)
    else:
        from tiro.platform.windows.hook import KeyboardHook

        hook = KeyboardHook(handler, can_reinstall=lambda: False)  # no periodic re-install: see the raw effect
    hook.start()
    go.write_text("go")
    t0 = time.time()
    time.sleep(a.load_at)
    import onnxruntime as ort

    so = ort.SessionOptions()
    so.log_severity_level = 3
    load0 = time.time()
    sess = ort.InferenceSession(a.model, sess_options=so, providers=["CPUExecutionProvider"])
    load1 = time.time()
    time.sleep(max(0.0, a.seconds - (load1 - t0)))
    stop.write_text("stop")
    observer.wait(15)
    hook.stop()
    del sess
    data = json.loads(out.read_text())
    sent, leaked = data["sent"], data["leaked"]
    delays = []
    if not a.native:
        j = 0
        for s in sent:
            while j < len(handled) and handled[j] < s - 0.001:
                j += 1
            if j < len(handled):
                delays.append((handled[j] - s) * 1000)
                j += 1
    during = [s for s in sent if load0 <= s <= load1]
    result = {
        "hook": "native" if a.native else "python",
        "model_load_s": round(load1 - load0, 2),
        "taps_sent": len(sent),
        "taps_sent_during_load": len(during),
        "not_swallowed_in_time": len(leaked),
        "not_swallowed_during_load": sum(1 for t in leaked if load0 <= t <= load1 + 0.5),
    }
    if delays:
        result["handled_late_ms_max"] = round(max(delays), 1)
        result["handled_late_ms_median"] = round(sorted(delays)[len(delays) // 2], 2)
    print(json.dumps(result, indent=1))


if __name__ == "__main__":
    main()
