import sys, time, wave, numpy as np
sys.path.insert(0, r"D:\dictation")
from tiro.asr import ParakeetEngine
w = wave.open(r"D:\dictation\assets\sounds\selftest.wav"); a = np.frombuffer(w.readframes(w.getnframes()), "<i2").astype(np.float32)/32768
for dev in ("cpu", "cuda"):
    e = ParakeetEngine(r"D:\dictation\dev\int8_only\parakeet-tdt-0.6b-v2", device=dev); e.load()
    t = time.perf_counter(); txt = " ".join(x.text for x in e.transcribe(a)); ms = (time.perf_counter()-t)*1000
    print(f"requested={dev} -> device={e.device} variant={e.variant} {ms:.0f} ms fallback={e.fallback_reason!r}\n  {txt}")
