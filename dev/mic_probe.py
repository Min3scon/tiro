"""List WASAPI input devices and measure how long opening the default microphone takes."""

import time

import numpy as np
import sounddevice as sd

t = time.perf_counter()
apis = sd.query_hostapis()
print("init+query %.0f ms" % ((time.perf_counter() - t) * 1000))
for i, a in enumerate(apis):
    print(i, a["name"], "default_in=", a["default_input_device"])
wasapi = next(i for i, a in enumerate(apis) if "WASAPI" in a["name"])
for i, d in enumerate(sd.query_devices()):
    if d["max_input_channels"] > 0 and d["hostapi"] == wasapi:
        print("  WASAPI in:", i, d["name"], "ch", d["max_input_channels"], "sr", d["default_samplerate"])
dev = apis[wasapi]["default_input_device"]
info = sd.query_devices(dev)
print("default WASAPI input:", info["name"], info["default_samplerate"])

for ch in (1, info["max_input_channels"]):
    got = []

    def cb(indata, frames, t, status):
        got.append((time.perf_counter(), indata.shape, float(np.abs(indata).max())))

    t0 = time.perf_counter()
    try:
        s = sd.InputStream(device=dev, samplerate=info["default_samplerate"], channels=ch, dtype="float32",
                           blocksize=0, latency="low", callback=cb)
        s.start()
        t1 = time.perf_counter()
        time.sleep(0.6)
        s.stop()
        s.close()
        print(f"channels={ch}: open+start {(t1 - t0) * 1000:.0f} ms, first callback after "
              f"{(got[0][0] - t0) * 1000:.0f} ms, blocks={len(got)}, shape={got[0][1]}, "
              f"peak={max(g[2] for g in got):.4f}")
    except Exception as e:
        print("channels", ch, "failed:", e)

t = time.perf_counter()
sd._terminate()
sd._initialize()
print("reinit %.0f ms" % ((time.perf_counter() - t) * 1000))
