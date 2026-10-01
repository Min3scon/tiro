"""Run one DictationSession headless (WAV as microphone, recording injector) and print what it would type."""

import logging
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
logging.basicConfig(level=logging.DEBUG, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

from tiro.asr import ParakeetEngine  # noqa: E402
from tiro.audio import FileCapture  # noqa: E402
from tiro.context import InputContext  # noqa: E402
from tiro.session import DictationSession, SessionCallbacks  # noqa: E402
from tiro.textproc import FormatOptions  # noqa: E402
from tiro.vad import SileroVad  # noqa: E402


class RecordingInjector:
    def __init__(self):
        self.typed = []

    def insert(self, text):
        self.typed.append((round(time.monotonic() - T0, 2), text))
        return True


wav = sys.argv[1] if len(sys.argv) > 1 and sys.argv[1] != "-" else str(ROOT / "tests" / "data" / "tts" / "notepad_test.wav")
engine = ParakeetEngine(ROOT / "models" / "parakeet-tdt-0.6b-v2", device=sys.argv[2] if len(sys.argv) > 2 else "auto")
engine.load()
ready = threading.Event()
ready.set()
states = []
inj = RecordingInjector()
cb = SessionCallbacks(
    on_state=lambda s: states.append((round(time.monotonic() - T0, 2), s)),
    on_text=lambda c, p: None,
    on_notice=lambda k, m: print("NOTICE", k, m),
)
sess = DictationSession(
    engine=engine, engine_ready=ready, vad=SileroVad(ROOT / "models" / "silero-vad" / "silero_vad.onnx"),
    capture=FileCapture(wav), injector=inj, context=InputContext(), fmt=FormatOptions(), smart_spacing=True,
    mode="hold", hands_free_pause_sec=2.0, callbacks=cb,
)
T0 = time.monotonic()
sess.start()
import soundfile as sf  # noqa: E402

dur = sf.info(wav).duration
time.sleep(dur + 0.5)
sess.stop()
sess.join(10)
print("states:", states)
print("typed chunks:", inj.typed)
print("TEXT:", repr(sess.text))
